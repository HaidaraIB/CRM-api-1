# Polling / load-reduction work — handoff

Status as of 2026-08-31. Everything described here is **committed and tested**;
deployment state is tracked in the checklist near the end.

> **Superseded in part, 2026-09-01.** A follow-up round took this from "make the
> poll cheap" to "stop polling". The document below is still accurate about *why*
> the counters exist and how they work — read it first — but three things have
> changed since:
>
> * **The digest is now a change feed.** It reports a counter per slice
>   (`chat`, `calls`, `arrivals`, `tenant_chat`, `user`, `global`), and clients
>   watch their slice instead of running a timer per view. The single company
>   counter was split; `bump_company_slice` moves both the slice and the coarse
>   ETag counter so they cannot drift.
> * **Conditional GETs are no longer just the digest.** The WhatsApp conversation
>   list, calls list/live/pending and the tenant-chat conversation list all send
>   ETags now, via `sync/conditional.py`.
> * **The "no WebSockets" decision was revisited and reversed** — see
>   `docs/REALTIME.md`. The reasoning below was right about SSE *inside gunicorn*;
>   a separate uvicorn process does not touch the gthread pool at all. Gunicorn's
>   configuration is unchanged.
>
> The deployment checklist near the end has been **run and is closed** except for
> one item. `docs/REALTIME.md` carries the deploy order for the new work.

## Why this work happened

LOOP CRM does all of its "real-time" over HTTP polling. There are no websockets
anywhere — `channels` is not installed, `asgi.py` is stock, and `WSGI_APPLICATION`
is what gunicorn serves. Production is a **shared** 2 vCPU / 8 GB VPS that also
runs a second Django app, Postgres, Redis and nginx, with gunicorn sized at
**2 workers × 4 threads = 8 concurrent request slots** (`gunicorn_config.py`).

The concern was that polling overhead would take the box down at launch. The
measured baseline was ~912 req/hour per idle browser tab, of which ~720 were
`/sync/digest/` at 5s — and each of those cost ~8-9 SQL queries to answer
"nothing changed".

**The diagnosis was that the request count was not the problem; the per-request
cost was.** 13 req/s against 8 slots is fine. 13 req/s each doing 9 queries is
not. So the work made the steady-state poll nearly free rather than replacing the
transport.

**Explicit decision: no WebSockets / Channels / ASGI / SSE before launch.** SSE is
actively worse here — under `gthread` WSGI every open stream pins one of only 8
request slots. Revisit real-time transport when sustained concurrency passes
roughly 400 tabs.

## The core mechanism — read this before touching anything

`sync/version.py` holds counters in the Django cache (Redis in prod). Every write
path that can change a polled value bumps one; endpoints fold the current values
into an opaque token and hand it back as an `ETag`. A client that returns that
token in `If-None-Match` gets a bare `304` **without touching the database**.

Scopes mirror how data is shared:

| Scope | Covers |
|---|---|
| global | news posts (platform-wide) |
| company | chats, calls, arrivals — anything a teammate can change for you |
| user | notifications, read cursors |
| conversation | one chat thread's messages and read receipts |

Two invariants that are easy to break:

1. **Bumps live in `sync/signals.py`, on model signals — not in views.** This is
   deliberate: a management command, webhook, admin action or data migration that
   writes these models is covered automatically. If you add a model whose data
   appears in a polled response, add a receiver there.
2. **Every token carries a 30s time bucket** (`SAFETY_BUCKET_SECONDS`). It is the
   safety net, not the mechanism. A missed bump degrades to *up to 30s of
   staleness*, never a permanently stuck client. This matches the freshness the
   badge tier always had, so it is not a regression — but it also means **a
   missing bump shows up as "the badge feels sluggish", not as a test failure.**
   That is the failure mode to suspect when something lags ~30s.

Tokens are prefixed with the user id (and conversation id) so two users can never
collide on a token — otherwise a tab that kept one across a logout could be 304'd
against the previous user's counts.

## What changed

### Backend (`CRM-api-1`)

| Area | Change |
|---|---|
| `sync/version.py` *(new)* | Counters, token builders, `normalize_etag`. |
| `sync/signals.py` *(new)* | All bumps, on model signals. Wired via `sync/apps.py` `ready()`. |
| `sync/views.py` | Token compared **before** `build_digest` → 304 costs 0 SQL. Previously the digest was fully built and then discarded, so the 304 saved bytes but not one query — and no client ever sent `If-None-Match`, so it was unreachable in production anyway. |
| `sync/cache.py` | Badge cache key now embeds the token, so a company-wide event expires every affected user's entry at once without enumerating them. |
| `tenant_chat/views.py` | `messages` GET supports conditional requests (304). `list()` overridden to resolve per-row lookups in bulk. Group `peer_presence` uses one `get_many` instead of up to 100 sequential cache reads. |
| `tenant_chat/serializers.py` | `bulk_conversation_context()` — collapses 4-6 queries **per conversation row** into ~5 total. Note `get_last_message` previously looked for a `last_message_prefetched` attribute that **nothing in the repo ever set**, so its fallback always fired. |
| `crm/dashboard_summary.py` | `_latest_activity_maps` no longer streams all tasks/calls/visits into Python. One correlated `Subquery` per activity kind. Written as `Subquery` not `DISTINCT ON` because that is Postgres-only and dev runs SQLite. |
| `integrations/policy.py` | `get_plan_integration_access` cached 60s, invalidated via the existing `invalidate_company_subscription_cache` seam (already hooked to `Subscription` post_save). |
| `settings/models.py` | `SystemSettings.get_cached_settings()` for hot paths — `get_settings()` was a `get_or_create` on every call. **Writers must keep using `get_settings()`**; the cached one returns a deserialized copy. |
| `integrations/views/twilio_sms.py` | `select_related('created_by')` — was an N+1 per outbound message. |
| `crm_saas_api/settings.py` | django-q broker fix (below), `DRF_THROTTLE_USER` 600→300, `WatchedFileHandler`. |
| `deploy/crm-api.logrotate` *(new)* | Log rotation. |

