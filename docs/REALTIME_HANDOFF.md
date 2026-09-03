# Realtime / event-driven work — handoff

Status as of 2026-09-03. Successor to `docs/PERFORMANCE_HANDOFF.md`, which covers
the round before this one and is still worth reading first for *why* the version
counters exist.

Everything here is committed to the working tree but **nothing is pushed and
nothing is deployed**. Read "State" before assuming any of it is live.

---

## The one idea to hold onto

The previous round made polling *cheap*. This round made the system *event-driven*
— but deliberately did **not** delete polling. The rule everywhere is:

> **Realtime is an accelerator over a polling floor. Never a replacement.**

Every feature has a fallback path that works with the socket switched off, and the
fallback is exercised by turning `REALTIME_ENABLED=false`. If you find a feature
that only works with the socket up, that is a bug, not a design.

The reason this rule is written in capital letters: a socket that is **open but
silently not delivering** is a failure the client cannot detect on its own. The
slow poll underneath is the only thing that would ever notice.

### Frames carry no data

A frame is `{"scope": "company:calls", "version": 41}` and nothing more. The client
responds by refetching through the ordinary authenticated REST endpoint.

This is the load-bearing security decision. Groups are company-wide, but visibility
*inside* a company is not — WhatsApp chat/call access is per-user, lead access is
per-assignee. If frames carried payloads, every ACL rule would be reimplemented on
the socket, and the failure mode is one colleague seeing another's leads. Sending a
version number means **group membership is the entire authorization surface**.

The cost of this choice is that **every change produces one HTTP request**. What
makes that acceptable is not removing the request but making it a `304` with zero
database work. If a future round wants to remove those requests, that means frames
with filtered payloads — discuss the ACL risk before building it.

---

## What exists now

Three layers, each usable without the ones after it.

### 1. The digest as a change feed
`sync/version.py` splits the old single company counter into named slices —
`chat`, `calls`, `arrivals`, `tenant_chat` — reported in the digest body as
`versions`. Clients watch the slice they care about instead of running a timer per
view.

`bump_company_slice()` moves **both** the slice and the coarse ETag counter. Never
call `bump_company` directly; moving the ETag without the slice makes the digest
rebuild while telling clients nothing changed.

### 2. Conditional GETs
`sync/conditional.py` — `conditional_token()` / `not_modified()` / `tag()`. Applied
to the WhatsApp conversation list, calls list/live/pending, and the tenant-chat
conversation list. Every token folds in **the counters, the user id, and a hash of
the request params**; leaving any of the three out is a correctness bug, not a
missed optimisation (see the module docstring).

### 3. The WebSocket
`realtime/` — a separate uvicorn process (`deploy/crm-realtime.service`). Gunicorn
is untouched and still serves the API from `wsgi.py`.

Server → client: version frames, and presence frames.
Client → server: `subscribe` / `unsubscribe` / `presence` / `heartbeat`.

Client writes have their own authorization in `realtime/presence.py`. Two rules:
a connection may only address a conversation it was **admitted to** on `subscribe`
(re-checked in the DB against `user_participates_in_conversation`, the same
predicate the REST API uses), and **the sender's identity comes from the
connection, never the frame**.

### Online presence
`accounts/presence.py` is the single reader. Online = **live socket OR
`last_seen_at` within 90s**. The `OR` is what keeps mobile-without-socket and
realtime-disabled browsers working unchanged. `last_seen_at` is still written but
throttled to once per 5 minutes per user.

Use `live_user_ids()` (one `get_many`) for lists and pass the set into serializer
context as `online_user_ids` — a naive per-row cache read is 100 Redis round trips
on a user list.

---

## Traps I fell into. Do not repeat these.

These cost real time and, in two cases, shipped broken behaviour the user found by
hand. They are listed because each one is easy to hit again.

**`queryClient.getQueryData()` is not reactive.** It returns a snapshot and
registers no subscription. `useSliceVersion` used it, so components never
re-rendered when the digest changed — the Team Chat badge (which subscribes
properly) counted new messages while the open thread never refetched. Use
`useSyncDigest({ enabled: false, refetchInterval: false })`: `enabled: false` still
subscribes to the cache, it only stops the hook putting a request on the wire.

