# Realtime channel — how it works and how to deploy it

## What this is

LOOP CRM used to do all of its "real-time" over HTTP polling. That work is done in
three layers, and this document covers the third:

1. **The digest as a change feed.** `GET /sync/digest/` reports a monotonic counter
   per slice of company data (`chat`, `calls`, `arrivals`, `tenant_chat`, plus
   `user` and `global`). Clients watch the slice they care about and refetch only
   when it moves, instead of running a timer per view.
2. **Conditional GETs.** The polled list endpoints send an `ETag` built from those
   same counters and answer a matching `If-None-Match` with a bare `304`, before
   running any queries.
3. **The WebSocket** — this document. It delivers "slice X moved" as it happens,
   so the digest poll drops to a 30s heartbeat.

Each layer works without the ones after it. **Turning realtime off degrades
freshness, never correctness.**

## Architecture

```
                    :8000  gunicorn (WSGI)  ──►  wsgi.py    ── REST API
   nginx  ──┤
                    :8001  uvicorn (ASGI)   ──►  asgi.py    ── /ws/ only
```

Gunicorn is untouched. This is the load-bearing detail: gunicorn runs `gthread`
with **8 request slots** (2 workers x 4 threads), and a WebSocket held inside that
pool would occupy one for the life of the connection. A few hundred open tabs and
the API stops answering anyone. On the asyncio process the same connections are
cheap — an idle socket costs a file descriptor, not a thread.

This is also why SSE was rejected: it has the same problem inside gunicorn, and
being unidirectional it could not absorb the client→server half of typing/presence.

### The frames carry no data

A frame is exactly `{"scope": "company:calls", "version": 41}`. The client responds
by refetching the digest through the normal authenticated endpoint.

That indirection is the security design, not laziness. Groups are company-wide, but
visibility *inside* a company is not — WhatsApp chat and call access are per-user,
lead access is per-assignee. If frames carried payloads, every ACL rule would be
reimplemented on the socket, and the failure mode would be one colleague seeing
another's leads. Sending a version number means group membership is the entire
authorization surface, and data keeps flowing through views that already filter it.

### Client → server: presence

Presence (typing / recording / uploading) is the one thing clients may *send*.
It fits the socket precisely: ephemeral, keystroke-rate, worthless a few seconds
late, and it never touches the database.

```
→ {"action": "subscribe",   "conversation": 8}
→ {"action": "presence",    "conversation": 8, "state": "typing"}
→ {"action": "unsubscribe", "conversation": 8}

← {"scope": "presence", "conversation": 8, "user_id": 42, "state": "typing"}
```

Because this reverses the data flow, it has its own authorization
(`realtime/presence.py`), and both rules matter:

* **A connection may only address a conversation it was admitted to.** Admission
  happens on `subscribe`, is re-checked in the database against
  `user_participates_in_conversation` — the same predicate the REST API uses —
  and the id is remembered on the connection. A `presence` frame naming anything
  else is dropped. Being connected is not permission to write into a group.
* **The sender is taken from the connection, never the frame.** A client that
  puts `user_id` in its payload is ignored, so nobody can claim someone else is
  typing.

Frames are rate limited per connection (40 per 10s — far above the ~1 per 3s a
real client sends) so a loop cannot turn one socket into a broadcast amplifier.

Socket presence is also written into the cache the HTTP `peer-presence` endpoint
reads, because the two transports have to describe the same world: a colleague
whose socket is down is still polling, and must still see you typing.

The web client backs that poll off from 2.5s to 15s while the socket is
delivering, rather than stopping it. An open-but-silent socket is a failure the
client cannot otherwise detect, and the slow poll is what notices.

### Online presence

An open socket *is* presence, so it is the primary signal:

```
→ {"action": "heartbeat"}          // every 45s, inside the server's 90s TTL
```

The consumer marks the user live on `connect` — the green dot lights the moment
the socket is accepted, rather than up to a minute later when the next HTTP
heartbeat lands.

`accounts/presence.py` is the single reader, and online means **live socket OR
seen within 90 seconds**. The `last_seen_at` half is what keeps the mobile app
(which has no socket) and any browser with realtime disabled working exactly as
before — turning realtime off degrades presence to the previous behaviour rather
than breaking it.

`last_seen_at` is still written, but throttled to once every 5 minutes per user
instead of once a minute. The column still backs "last seen 3 hours ago" text and
reports, which a cache with a 90s TTL cannot; the cache answers "online right
now", which a 5-minute-old column cannot.