### Frontend (`CRM-project`)

- `services/api.ts` — `apiRequest` handles `304` (throws `code: 'NOT_MODIFIED'`) and
  can surface the `ETag` via an optional `EtagSink`. `getSyncDigestAPI` and
  `getTenantChatMessagesAPI` send `If-None-Match` and replay their cached payload
  on a 304. `resetConditionalRequestCaches()` clears both, called from
  `AppContext` logout.
- Intervals: team-chat messages 1.5s→3s, presence 1.2s→2.5s, conversations 8s→10s,
  WhatsApp conversations 3s→6s, WhatsApp thread 5s→6s.
- `CallsPage` live-calls poll was a **flat 2s whenever the page was open**
  (~1,800 req/hr idle). Now 8s idle, 2s when the digest reports a pending call or
  the endpoint returns any live one. Reads the *raw* response so an answered
  in-progress call keeps the fast interval, not just a ringing one.
- Maintenance polling (both this and `CRM-admin-panel`): was an unconditional 30s
  raw `setInterval` that ran even logged-out and never paused in a hidden tab. Now
  one check on mount, and the interval runs **only while in maintenance** —
  entering maintenance is already learned from the 503 any request returns.

## Results

| Path | Before | After |
|---|---|---|
| Idle digest poll | ~8-9 SQL | **0 SQL**, 1 cache read |
| Open chat thread | cursor queries + up to 200 rows serialized, 1.5s | 1 cache read, 3s |
| Chat conversation list | 4-6 queries **per row** | flat ~5 total |
| Supervisor on Calls page | flat 2s | 8s idle / 2s live |
| `_latest_activity_maps` | O(all activity rows), unbounded | 1 query per kind |

## Two production traps found along the way

**1. django-q was never using your Redis.** `Q_CLUSTER["django_redis"] = "default"`
only works if the `django-redis` **package** is installed. It is not — the project
uses Django's built-in `RedisCache`. django-q's guard is
`if django_redis and Conf.DJANGO_REDIS`, always False, so it fell through to
`redis.StrictRedis(**{})` = **localhost:6379 db 0, no password**, ignoring
`REDIS_URL` entirely. Now passed as a URL string, which takes the `redis.from_url`
path. **Still unverified against the real server** — see the checklist.

**2. logrotate `size` is not `maxsize`.** `size` *replaces* the time schedule, so
`daily` + `size 50M` means "only on size, never on a timer". Use `maxsize`. Also
`/var/log` is group-writable by `syslog`, and logrotate silently **skips** the
whole block without an explicit `su root syslog` — the failure mode is a no-op,
not an error, so always check with `logrotate -d` after editing that file.

Related: the handlers were plain `FileHandler`, which never reopens a rotated
file. Installing logrotate against them would have left every process writing to
a deleted inode with the new file empty until restart. `RotatingFileHandler` is
also wrong here — both gunicorn workers, the qcluster and ~21 cron commands write
these same files and would each rotate independently. `WatchedFileHandler` +
external logrotate is the only correct pairing.

## Verification

- **970 backend tests pass** (`.\.venv\Scripts\python.exe -m pytest`, ~20 min).
- Both frontends build. TypeScript errors unchanged at their pre-existing
  baseline of **41** in `CRM-project` / **5** in `CRM-admin-panel`, none in
  touched files. Do not treat those as regressions; they predate this work.
- `makemigrations --check` → no changes. Nothing here touches model fields.

Tests worth knowing about, because they encode the contracts above:

- `tests/test_sync_digest.py` — `assertNumQueries(0)` on a matching 304. If this
  fails, the fast path is broken.
- `tests/test_tenant_chat.py` — flat query count as thread count grows; chat ETag
  changes on new message **and on peer read** (read receipts would otherwise
  freeze); token rejected for a different viewer or different paging.
- `tests/test_dashboard_latest_activity.py` — newest-wins semantics across all
  three activity kinds, and ≤3 queries against 30 rows of history.

## Deployment checklist