**`CRM-project` has a global `staleTime: 60_000`** (`index.tsx`). Removing an
interval can therefore stop a remount refetching *at all* — reopening the chat
dialog served minute-old cache. Set `staleTime: 0` on anything that must be fresh
when reopened.

**`async_to_sync` inside an ASGI request tears down the DB connection.** The first
version of `realtime/publish.py` called it inline; under ASGI it blocks the worker
thread waiting on the loop and the next ORM call dies with "Cannot operate on a
closed database". Publishing is now fire-and-forget — scheduled onto the running
loop when there is one, `async_to_sync` only when there isn't (gunicorn, cron,
qcluster).

**Bulk updates fire no signals.** WhatsApp mark-read is a `queryset.update()`, so
`post_save` never runs and no counter moved — the conversation list would 304 with
stale unread counts. That view now bumps explicitly. Publishing hooks the *counter
bump* rather than model signals precisely so cases like this are covered.

**`behavior: 'smooth'` is not a way to reach the bottom of a chat.** It animates
toward an offset captured when the animation starts, so any content that grows
during it — blob media resolving, a refetch landing — leaves you short, and a
second smooth scroll cancels the first outright. Both chat threads now use
instant `scrollTop` assignment only, and reserve smooth for an explicit
"jump to latest" button. Related: a scroll effect keyed on `messages.length`
never re-runs when *heights* change, so the correction has to come from a
ResizeObserver on the transcript column, not from the render path.

**`Timer.periodic` captures its duration once** (Dart). A screen that mounts before
the socket connects keeps the short interval until it remounts. Left as-is on
purpose — being wrong toward *more* polling is the safe direction.

**Don't add `channels` to `INSTALLED_APPS`.** It replaces `manage.py runserver`
with the Channels ASGI server and silently changes how everyone runs the app
locally. Nothing needs it; production invokes uvicorn against `asgi.py` directly.

**Don't install `daphne`.** `channels.testing` imports it, and it drags in Twisted
*and force-upgrades the pinned `cryptography`* that signs payment and auth tokens.
`tests/test_realtime.py` has a ~20-line communicator built on `asgiref` instead.

---

## Environment gotchas that will waste your time

**Local has no Redis and no Postgres** (SQLite, `DB_ENGINE=sqlite3`). Consequences:

- The channel layer falls back to **in-memory, which does not cross processes**.
  The API and the socket must be one process locally: run
  `python -m uvicorn crm_saas_api.asgi:application --port 8000`, not `runserver`.
  With a local Redis you can run the production split instead.
- The cache is per-process too, so a verification script in another process cannot
  see what the server cached. Two "failures" during this work were this, not bugs.
- django-q falls back to the **ORM broker**, which writes to SQLite. That is what
  made group-chat sends 500 (a queue write inside an open `.iterator()` cursor).

**`PUSH_QUEUE_ENABLED=true` in the local `.env` fails 3 tests.** They assert the
committed default is off. Run the suite with it `false`; 1026 pass.

**Vite reads `.env` once at startup.** Editing `VITE_REALTIME_ENABLED` and
reloading the browser does nothing — restart `npm run dev`. The client logs
`[realtime] disabled — VITE_REALTIME_ENABLED is null …` when this happens.

**`X-API-Key` is required** on every `/api/` route. Manual curl/Postman testing
401s without it; the apps send it automatically.

**An `OPTIONS` before every request is probably DevTools.** `CORS_PREFLIGHT_MAX_AGE`
is already 3600. Chrome disables the preflight cache when DevTools has "Disable
cache" ticked, which doubles the apparent request count in the server log. Confirm
with DevTools closed before "fixing" the CORS config.

---

## State: what is verified and what is not

**Verified**
- Backend suite **1027 passed** (`PUSH_QUEUE_ENABLED=false`, ~27 min).
  `tests/test_realtime.py` alone is 24, covering auth rejection, cross-tenant
  isolation, the presence authorization boundary, spoofed `user_id`, rate limiting,
  fail-soft on a dead channel layer, and that a publish never blocks a write.