Bulk matters here: `live_user_ids()` takes a list and does one `get_many`. List
endpoints and the dashboard pass the resulting set into the serializer context as
`online_user_ids`, so a page of users costs one cache round trip rather than one
per row.

### Where events come from

`sync/version.py` notifies registered listeners whenever a counter is bumped;
`realtime/publish.py` is the listener, registered in `RealtimeConfig.ready()`.

Hooking the *bump* rather than the model signal is deliberate. Signals miss bulk
updates — the WhatsApp mark-read view is a `queryset.update()` that fires no
`post_save` — and they miss `sync.cache.invalidate_badges`. Hooking the counter
means every path that records a change also announces it, by construction.

Publishes are deferred to `transaction.on_commit`, so a client is never told to
refetch data that is not yet visible, and never told at all if the write rolls back.

## Deploy

Order matters. Each step is safe on its own; the flag goes last.

### 1. Install dependencies

```bash
cd /var/www/crm-api
source venv/bin/activate
pip install -r requirements.txt      # channels, channels-redis, uvicorn
```

### 2. Confirm Redis is suitable

```bash
REDIS_URL=$(grep -E '^REDIS_URL=' .env | cut -d= -f2- | tr -d '"'"'")
redis-cli -u "$REDIS_URL" config get maxmemory-policy    # MUST be noeviction
```

Not optional. An evicted group registration silently drops a client's
subscription, and the symptom is "realtime works for some people", not an error.

### 3. Start the service

```bash
sudo cp deploy/crm-realtime.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crm-realtime
sudo systemctl status crm-realtime --no-pager
```

### 4. Route /ws/ in nginx

Paste `deploy/nginx-realtime.conf.snippet` into the existing `server { }` block,
then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Turn it on

```bash
# .env
REALTIME_ENABLED=true
```

```bash
sudo systemctl restart crm-api        # publishers pick up the flag
```

Then set `VITE_REALTIME_ENABLED=true` in the frontend build and deploy it. Server
first: with the flag on and no clients, nothing is wasted; with clients and no
server, they retry against a route that does not exist.

## Verifying

```bash
sudo journalctl -u crm-realtime -f
redis-cli -u "$REDIS_URL" info clients          # connections track open tabs
```

In the browser, on a logged-in tab: DevTools → Network → WS should show **one**
connection, and it should stay open. Open three more tabs — still exactly one
connection, because tabs elect a leader over `BroadcastChannel` and the leader
fans events out to the rest.

**The acceptance test** is the fallback, not the feature:

```bash
sudo systemctl stop crm-realtime
```

Ringing call toast, walk-in alert, team-chat message, peer-read "seen" tick,
notification-read badge clear — every one of these must still work, just slower
(within ~5s instead of instantly). If any of them breaks, the fallback is wrong
and realtime is not safe to depend on. Start it again afterwards.

## Failure modes

| Symptom | Cause |
|---|---|
| Socket connects then closes with **4401** | Access token rejected. Expired or the client sent a refresh token. |
| Handshake 200s instead of upgrading | The `Upgrade`/`Connection` headers are missing from the nginx block. |
| Connection drops every ~60s | `proxy_read_timeout` left at the nginx default. |
| Connect/disconnect loop | More expensive than the polling it replaced. Check auth and the nginx timeout before anything else. |
| Realtime works for some users only | Redis evicting group registrations — check `maxmemory-policy`. |
| Everything feels ~30s late | Socket is dead but the client thinks it is connected, so it stayed on the slow heartbeat. Check `journalctl -u crm-realtime`. |

## Local development

`channels` is **not** in `INSTALLED_APPS`, on purpose — adding it replaces Django's
`runserver` with the Channels ASGI one, silently changing how everyone runs the app.
Nothing here needs it. `manage.py runserver` behaves exactly as before and serves no
WebSocket. To exercise the socket locally, run what production runs:

```sh
.\.venv\Scripts\python.exe -m uvicorn crm_saas_api.asgi:application --port 8001
```

With no `REDIS_URL`, the channel layer falls back to in-memory, which only works
when the publisher and the socket are the same process — fine for a single dev
server, never valid in production.

## Tests

`tests/test_realtime.py` covers connect/reject, group membership (including that a
company's events do not reach another company), the bump→publish bridge, the
enable flag, and that a broken channel layer does not break the write that
triggered it. That last one is the one to keep green: if it regresses, an
unreachable Redis stops people creating leads.