Repos are all clean and pushed: `CRM-api-1 @ ae79e85`, `CRM-project @ b7de902`,
`CRM-admin-panel @ f95e1bf`.

**Ran 2026-09-01. All closed but one.**

- [x] Backend deployed
- [x] logrotate installed and dry-run verified clean
- [x] **`DRF_THROTTLE_USER=300/minute` in the production `.env`** — verified, it
      reads `300/minute`.
- [x] **Verify the django-q Redis broker** — `django_q:CRM_Queue:cluster:*` is
      present on our Redis, and `maxmemory-policy` is `noeviction`. Beyond that,
      `crm-qcluster.service` had been up 19h processing real
      `deliver_push_task` jobs with `FCM multicast … 1/1 delivered`, so
      `PUSH_QUEUE_ENABLED` is already on and working — this document's assumption
      that it was still off was out of date. **This unblocked the cron migration**
      (open item 2 below), now shipped as `manage.py install_qcluster_schedules`.
- [ ] **Confirm 304s in production** — still open. `journalctl -u crm-api --since
      "30 min ago" | grep -c 'sync/digest'` returned 0, which is ambiguous: either
      no tab was open (likely, it was 22:49 UTC and the journal showed only
      cron-driven pushes) or access logs are not reaching the journal.
      Disambiguate with `journalctl -u crm-api --since "30 min ago" | grep -c '"GET'`
      and then watch live with a CRM tab open. **All 200s means the frontend build
      did not deploy.**
- [ ] **Smoke-test freshness** — ringing call toast, walk-in alert, team-chat
      message, peer read → sender's "seen" tick, notification read → badge clears.
      Anything lagging ~30s is a missing bump in `sync/signals.py`.

Two incidental findings from that run, both worth a look:

- `WARNING User abdulah_amer has no FCM token` — some users are unreachable by
  push. The new `UserDevice` table makes this auditable per platform.
- `push:lead_no_follow_up:19` was processed twice within 7s. The `:19` is a *user*
  id, so two leads for one user is normal — but check it against open item 4, the
  20MB `lead-no-follow-up` log.

## Open work, in priority order

1. **Load test.** Still the biggest gap, and now more so: everything here and in
   the follow-up round is a measured *reduction*, and there is still no measured
   *ceiling*. Script N tabs on the real idle pattern and ramp until p95 knees.
   The idle pattern has changed — with realtime on it is one digest request per
   30s per browser plus an open socket, not one per 5s per tab — so measure both
   configurations and include socket count in what you watch.
2. ~~**Cron cold starts.**~~ **Done** — `manage.py install_qcluster_schedules`
   installs both per-minute jobs as django-q `Schedule` rows. The broker
   verification this was waiting on is closed (see checklist). Installing the
   schedules requires commenting out the two matching `crontab` lines, or each job
   runs twice a minute; the command prints which ones.
3. ~~**Multi-tab load.**~~ **Done** — `hooks/useRealtimeChannel.ts` elects one
   leader tab per browser over `BroadcastChannel` and fans events out to the rest,
   so N tabs hold one socket between them.
4. **`/var/log/crm-api-lead-no-follow-up.log` is already >20MB** while every other
   cron log is small. A 15-minute job producing that much output may be logging an
   error every run. Worth a look — and see the duplicate-processing note in the
   checklist above, which may be the same thread.
5. **Index check.** The dashboard rewrite leans on a usable index on
   `(client_id, created_at)` for `ClientTask` / `ClientCall` / `ClientVisit`. It is
   strictly less work than the old full scan either way, but if the dashboard is
   slow under real data volume, look there first.
6. **A dedicated mobile notification channel for ringing calls.** The inbound
   WhatsApp call push currently reuses the `arrival` channel to get an insistent
   ring, because channel ids here must match channels `crm_mobile` actually
   creates. The behaviour is right; the tone is borrowed from walk-ins. Adding a
   `call` channel needs a mobile release with a bundled sound.
7. **Web push is built but unverified.** `services/webPush.ts` and
   `public/firebase-messaging-sw.js` are complete and gated on
   `VITE_FIREBASE_*` + `VITE_FIREBASE_VAPID_KEY` being set. Nobody has run it
   against a real Firebase web app yet — those credentials did not exist at the
   time of writing. Until they are filled in it no-ops.

## Things deliberately NOT done, and why

- **Caching `dashboard-summary`.** Originally planned, then rejected: the company
  counter moves on chat/call/arrival events, not on `Client`/`ClientTask` writes,
  so it would not invalidate correctly — and a counter that *did* track CRM writes
  would rarely be stable long enough to hit, while eroding the digest win if
  folded into the same key. The endpoint is not polled. Its real problem was the
  unbounded scan, which was fixed directly instead.
- **Merging the chat presence GET and POST.** The POST only fires while actively
  typing, so it is the smaller of the two; backing the GET off to 2.5s captured
  most of the win without coordinating two React Query lifecycles.
- **Raising `GUNICORN_WORKERS`.** The box is shared and `cpu_count()` on a VPS does
  not match billed vCPU. Past the real core count more workers add contention, not
  capacity. Measure (item 1) before touching this.