- `CRM-project`: build clean, TypeScript at its **pre-existing 41-error baseline**
  (treat 42+ as a regression; none are in touched files).
- `crm_mobile`: `flutter analyze` clean.
- Socket end-to-end against a live server, from both Python and the Dart VM:
  unauthenticated rejected, garbage token rejected, valid token accepted, a real
  API write delivered `{"scope":"user","version":1}`, presence delivered in <1ms,
  spoofed `user_id` ignored, outsider blocked, unsubscribed sender blocked.
- Mobile conditional digest: `200` then `304`, `304` against the real server.

**Not verified — do not claim these work**
- **Web push.** `services/webPush.ts` + `public/firebase-messaging-sw.js` are
  complete but need Firebase web credentials that do not exist yet (six
  `VITE_FIREBASE_*` values plus `VITE_FIREBASE_VAPID_KEY`, documented in
  `.env.example`). It no-ops silently until they are filled in.
- **Mobile socket inside the running app.** The protocol and URL derivation were
  verified from the Dart VM; nobody has watched it in a real `flutter run` session.
- **The ringing-call push**, which needs a real inbound WhatsApp call.
- **Load.** Everything here is a measured *reduction*; there is still no measured
  ceiling. The idle pattern changed, so measure both socket-on and socket-off.

**Open bug — diagnosed and fixed 2026-09-03, needs hand-verification**

The surface is the **WhatsApp Chats thread** (`components/whatsapp/ChatThread.tsx`),
not Team Chat. It was *both* answers to the "first unread or arbitrary" question,
from two independent causes:

1. **First unread, presented badly.** With unread messages the thread deliberately
   scrolled the "New Messages" divider to `block: 'center'`, leaving the newest
   message halfway up the viewport with dead space below — visually identical to
   a scroll that stopped partway. Now anchored near the *top* of the viewport,
   which is what the divider is for.
2. **Arbitrary, and this is the real bug.** Both scrolls used
   `behavior: 'smooth'`, which animates toward an offset captured when it starts.
   `ChatBlobMedia` fetches every image and video into a blob URL, so media bubbles
   render as a one-line `…` placeholder and only grow to real height a few hundred
   ms later. The animation therefore finished at what *had been* the bottom, and
   the effect's deps (`messages.length`, `threadItems.length`) do not change when
   heights do, so nothing corrected it. `ChatMessageBubble` never passed
   `onIntrinsicLayout` down, and there was no ResizeObserver — Team Chat has both,
   which is why it does not show this.

The rewrite makes the opening position a **standing anchor** rather than a
one-shot scroll: instant jumps only, re-applied from a ResizeObserver on the
scroll container *and* the inner transcript column, released when the user
scrolls or when a message arrives after a 1.5s settle window. Same shape as
`TeamChatPage`.

Discriminating hand-test, if you want to confirm the diagnosis on the old code:
open a media-heavy thread with everything already read (lands arbitrarily short of
the bottom, worse the more media) versus a text-only thread with unread (lands
exactly on the divider, centred).

Also fixed while in there: `TeamChatPage`'s tail refs were reset in a *passive*
effect, which runs after the layout effect that positions the thread — so
switching to a conversation with cached messages positioned it using the previous
thread's pinned/tail state. Narrow (needs you to have scrolled up in the thread
you are leaving), but the same class of bug.

---

## Deploy order

Both halves default **off**, so this ships dark and can be enabled in either order.
Full detail in `docs/REALTIME.md`; the short version:

1. `pip install -r requirements.txt` (channels, channels-redis, uvicorn)
2. `manage.py migrate` — three migrations, one of them a data backfill
3. Confirm `redis-cli config get maxmemory-policy` is **noeviction**. Not optional:
   an evicted group registration silently drops a subscription, and the symptom is
   "realtime works for some people", not an error.
4. `systemctl enable --now crm-realtime`
5. nginx: paste `deploy/nginx-realtime.conf.snippet` (note `access_log off` — the
   JWT is in the query string)
6. `REALTIME_ENABLED=true` in `.env`, restart `crm-api`
7. `VITE_REALTIME_ENABLED=true` and redeploy the frontend

Server before client: with the flag on and no clients nothing is wasted; with
clients and no server they retry against a route that does not exist.

