# Telephony (Loop Cloud PBX): CRM side

This is the `telephony` app. The PBX nodes, the applier and the CRM↔PBX contract live in the sibling repo `../CRM-pbx`. Read [`CRM-pbx/docs/contract.md`](../../CRM-pbx/docs/contract.md) first. The phased plan is in `.cursor/plans/loop_cloud_pbx_integration_plan.md`.

## What is built (Phase 1)

| Piece | Where |
|---|---|
| Contract port (naming, credentials, call ids, artifacts) | `telephony/contract.py`. It must reproduce `tests/fixtures/pbx_contract/*.json`, which are copies of `CRM-pbx/contract/vectors`. |
| Models | `telephony/models.py`: node, tenant, extension, trunk, number, inbound/outbound route, artifact, provisioning job |
| Compiler | `telephony/services/compiler.py`, typed rows → `pjsip_t<key>.conf` + `extensions_t<key>.conf`. Golden output: `tests/snapshots/telephony/` |
| Provisioning state machine | `telephony/services/provisioning.py` (compile → versioned encrypted artifact → node applies → apply-result) |
| Tenant API | `/api/v1/telephony/`: `status/`, `settings/` (call recording), `extensions/` (+ `credentials/rotate/`), `trunks/`, `numbers/`, `inbound-routes/`, `outbound-routes/` |
| Platform admin API | `/api/v1/admin/telephony/`: `nodes/` (+ `rotate-tokens/`), `tenants/`, `tenants/candidates/` (pilot-approved companies with no tenant, each with the provision gate's `eligible` verdict), `tenants/<company_id>/provision/`, `…/suspend/`, `…/resume/`, `…/jobs/`, `numbers/<id>/verify/` |
| Internal node API | `/api/v1/internal/telephony/nodes/<id>/desired-state`, `…/artifacts/<tag>/<version>/`, `…/apply-result`. Authenticated by node token, not by JWT or X-API-Key. |

**Access**

- **Tenant endpoints** need all of:
  - an active subscription;
  - the plan feature `telephony`, which is **off by default** (a paid add-on);
  - the integration policy `telephony` not being kill-switched;
  - the caller being a company admin, or a supervisor with `manage_settings`.
- **Admin endpoints** use `CanManageTelephony` (limited-admin flag `can_manage_telephony`; tenant managers, `can_manage_tenants`, may read but not change anything). The node's GSM-gateway link (`gateway_wg_public_key`, `gateway_wg_endpoint`, `gateway_wg_address`) is set through `admin/telephony/nodes/<id>/`.

**Plan quotas** (in `Plan.limits`; the defaults are in `subscriptions/entitlements_catalog.py`):

- `max_pbx_extensions` (10)
- `max_pbx_trunks` (2)
- `max_pbx_concurrent_calls` (5)
- `max_pbx_cps` (1)
- `max_pbx_recording_bytes` (5 GB)

## Calls (Phase 2)

| Piece | Where |
|---|---|
| Models | `PbxCall` (id = contract call id), `PbxCallLeg`, `PbxCallEvent` (durable, deduped log), `PbxRecording` |
| Event ingest | `POST /api/v1/internal/telephony/events` → `telephony/services/events.py`. Idempotent by `event_id`, order-tolerant (the first `call.ended` is final), and only for tenants homed on the reporting node. |
| Recordings ingest | `POST /api/v1/internal/telephony/recordings` → `telephony/services/recordings.py`. Rejects any storage key outside `telephony/<company_id>/…`. |
| Lead timeline | On `call.ended`, an inbound/outbound call becomes a `ClientCall(source="pbx", pbx_call=…)` on the lead. The lead is the click-to-call contact, otherwise a phone match. This matches how WhatsApp calls are linked. |
| Realtime | The `telephony` sync slice is bumped once per node batch. A data-free `telephony.event` frame (`{event, call_id}`) goes to each agent on the call. |
| Tenant API | `GET /telephony/calls/` (cursor), `…/calls/<id>/`, `…/calls/<id>/recordings/`, `POST /telephony/recordings/<id>/access/` (signed URL, 5 min, logged), `GET /telephony/events/` (admin replay) |

**Call visibility:** admins and supervisors with `manage_settings` see every call. Everyone else sees calls they were on, plus calls on leads assigned to them.

**Recording playback** is a 5-minute presigned GET on the `recordings` storage (Cloudflare R2, `docs/STORAGE.md`). With local recordings storage (dev), it is a signed `/api/public/telephony/recordings/<token>/` link instead.

### Call recording

Recording is **off by default**. It is a tenant setting:

| Field | Values | Meaning |
|---|---|---|
| `recording_mode` | `off`, `external`, `all` | `external` records calls through a trunk; `all` also records internal calls |
| `recording_notice` | `none`, `ar`, `en`, `ar_en` (default) | The announcement the outside party hears before the call connects |

- **Changing it:** `GET/PATCH /api/v1/telephony/settings/`, by tenant admins only.
- **Platform legal gate:** `TELEPHONY_RECORDING_ALLOWED` is off by default. While it is off:
  - any `recording_mode` other than `off` is refused with `validation.pbx_recording_unavailable`;
  - `GET /telephony/settings/` returns `recording_available: false`, and the dashboard locks the control;
  - the compiler emits no MixMonitor or notice, whatever a tenant row says.
  Turn it on only after legal approval, once the approved 8 kHz / 16-bit / mono Arabic and English notices are installed on every node.
- **Attestation:** turning recording on requires `"accept_recording_terms": true`. That is the admin's attestation that the company may record and will inform callers. `recording_accepted_at` and `recording_accepted_by` are stored.
- **Audit:** every change is written to `SystemAuditLog` (`telephony.recording_settings_changed`) and triggers a recompile.

How a recording flows (contract §7):

1. The compiler emits `t<key>-sub-record` and `t<key>-sub-notice`. MixMonitor records bridged audio only, as WAV, into the node spool.
2. The worker uploads the file to the `loop-pbx-recordings` bucket under `telephony/<company_id>/…`.
3. It then reports the recording to `POST /internal/telephony/recordings`.
4. The CRM links the recording to the call and sets the lead timeline's `ClientCall.recording_status` to `ready`.

**Playback in production** uses the CRM's one object-storage configuration (`docs/STORAGE.md`):

- `R2_ACCOUNT_ID` (or `R2_ENDPOINT_URL`), `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`, whose token can read and delete (for retention) the recordings bucket;
- `R2_RECORDINGS_BUCKET`, default `loop-pbx-recordings`.
- The old `RECORDING_STORAGE_BACKEND=r2` + `RECORDING_S3_*` / `TELEPHONY_RECORDING_BUCKET` + `AWS_*` variables still work as fallbacks, but credentials are now always passed explicitly (never boto3's ambient chain).
- Do **not** put an R2 lifecycle rule on the recordings bucket: it would delete recordings on legal hold. Retention is `purge_pbx_recordings`.
- `manage.py check_object_storage` verifies the bucket from the CRM VPS.

**Retention, deletion and the storage quota (Phase 5):**

- `recording_retention_days` (tenant setting, default 90, 7–3650) is applied by `manage.py purge_pbx_recordings`, daily from cron (`crontab_complete.txt` #22). The object is deleted first, then the row; a storage error keeps the row for the next run. The lead timeline's `recording_status` becomes `deleted`. Each run is audit-logged (`telephony.recordings_purged`).
- `recording_legal_hold` (platform operator only) stops both retention and manual deletion for the tenant.
- `DELETE /telephony/recordings/<id>/` lets a tenant admin remove one recording (audited, `telephony.recording_deleted`; 409 `legal_hold`).
- When stored recordings reach the plan's `max_pbx_recording_bytes`, the tenant gets `recording_paused_quota` and a recompile: the dialplan stops starting MixMonitor (audit `telephony.recording_quota_paused`). Freeing space resumes it (`…_resumed`). The check runs on every recording ingest, deletion and purge.
- `GET /telephony/settings/` also returns `recording_bytes_used` / `recording_bytes_limit`; Settings → Phone System → Call recording shows the storage meter, the retention field and the paused/legal-hold notices.

## Click-to-call and the browser softphone (Phase 3)

| Piece | Where |
|---|---|
| Policy + originate | `telephony/services/calls.py` (`place_call`, `hang_up`) |
| Worker client | `telephony/services/pbx_worker_client.py`: CRM → node worker over the private link, with the node's `crm_token` |
| Browser credentials | `telephony/services/webrtc.py`: contract §4 HMAC credentials, cached per user until `refresh_after` |
| Tenant API | `POST /telephony/calls/` (202, `Idempotency-Key`), `POST /telephony/calls/<id>/hangup/`, `GET /telephony/me/`, `GET /telephony/me/webrtc-credentials/`, `GET /telephony/agents/status/`, `GET/PUT /telephony/agents/me/status/` |
| Agent status | `telephony/services/agents.py` (`set_my_status`, `presence`) |
| Web | `CRM-project/components/telephony/` (`SoftphoneProvider`, `SoftphoneDock`, `AgentStatusToggle` in the header, `PbxCallsPanel`, `PbxRecordingPlayer`); click-to-call in `LeadContactPhone` |
| Mobile | `crm_mobile` lead timeline renders `source == "pbx"` (`timeline_builder.dart`, `lead_timeline.dart`) |

**`POST /telephony/calls/`** with `{destination, crm_contact_id?}`. Anyone with telephony and their own active extension can call. The policy checks run in this order:

1. The tenant is live and outbound calling is not suspended.
2. The caller has an extension.
3. The destination is E.164 (spaces, dashes and a leading `00` are normalised) and matches an enabled outbound route. The routes are the allowlist. An extension with **Outbound SIM** set (`PbxExtension.outbound_number`, a verified number) presents that number and uses its trunk; every other extension uses the route's caller id and trunk. The gateway chooses the physical SIM port from that caller id.
4. The lead is one the caller may see. This mirrors `ClientViewSet`.
5. `monthly_pbx_minutes` is not used up.

If the checks pass, the CRM creates the call as `queued` with a uuid4 and asks the home node to originate.

- **Node refusals** come back as these error codes:
  - `concurrency_limit` and `cps_limit` (429);
  - `phone_not_registered` and `outbound_suspended` (409);
  - `pbx_unavailable` (503).

  In each case the queued call is marked `failed`.
- **Error bodies** are `{code, message}`, which the envelope renders as `error.code` and `error.message`.
- **Minutes:** each answered outbound call adds its started minutes to `monthly_pbx_minutes` when it ends.

**How a click-to-call proceeds:** the node rings the agent's own phone first.

- **Browser extension:** the INVITE carries `X-Loop-Call-Id`. The softphone answers it automatically only if it equals the call it just requested; an INVITE that arrives before the 202 is held until the ids can be compared.
- **Desk phone:** the dock tells the agent to pick up.
- When the agent answers, the dialplan dials the lead with that extension's SIM number, or the route's verified caller id when the extension has none.

**Softphone:**

- **Gating:** `GET /telephony/me/` returns 403 when the plan has no telephony or the kill switch is on. The softphone then stays inert, and `LeadContactPhone` keeps its `tel:` link.
- **Registration:** a WebRTC extension registers with JsSIP, which is loaded on demand in its own chunk.
- **One call at a time:** across PBX and WhatsApp.
  - While WhatsApp is busy, an inbound PBX call gets 486 and click-to-call is refused.
  - While a browser PBX call is already up, one more inbound call is offered in the dock. The caller hears hold music (`Dial` option `m`). Answer holds the current call and answers the new one; Reject sends 486 on that invite only. A further invite still gets 486. A desk phone that rejects the second invite with 486 drops the caller; one that does its own call waiting does not.
  - While a PBX call is up, WhatsApp refuses to start a call (`whatsappCallBusyPhoneSystem`), and an inbound WhatsApp call shows the call-waiting banner instead of ringing over it. Its "end and answer" hangs up the PBX call first. The softphone registers its busy state and hang-up with `WhatsAppCallListener` (`registerOtherLine`).
- **Live state:** polled over REST every 2.5 s, and only during a call. The socket carries no call data (ADR 0003).
- **Transfer:** blind transfer with SIP REFER to an extension number. The node reports a completed transfer as `call.transferred` (contract §5). The CRM stores it and lists it in the call detail's `transfers`, and the target agent gets a `telephony.event` frame. The transfer never changes the call's outcome.

### Agent status

`PUT /telephony/agents/me/status/ {"status": "available" | "paused"}` is set from the header toggle.

- **Who:** the caller's own active extension only. It needs a live tenant (`not_provisioned`) and an extension (`no_extension`, 403).
- **Order:** the CRM pushes the status to the home node first (`POST /v1/agents/status`, contract §6) and saves it only once the node took it. A node refusal or an unreachable node gives `pbx_unavailable` (503) with nothing saved.
- **Effect:** the worker sets the device state `Stasis:t<key>_<ext>-agent`, which Asterisk keeps across restarts. The compiled `-internal` entry answers busy (cause 17, SIP 486) instead of ringing a paused agent, for direct, internal and DID calls. Such calls end as `busy`. The agent can still place calls. No recompile.
- **Presence:** `GET /telephony/agents/status/` lists the company's active extensions with `agent_status`, `registered` (from the node) and `on_call` (live calls from the last 4 h). Anyone with telephony may read it, for example to pick a transfer target. Changes bump the `telephony` slice.
- **Node migration:** a new node has an empty astdb, so paused agents must be re-pushed (Phase 5 `migrate-node`).

### Apply-now nudge

With `TELEPHONY_APPLY_NUDGE=true`, every compile that commits calls the home node's `POST /v1/apply-now`. It is best effort, with `TELEPHONY_APPLY_NUDGE_TIMEOUT` (1.5 s). The applier then pulls at once instead of on its 15 s poll. It is off by default, so tests and dev without a worker never reach the private link.

**Verified:**

- **Unit tests:** `tests/test_telephony_click_to_call.py`.
- **Live E2E:** `test_click_to_call_rings_the_agent_then_the_lead`. The CRM API, the real worker control API, ARI, the desk phone and the lead give an answered call, linked to the lead.
- **Browser:** `CRM-pbx/devtools/webrtc/run.mjs`, headless Chrome with JsSIP against the dev Asterisk. It covers an inbound call to the browser and a click-to-call auto-answered by the browser, both with DTLS-SRTP and PCMA media flowing.
- **The real UI:** `CRM-pbx/devtools/webrtc/crm_ui.mjs` (see Tests) drives the CRM-project app itself in headless Chrome against the live PBX. It covers the header pause, the dock's inbound popup, answer and transfer, and click-to-call from a lead page.
- **Live E2E:** `test_paused_agent_is_not_rung_and_compiles_land_on_a_nudge`.
- **Not covered by a browser test:** the WhatsApp side of the one-line rule (it needs a WhatsApp Cloud API number).

**CORS:** the softphone's `POST /telephony/calls/` sends `Idempotency-Key`, so that header is in `CORS_ALLOW_HEADERS`. Without it a cross-origin browser fails the preflight and never sends the call (`test_browser_may_send_the_idempotency_key_cross_origin`).

**Live calls and queue wallboard** (`GET /telephony/wallboard/`, Calls → Phone system → Live):

- Per queue: callers waiting and the longest wait, the members (available, on a call, paused, offline), and today's offered/answered/abandoned/sent-on calls with the average wait and the share answered within 20 s.
- Live calls, filtered like the call history (`visible_calls`), and agent presence.
- Built from `call.queued` / `call.dequeued` (contract §5): `PbxCall.queue`, `queued_at`, `queue_left_at`, `queue_outcome` (answered | abandoned | overflow).
- The board re-reads when the `telephony` sync slice moves (version-token polling, no data socket), with a 30 s floor; durations tick in the browser.

**Not built yet:**

- the Calls page is gated by the WhatsApp integration policy, so a WhatsApp kill switch also hides the "Phone system" tab.

## Routing and the Phone System settings (Phase 4)

| Piece | Where |
|---|---|
| Models | `PbxBusinessHours`, `PbxIvrMenu` + `PbxIvrOption`, `PbxQueue` + `PbxQueueMember`, `PbxGatewayPeer`; inbound routes gained IVR/queue destinations, business hours and a closed destination |
| Tenant API | `business-hours/`, `ivr-menus/` (options nested, replaced as a whole), `queues/` (members nested, plus `PUT queues/<id>/members/` from spec section 6), `gateways/` (+ `verify/`, `enrollment/reset/`) |
| Public | `POST /api/v1/public/telephony/gateways/enroll/ {token, public_key}`: the installer's one call. The one-time token is the authorization; throttled like a login. |
| Compiler | `services/compiler.py` `_Routing`, `_render_hours/_ivr/_queue_entry/_queues`. Golden output: `tests/snapshots/telephony/routing/` |
| Gateways | `services/gateways.py` (address pinning, tokens, enrollment, verify, `desired_peers`) |
| Web | `CRM-project/pages/settings/telephony/*`: Settings → **Phone System** (extensions, numbers, trunks and gateways, inbound and outbound routes, business hours, IVR menus, queues, call recording) |

**Destinations.** Every routing choice is typed: an extension, an IVR menu, a queue, or hang up (`models.DestinationType`). The serializers scope each target to the caller's company and clear the targets the type does not use.

- A target that a route, menu or queue still uses cannot be deleted. The API answers 409 `in_use` (destinations are `PROTECT`).
- A queue never overflows into another queue.
- Without business hours, the closed destination is reset to hang up.

**Business hours.** Weekly opening periods (`days` 0 = Monday … 6 = Sunday, `HH:MM`, end after start) in an IANA timezone, plus whole closed dates. An inbound route with hours goes to its closed destination outside them.

**IVR menus.** The greeting is a fixed platform prompt (`loop/ivr-default`, `loop/ivr-welcome-ar`, `loop/ivr-welcome-en`) or one of the company's own recordings, so a tenant never names a file.

- Keys are `0-9`, `*`, `#`, each at most once and at most 12.
- A wrong key replays the menu up to `max_retries` times. Silence, or too many wrong keys, goes to the timeout destination.

**Custom greetings** (`/telephony/sounds/`, Settings → Phone System → Greetings):

- Upload is multipart (`name`, `file`): a WAV of 16-bit PCM, mono, 8 kHz, 0.5–60 s. The CRM-project UI converts whatever the admin picks (MP3, M4A, WAV…) in the browser with Web Audio, so the CRM never transcodes. At most 20 per company.
- Stored as raw G.711 A-law (`PbxSound.audio`, with its sha256): the codec calls use, so Asterisk plays it without transcoding. `GET sounds/<id>/audio/` returns it as a PCM WAV for the admin to listen to.
- An IVR menu's `greeting` ships that sound inside the tenant's artifact as `sound_t<key>_<id8>.alaw` (sha256-checked by the node; contract §2) and the dialplan plays `/etc/asterisk/loop/active/sound_t<key>_<id8>`. Only greetings a menu uses are shipped; one still in use cannot be deleted (409 `in_use`).
- Artifacts are pruned to the newest 5 versions plus the running and draining ones, since greetings make them larger.

**Trunk test** (`POST /telephony/trunks/<id>/test/`, the check icon on a trunk row): the home node sends one SIP OPTIONS to the contact its live config has for the trunk (contract §6, trunk probe), and the CRM checks the config version, inbound identification (allowed networks), numbers and their verification, inbound routes and outbound routes. Each check is `ok`, `warning`, `failed` or `skipped` (REGISTER is never used: trunks are identified by address). The probe result is kept as the trunk's `health`. At most one test per trunk every 5 s (429 `too_many_tests`).

**Queues.** The strategy, how long each agent rings, the longest wait (10 s – 1 h), wrap-up time, member extensions with a penalty, and where callers go after the longest wait.

- A paused agent is skipped: members ring through `t<key>-agent`, which runs the same pause check as direct calls.
- A caller who hangs up in a queue or IVR is `abandoned` (contract §5).

**GSM gateways** (spec §5 onboarding; contract §5 *GSM gateway peers*):

1. **Add.** Adding a gateway pins it an address on the tenant's home node (`10.78.0.x`) and creates its `gsm_gateway` trunk, identified by that address only. It counts toward `max_pbx_trunks`. The response shows a one-time enrollment code (72 h; `TELEPHONY_GATEWAY_TOKEN_TTL_HOURS`).
2. **Enroll.** The installer posts the code with the gateway's WireGuard public key. The reply has the tunnel and SIP settings, the node adds the peer, and the code is spent.
3. **Verify.** The node qualifies the gateway trunk (SIP OPTIONS every 30 s) and reports it reachable or down. **Verify** marks the trunk READY only after the node has reported it reachable.
4. **Pinned.** The trunk's address cannot be edited; a replaced gateway gets a new code (**New enrollment code**).
5. **Operator steps first.** The platform operator sets the node's `gateway_wg_public_key`, `gateway_wg_endpoint` and `gateway_wg_address` (admin nodes API). Until then enrollment answers 503 `gateway_link_not_configured` and keeps the code.

**Validation.** Every Phone System form has a catalog form id: `pbx_extension.upsert`, `pbx_trunk.upsert`, `pbx_gateway.create`, `pbx_number.upsert`, `pbx_inbound_route.upsert`, `pbx_outbound_route.upsert`, `pbx_settings.update`, `pbx_business_hours.upsert`, `pbx_ivr.upsert`, `pbx_queue.upsert`, `pbx_sound.upload` and `pbx_sound.update`. The admin console's forms are `pbx_node.upsert`, `pbx_tenant.admin_update`, `pbx_tenant.suspend`, `pbx_tenant.migrate` and `pbx_fraud_alert.resolve`. Each is bound to its serializer in `validation/bindings.py`. After a schema change, run `manage.py export_validation_catalog`.

**Verified:**

- **Unit tests:** `tests/test_telephony_routing.py`, `tests/test_telephony_gateways.py`, compiler snapshots and the isolation suite.
- **Live E2E:**
  - `test_ivr_queue_and_business_hours_live`: hours open, the IVR, key 1 by RFC 4733 DTMF, the queue, desk 101 answers. With today closed, the closed destination is used.
  - `test_gateway_enrollment_and_reachability_live`: enrollment, the real applier's `wg set`, qualify, verify, then loss reported.
- **The real UI:** `crm_ui.mjs` builds a queue, an IVR menu and business hours in Settings → Phone System. A real call then presses 1, and the queue rings the browser. It also turns recording on with the attestation and enrolls a gateway.
- **Greetings, trunk test, wallboard:** `test_custom_greeting_is_heard_by_the_caller` (the caller hears the uploaded 1500 Hz greeting, share 1.00, and not after switching back); `test_trunk_test_probes_the_configured_contact_live`; `crm_ui.mjs` uploads a 44.1 kHz stereo file through the UI (stored as 3 s of 8 kHz A-law), the caller hears it, runs the trunk test, and watches a waiting caller on the wallboard turn into a live call.

## SaaS controls (Phase 5)

| Piece | Where |
|---|---|
| Recording retention, deletion, storage quota | `services/recordings.py` (`purge_expired`, `delete_recording`, `enforce_quota`), `manage.py purge_pbx_recordings` |
| Usage | `services/usage.py`, `PbxUsageDaily`, `manage.py rollup_pbx_usage` (hourly, `crontab_complete.txt` #23), `GET /telephony/usage/` (Settings → Phone System → Usage) |
| Fraud | detection on the node (CRM-pbx `loop_pbx/worker/fraud.py`); `services/fraud.py` (limits in the manifest, `PbxFraudAlert`, automatic suspend, the node's outbound switch) |
| Subscription hook | `services/billing.py` `reconcile()`, run at the end of `end_expired_subscriptions` (every 15 min): a lapsed subscription or a plan without `telephony` suspends outbound calls (blocked on the node at once, then recompiled); a renewal resumes them within one run. Only suspensions it made (`suspended_for_billing`) are lifted |
| Node migration | `services/migration.py` (`migrate_tenant`, `repush_node_state`); drain handling in `provisioning.desired_state` / `record_apply_result` |
| Platform console | `/api/v1/admin/telephony/`: `health/`, `nodes/`, `tenants/` (+ `PATCH`, `migrate-node/`, `repush/`), `numbers/?verified=false` (verification queue), `fraud-alerts/` (+ `resolve/`); CRM-admin-panel → **Telephony** |
| Plans | CRM-admin-panel plan modal: the `telephony` flag (off unless set) and the PBX quotas; a blank quota keeps the platform default |

**Fraud.** The node checks every outbound call against the tenant's limits (`settings.TELEPHONY_FRAUD_DEFAULTS`, overridden per tenant by the operator) and, with action `block`, blocks the tenant's outbound calls at once and cuts its live ones. The CRM records the alert and suspends the tenant; only a platform resume lifts it, and the resume must reach the node first (503 `pbx_unavailable` otherwise). A platform suspend blocks on the node too (best effort).

**Moving a tenant.** `migrate-node` drains the old node with a `remove` artifact, re-homes and recompiles the tenant, moves its gateway peers (they must be reconfigured on site and verified again) and re-pushes paused agents and any outbound block once the new node has applied the tenant. See contract §2, node migration.

**Verified:** unit tests (`test_telephony_saas.py`, `test_telephony_fraud.py`, `test_telephony_migration.py`, CRM-pbx `test_fraud.py`), live E2E (`test_fraud_detector_blocks_and_resume_lifts_it_live`, `test_migrate_node_live`), the real tenant UI (`crm_ui.mjs`: retention, storage meter, usage) and the real admin console (`CRM-pbx/devtools/webrtc/admin_ui.mjs`: nodes with tokens and the gateway link, fraud limits and legal hold, suspend, resume, move, provision, number verification, fraud alert resolution, plan flag and quotas, Arabic).

## Mobile (Phase 6)

| Piece | Where |
|---|---|
| Call history | crm_mobile `lib/screens/calls/pbx_calls_screen.dart` (drawer → **Phone calls**, shown when `GET /telephony/me/` answers): `GET /telephony/calls/` cursor pages, an **All / Missed** filter, call back, recordings loaded on tap (presigned, no CRM token) |
| Click-to-call | `lib/services/pbx_call_launcher.dart`: with an extension and `can_click_to_call`, the lead's call buttons offer **Call through the phone system** (rings the agent's own desk phone or browser first; the app has no softphone) or **Call from this phone** (`tel:`). `POST /telephony/calls/` with an `Idempotency-Key`; each refusal code has an EN/AR message, with the dialer as the fallback |
| Missed-call push | `telephony/services/missed.py`: an inbound call ending `missed`, `no_answer` or `abandoned` notifies the extension it was placed to, else the agents whose phones rang, else the queue's members, else the lead's assignee. Type `pbx_call_missed` (Android channel `tasks`), once per call (`PbxCall.missed_notified_at`). A tap opens the lead, or the history on **Missed** |
| Filter | `GET /telephony/calls/?status=` takes a comma-separated list (`missed,no_answer,abandoned`) |
| Declined vs missed | A softphone **Decline** sends 486, so the node records `busy`, which is not a missed outcome: no push, not in **Missed**. The web dock's ended card says "Declined"; history labels `busy` "Busy or declined" (web and mobile), because a customer rejecting an outbound call also ends `busy`. "Missed" means the caller gave up before anyone answered (`tests/test_telephony_missed.py`, CRM-project `utils/pbxCallOutcome.test.ts`) |

**Verified:** `tests/test_telephony_missed.py` (recipients, once-only, replay, isolated number in the Arabic body, the filter), crm_mobile `test/models/pbx_call_model_test.dart` (parsing, the dialer fallback, every refusal message in EN and AR, the push type) and `test/utils/stable_network_image_test.dart`.

**On a device (sixth session, Android emulator Pixel 9 / API 36, debug build against a local CRM, `CRM-pbx/devtools/webrtc/mobile_stack.mjs`, real FCM):**
- Phone calls, **All** and **Missed**: both list the right calls, and a recording plays (the player held audio focus for the recording's 2 s).
- Click-to-call with a **desk phone** extension: the chooser offers "Your desk phone (ext. 100) rings first, then the lead". The desk rang first, then the lead was called from the verified DID; the CRM call is answered and linked.
- `pbx_call_missed`: a real push ("Missed call from UI Lead"). A tap opens the lead, whose history shows the missed call. For an unknown number ("Missed call from +9647809999999", bidi-isolated), a tap opens Phone calls on **Missed**.
- `pbx_trunk_down`: a real push on channel `system` ("Phone line down"). A tap opens Home.
- Found, not fixed:
  - the Phone calls screen does not refresh when you come back to it, so a call that arrived meanwhile is missing until the screen is reopened. Realtime was off in this check, and production nudges it on the `telephony` slice.
  - with realtime unavailable, the app retries the sync socket about every second and logs an unhandled `WebSocketChannelException` each time.

**Also in the fifth session:**

- **Recording delete on the Calls page.** Calls → Phone system → History has a delete button next to each recording, for company admins and supervisors who manage settings. It asks first (`ConfirmDeleteModal`); a legal hold shows its 409 message.
- **Live-limit warnings.** The node emits `quota.warning` when an originate hits `concurrency_limit` or `cps_limit` (contract §6, at most one per tenant and quota every 5 min). The CRM validates the data and `GET /telephony/usage/` reports `hits_7d` / `last_hit_at` under `concurrent_calls` and `cps`. Settings → Phone System → Usage shows a banner.
- **Phone line alerts.** When a node reports a trunk or GSM gateway going `down`, or coming back from it, every active company admin gets `pbx_trunk_down` / `pbx_trunk_recovered` (push and inbox, Android channel `system`, EN/AR). A trunk's first report never alerts (`services/alerts.py`).
- **Caller id hardening.** Outbound routes set `CALLERID(all)` (name and number) to the verified number, so a phone's own display name never reaches the carrier. A forged From number is not identified at all (401). Verified live in `test_acceptance_rejections_and_missed_call_live`.
- **Read-only console.** The CRM-admin-panel **Telephony** page now opens for tenant managers (`can_manage_tenants`) read-only. Every action is hidden for them, matching the API (`CanManageTelephony` lets them read only).
- Verified in the real UIs: `crm_ui.mjs` (play and delete a recording, the limit banner) and `admin_ui.mjs` (the read-only console).

## Emergency calls (acceptance #10)

Decided 2026-10-17 (spec §14). `services/emergency.py`, the compiler's `_render_emergency`:

- **Routed.** Every tenant's phones can dial the numbers in `TELEPHONY_EMERGENCY_NUMBERS`. They go out the tenant's own trunk: the highest-priority usable outbound route's trunk and caller id, else the first verified number on a usable trunk. With neither, the compile warns and no line is rendered. The carrier or the gateway's SIM does the emergency routing and any location handling.
- **Never suspended.** The lines are compiled whatever the suspension (billing lapse, plan without telephony, fraud block, platform suspend), and never check the node's outbound switch. They are not recorded and play no notice.
- **Reserved.** No extension may use an emergency number (`validation.pbx_emergency_number`, EN/AR in CRM-project). An older one keeps its internal entry, but agents dialling it reach emergency services; the compile warns. Tenants numbering desks 100–199 lose 104, 112, 115 and 122.
- **Alerted.** The node reports a short code as `internal`; the CRM records the call as `outbound` and pushes `pbx_emergency_call` to every active company admin, once per call (`PbxCall.emergency_notified_at`). The push is on Android channel `system` and is never muted (no quiet hours); a tap opens Phone calls. The call never counts against `monthly_pbx_minutes` or the usage rollup's outbound minutes.
- **The node.** Nothing changes there: the fraud detector ignores destinations without `+`, and a fraud block's hang-up only touches outbound channels.
- **Still unconfirmed (2026-10-18).** The owner has not confirmed the list with carriers/regulator. The default stays `104,115,122,112,911` (commonly cited Iraqi set: police, civil defence, ambulance, GSM 112, 911). Do not invent a shorter list. Confirm before commercialization and set the env per market.
- **Verified:** `tests/test_telephony_emergency.py`, the compiler's emergency tests and snapshots, and live `test_emergency_number_bypasses_a_suspension_live`.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `TELEPHONY_QUEUE_ENABLED` | off | Run provisioning jobs on the dedicated `telephony` django-q cluster. When off, jobs run inline after commit; a compile takes a few ms and touches no node. |
| `TELEPHONY_WEBRTC_AUTH` | `static` | `static`: WebRTC extensions get a digest secret (dev, no Kamailio). `edge`: Kamailio authenticates WebRTC with ephemeral credentials, so no auth section is rendered. Browser and trunk endpoints are then `identify_by = header` (`X-Loop-Endpoint`, set only by Kamailio after its own check); trunks hop to Kamailio over loopback UDP with their transport in the contact (`\;transport=tls`). Proven on the dev edge profile (CRM-pbx `devtools/webrtc/sim_lab.mjs`). |
| `TELEPHONY_SIP_WS_URI` / `TELEPHONY_SIP_DOMAIN` | — | `wss://sip.<domain>/ws` and `sip.<domain>`, used for the softphone; the domain also becomes `from_domain` on WebRTC endpoints |
| `TELEPHONY_SIP_EDGE_PROXY` | — | Required in edge mode; private `host:port` used as Asterisk's Kamailio outbound proxy (for example `127.0.0.1:5065`) |
| `TELEPHONY_TURN_URLS` | — | Comma-separated `turn:`/`turns:` URLs |
| `PBX_SIP_EPHEMERAL_SECRET_FILE` / `PBX_TURN_SECRET_FILE` | — | Preferred restricted-file settings, shared with Kamailio (`auth_ephemeral`) and coturn (`use-auth-secret`); edge mode requires distinct values. Direct env names remain supported for compatibility. |
| `TELEPHONY_CREDENTIAL_TTL_SECONDS` | 28800 | Lifetime of browser credentials (5 min – 24 h) |
| `TELEPHONY_ALLOW_STATIC_WEBRTC` | off | `static` mode hands the browser the digest secret; this is refused unless `DEBUG` or this flag is on |
| `TELEPHONY_WORKER_TIMEOUT` | 5 | Seconds for CRM → worker calls |
| `TELEPHONY_APPLY_NUDGE` | off | After a compile, nudge the home node's applier through its worker |
| `TELEPHONY_APPLY_NUDGE_TIMEOUT` | 1.5 | Seconds for that nudge |
| `TELEPHONY_GATEWAY_TOKEN_TTL_HOURS` | 72 | Lifetime of a GSM gateway's one-time enrollment code |
| `TELEPHONY_FRAUD_MAX_CALLS_PER_HOUR` | 120 | Fraud default: outbound calls per hour |
| `TELEPHONY_FRAUD_MAX_NEW_DESTINATIONS_PER_HOUR` | 60 | Fraud default: numbers not called in 30 days, per hour |
| `TELEPHONY_FRAUD_MAX_INTERNATIONAL_SHARE` / `…_MIN_CALLS_FOR_SHARE` | 0.5 / 10 | Fraud default: share of calls outside the home prefixes, once that many calls were made |
| `TELEPHONY_FRAUD_HOME_PREFIXES` / `…_PREMIUM_PREFIXES` | `+964` / — | Comma-separated E.164 prefixes |
| `TELEPHONY_FRAUD_ACTION` | `block` | `block` stops the tenant's outbound calls and suspends it; `alert` only reports |
| `TELEPHONY_EMERGENCY_NUMBERS` | `104,115,122,112,911` | Comma-separated short codes routed for every tenant, even when suspended (see Emergency calls). **Unconfirmed** with carriers/regulator (2026-10-18); default unchanged |
| `INTEGRATION_ENCRYPTION_KEY` | (required) | Fernet key. It encrypts SIP and trunk secrets, the CRM→node token and artifact blobs. |

To turn the queue on in production:

1. Install a second qcluster unit that runs `Q_CLUSTER_NAME=telephony python manage.py qcluster`. It has one worker, configured in `settings.Q_CLUSTER["ALT_CLUSTERS"]`.
2. Then set `TELEPHONY_QUEUE_ENABLED=true`.

## Bringing a node online

1. In the admin API, `POST /api/v1/admin/telephony/nodes/ {"name": "pbx-1", "control_url": "http://10.77.0.2:7070"}`. The response shows `node_token` and `crm_token` **once**.
2. On the PBX VPS, put `node_token` in `/etc/loop-pbx/node-token` and start `loop-pbx-applier` (see `CRM-pbx/deploy/systemd/`).
3. Expose the internal API on the WireGuard address only, using `CRM-pbx/deploy/nginx-internal-telephony.conf.snippet`.
4. On the PBX VPS, store `sha256(crm_token)` in `/etc/loop-pbx/crm-token.sha256` and start `loop-pbx-worker`. It runs the bridge, the recording uploader and the call-control API on `10.77.0.2:7070`.
5. Provision a company with `POST /api/v1/admin/telephony/tenants/<company_id>/provision/` (Admin panel: Telephony → Companies → **Set up a company**, which lists only pilot-approved companies without a tenant). The tenant shows `pending` until the node reports `applied`, then `active`.

## Tests

```sh
.\.venv\Scripts\python.exe -m pytest tests/test_telephony_contract.py tests/test_telephony_compiler.py tests/test_telephony_provisioning.py tests/test_telephony_api.py tests/test_telephony_isolation.py tests/test_telephony_calls.py tests/test_telephony_click_to_call.py tests/test_telephony_routing.py tests/test_telephony_gateways.py tests/test_telephony_greetings.py tests/test_telephony_trunk_test.py tests/test_telephony_wallboard.py tests/test_telephony_saas.py tests/test_telephony_fraud.py tests/test_telephony_migration.py tests/test_telephony_billing.py tests/test_telephony_missed.py tests/test_telephony_emergency.py tests/test_object_storage.py
```

**Snapshots:** regenerate them after an intended compiler change with `UPDATE_SNAPSHOTS=1`.

**End to end** (needs Docker and `../CRM-pbx` with its venv). Thirteen tests (about 9 minutes):
- Compiled config is pushed through the real applier into the dev Asterisk, then the live isolation smoke test runs.
- Real SIP calls (devphones: desk phone 101 and a fake carrier) flow through the real worker into the live API. The test asserts answered inbound and outbound `PbxCall`s, lead-timeline entries and presence.
- Recorded calls with real audio (devphones send tones). The test asserts:
  - the notice played to the outside party;
  - MixMonitor files were uploaded by the worker to the dev S3 (SeaweedFS on `127.0.0.1:9000`);
  - `PbxRecording` rows exist and the timeline status is `ready`;
  - a CRM-presigned URL returns the same bytes (sha256), and the audio is not silent.
- Click-to-call through the API. The real worker control API rings desk phone 101, which answers, and the dialplan dials the lead (the carrier) with the verified DID. The test asserts an answered `crm`-origin call linked to the lead, and `X-Loop-Call-Id` on the agent's INVITE.
- Agent status and the apply-now nudge, with a long-running applier that polls only every 60 s:
  - pausing through the API flips the node's device state, and a carrier call to the DID gets 486 without ringing desk 101, ending `busy`;
  - after switching back to available, the next call is answered;
  - a new extension reaches the node on the nudge, in about 8 s on Windows (mostly `docker exec` round-trips), well inside the poll.
- Business hours, the IVR and a queue configured through the API. A call presses 1 and desk 101 answers through the queue. With today closed, the closed destination takes the call.
- GSM gateway enrollment. The real applier adds the peer (a recording stand-in for `wg` in dev), and the devphone carrier plays the gateway. The node reports it reachable, verify makes it READY, and it is reported down when the gateway goes away.
- A custom greeting uploaded through the API is heard by a caller (`--listen-tone`), and stops once the menu goes back to a platform prompt.
- The trunk test probes the contact in the node's live config (a devphone on the host's loopback, since the host cannot route to the container network) and reports it unreachable once that phone is gone.
- The fraud detector blocks a tenant over its hourly limit, cuts the live call, the dialplan refuses the next, and a platform resume lifts the block.
- `migrate-node`: the old node's applier removes the tenant and its gateway peer, the new node's applies it, and the paused agent is re-pushed to the new node.
- Spec §12 rejections (`test_acceptance_rejections_and_missed_call_live`):
  - a DID the tenant does not own;
  - its DID from an address its trunk does not allow;
  - a forged From number (401) and a forged display name (the lead sees the verified DID);
  - then an unanswered call becomes a `pbx_call_missed` notification for the desk's user.

- Emergency numbers (`test_emergency_number_bypasses_a_suspension_live`): a billing-suspended tenant with the node's outbound switch BUSY is refused an ordinary number, but `112` reaches the carrier from the verified DID; the CRM records an outbound call and alerts the admin.

The gateway test also checks the `pbx_trunk_down` alert. The full traceability is in `../CRM-pbx/docs/acceptance.md`.

```sh
docker compose -f ../CRM-pbx/deploy/dev/docker-compose.yml up -d --build
LOOP_PBX_E2E=1 .\.venv\Scripts\python.exe -m pytest tests/test_telephony_e2e.py -n 0   # serial: one shared Asterisk
```

**The real UI against the real PBX** (needs the dev stack, `npm install` in CRM-project and in `CRM-pbx/devtools/webrtc`). It seeds an isolated sqlite database (`CRM-pbx/deploy/dev/runtime/crm-ui/`, never your own database), then runs CRM-api-1 on :8765, the CRM-project vite dev server on :3100, and the node applier and worker. It drives the app in headless Chrome. About 6 minutes, 83 checks; set `UI_SHOTS=<dir>` for screenshots of failures. Its last section is acceptance #4 as **one call**: DID → IVR → queue → the browser answers → blind transfer to desk 101 → one recording uploaded by the worker (`LOOP_RECORDING_STORE=s3`) that spans the transfer → the lead's history with the recording `ready` → playback on Calls.

**Load test** (acceptance #8): `../CRM-pbx/scripts/load_test.py` and `../CRM-pbx/docs/load-test.md`. This covers the dev runs and how to run the 100-call / 5 CPS test on the PBX VPS.

```sh
cd ../CRM-pbx/devtools/webrtc && node crm_ui.mjs
```

**Route coverage:** `test_telephony_isolation.py::test_every_route_is_covered` fails when a telephony route is added without isolation coverage.