**The acceptance gate is step 8:** `systemctl stop crm-realtime` and re-run every
realtime check. Everything must still work, just slower. That single test matters
more than the rest.

---

## Open work, in priority order

1. **Load test.** Biggest gap, unchanged from the previous handoff.
2. ~~**The scroll-position bug.**~~ Fixed — see the section above. Still wants a
   hand-test on a media-heavy thread and on one with unread.
3. ~~**Fold mobile's `notifications/unread_count` into the digest.**~~ **Done.**
   `NotificationsUnreadHolder` joins the two existing holders;
   `WhatsAppChatUnreadPoller` writes it from the digest's `notifications_unread`,
   ahead of the WhatsApp branch that returns early for users without chat access.
   Both home screens are pure `ValueListenableBuilder`s now and make no request of
   their own, and `NotificationsScreen` writes its count through on read/mark-all
   so the badge behind it is already right on pop — the reload-on-return is gone.
   The screen itself still calls the endpoint, which is the one place that is the
   natural source.

   This uncovered a **missing bump**: `notifications/delete_all` is a
   `queryset.update()` that soft-deletes *unread* rows, so `notifications_unread`
   changed with no `post_save` and no bump — the digest kept serving the cached
   count under an unchanged token. Invisible while the bell read the endpoint
   directly; a stuck badge the moment it reads the digest. `mark_all_read` was
   already covered, `delete_all_read` only touches read rows, and
   `_soft_delete_unread_pbx_incoming` is always followed by a create for the same
   users, which bumps. `tests/test_sync_digest.py::test_delete_all_invalidates_badge_cache`
   pins it — verified failing (`assert 1 == 0`) with the bump removed.
4. **A dedicated mobile notification channel for ringing calls.** The push
   currently borrows the `arrival` channel to get an insistent ring. Behaviour is
   right, tone is borrowed; a real `call` channel needs a mobile release with a
   bundled sound.
5. **`install_qcluster_schedules`** is written but not run in production. It moves
   the two per-minute cron jobs into the warm qcluster; it requires commenting out
   the matching `crontab` lines or each job runs twice a minute.
6. **`/var/log/crm-api-lead-no-follow-up.log` >20MB** — inherited from the previous
   handoff, still unexamined.

---

## Files

Backend (`CRM-api-1`): `realtime/` (new app), `sync/version.py`,
`sync/signals.py`, `sync/views.py`, `sync/conditional.py` (new),
`accounts/presence.py` (new), `accounts/models.py` (`UserDevice`),
`integrations/services/whatsapp_call_push.py` (new), `crm_saas_api/asgi.py`,
`crm_saas_api/settings.py`, plus `deploy/` units and `docs/REALTIME.md`.

Web (`CRM-project`): `hooks/useRealtimeChannel.ts`, `hooks/useSliceVersion.ts`,
`hooks/useConversationPresence.ts`, `hooks/useWebPush.ts`, `services/webPush.ts`,
`public/firebase-messaging-sw.js` (all new), plus the chat/calls pages,
`components/whatsapp/ChatThread.tsx` (scroll anchoring) and `services/api.ts`.

Mobile (`crm_mobile`): `lib/services/realtime_channel.dart`,
`lib/services/notifications_unread_holder.dart` (both new),
`whatsapp_chat_unread_poller.dart`, `sync_invalidation.dart`, `api_service.dart`,
`main.dart`, the two home screens, `screens/notifications/notifications_screen.dart`,
and the team-chat presence cubit/repository.

> **Not part of this work:** the uncommitted RTL text-direction changes in
> `crm_mobile` — `lib/screens/team_chat/team_chat_text_direction.dart`,
> `team_chat_message_bubble.dart`, `team_chat_composer.dart`,
> `team_chat_conversation_tile.dart`, `widgets/team_chat_composer_section.dart`,
> and `test/features/team_chat/team_chat_bubble_layout_test.dart`. Those are the
> user's own in-flight changes. Leave them alone.

A step-by-step manual test plan (20 tests, grouped, with expected results and
failure signatures) was delivered to the user as a private artifact. Ask them for
the link rather than rewriting it.
