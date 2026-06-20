# CURSOR TODO — LOOP Mobile Softphone (ZYCOO CooVox)

**How to use this file:** Work top to bottom, phase by phase. Every item is either an
**AUDIT** (open the file, confirm the described behavior actually exists and is correct —
fix it if it doesn't, report the discrepancy if it does something different) or a
**BUILD** (implement what's missing). Don't skip AUDIT items just because the feature
prompt says something is "implemented" — confirm it, don't trust it.

Read `CRM-api-1/docs/SOFTPHONE_FEATURE_PROMPT.md` and
`docs/SOFTPHONE_TEST_PLAYBOOK.md` first for full context.

## Global constraints (apply to every task below)

- Do **not** add a web SIP client (sip.js, SoftphoneBar, SoftphoneContext, useSoftphone,
  sipClient.ts) unless explicitly asked. Web stays AMI click-to-dial only.
- Only one SIP registration per extension. CooCall must be off on any extension LOOP
  mobile uses.
- Mobile registers over **WSS**, not TLS SIP on port 5162 (`sip_ua` 1.0.1 limitation).
- Python in `CRM-api-1`: always `.\.venv\Scripts\python.exe`.
- Do **not** edit any `.plan.md` files.
- Never let `sip_password` reach logs, crash reports, analytics events, or push payloads.
  It may only ever appear in the one authenticated config API response and in secure
  on-device storage.
- PBX: `shatalarab.uae.zycoo.com`, TLS `5162`, WSS `wss://shatalarab.uae.zycoo.com:8089/ws`.

---

## Phase 0 — Repo & environment audit (read-only, no edits yet)

- [ ] **0.1** Open every file listed in `SOFTPHONE_FEATURE_PROMPT.md §11`. Build a status
  table: exists / missing / partial, for each. Don't change anything yet — this is the
  baseline.
- [ ] **0.2** In `crm_mobile`, run `flutter pub get` then `flutter analyze`. Paste full
  output. Confirm the "clean" claim in the feature doc is still true after `pub get`
  (lockfile drift can break this).
- [ ] **0.3** In `CRM-api-1`, run
  `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`
  to confirm migration `0029_softphone_integration` matches the current model state with
  zero drift.
- [ ] **0.4** Grep `CRM-project` for `sip.js`, `SoftphoneBar`, `SoftphoneContext`,
  `useSoftphone`, `sipClient.ts`, and any softphone call button on lead pages. Confirm
  true deletion (not just unimported/dead code still sitting in the tree).
- [ ] **0.5** Grep `CRM-project/src/services/api.ts` for `getSoftphoneConfigAPI` /
  `registerSoftphoneDeviceAPI`. Confirm removed; confirm `updatePbxExtensionAPI` is the
  only softphone-related export remaining there.

---

## Phase 1 — Backend (`CRM-api-1`)

### Models & migration
- [ ] **1.1** Open `integrations/models.py`. Confirm `PbxSettings`,
  `UserPbxExtension`, `UserSoftphoneDevice` match the field list in the feature doc.
- [ ] **1.2** Confirm `sip_password` on `UserPbxExtension` uses an encrypted field
  (e.g. Fernet / `django-encrypted-model-fields`) with the key sourced from an env var /
  secrets manager — **not** hardcoded, **not** derived from `SECRET_KEY`.
- [ ] **1.3** Confirm `sip_password` is **write-only** on any serializer used for admin
  list/GET views of extensions (`/api/integrations/pbx/extensions/`). Admin should be able
  to *set* it but not read it back in plaintext on a later GET. Fix if it's currently
  returned in list responses.

### API endpoints
- [ ] **1.4** Audit every endpoint in `views/pbx.py` for: authentication required,
  company-scoped queryset (a user from Company A must never retrieve Company B's PBX
  config or another user's extension), and correct HTTP method restrictions.
- [ ] **1.5** Confirm `GET /api/integrations/pbx/softphone/config/?platform=ios|android`
  returns `extension`, `sip_password`, `wss_uri`, `sip_uri`, and **TURN credentials**
  (`turn_server` + username/credential, see 1.10). Confirm unmapped user → `no_extension`
  response, not a 500 or empty 200.
- [ ] **1.6** Confirm `POST/DELETE /api/integrations/pbx/softphone/devices/` correctly
  upserts/removes `UserSoftphoneDevice` rows keyed by user+platform, and that DELETE is
  actually called from mobile on logout (cross-check with Phase 3.12).

### Push
- [ ] **1.7** Open `services/softphone_push.py`. Confirm the push payload contains only
  caller-facing metadata (caller number, caller name if matched, call UUID, lead ID if
  matched) and **never** `sip_password` or other credentials.
- [ ] **1.8** Confirm the APNs VoIP push sets `apns-push-type: voip` and an
  `apns-topic` of `<APNS_BUNDLE_ID>.voip` (not a plain alert push) — this is a common
  silent-failure point with token-based (.p8) APNs auth specifically. Confirm
  `apns-priority: 10`.
- [ ] **1.9** Confirm the Android FCM push is sent with `priority: high` (and
  `content-available`/data-only payload so it can wake the app to show the CallKit-style
  full-screen UI rather than relying on a system tray notification).
- [ ] **1.10** Add ephemeral/time-limited TURN credentials if not already present (static
  long-lived TURN secrets are a known abuse vector). If `PbxSettings.turn_server` is a
  fixed static-credential TURN server, flag this as a follow-up rather than blocking —
  but document it.
- [ ] **1.11** Add cleanup logic: on APNs `BadDeviceToken` / FCM `NotRegistered`/`Invalid
  Argument` responses, delete the corresponding `UserSoftphoneDevice` row so stale tokens
  don't keep getting retried.

### Race-condition instrumentation (see Phase 5)
- [ ] **1.12** In `services/pbx_handler.py`, add a structured log line (with a shared
  `call_id`/correlation id) at: connector RINGING event received, push dispatch
  attempted, push dispatch result (success/failure + provider response). This is required
  before Phase 5 testing can produce real numbers.

### Offboarding
- [ ] **1.13** Add an admin action or signal: when a `UserPbxExtension` is unmapped from a
  user, deactivated, or the user is deactivated, automatically (a) clear/rotate
  `sip_password` server-side note for the admin to update on the PBX, and (b) delete that
  user's `UserSoftphoneDevice` rows so old devices stop receiving push for that extension.

### Tests
- [ ] **1.14** Extend `integrations/tests/test_softphone_config.py`:
  - unmapped user → `no_extension`
  - user with `softphone_enabled=False` → correct disabled response
  - `platform=ios` vs `platform=android` return different/correct URI shapes
  - `sip_password` never appears in any non-authenticated or list response
- [ ] **1.15** Extend `integrations/tests/test_pbx_extension_update.py` to cover the
  write-only password behavior from 1.3 and the offboarding flow from 1.13.
- [ ] **1.16** Run `.\.venv\Scripts\python.exe manage.py test integrations`. Paste full
  output. All green before moving on.

---

## Phase 2 — Web admin (`CRM-project`) — audit only, no new calling UI

- [ ] **2.1** Confirm no call/dial button was reintroduced anywhere outside the existing
  AMI click-to-dial path (`LeadContactPhone` + `usePbxDialEnabled`).
- [ ] **2.2** Open `components/integrations/PbxSettingsForm.tsx`. Confirm every form field
  key matches the backend serializer field names exactly (silent key mismatches are the
  most common bug in admin forms like this).
- [ ] **2.3** Confirm the "edit extension" (pencil) flow does **not** send an empty-string
  `sip_password` on save-without-changing-password (that would silently blank the
  extension's password on the PBX side). If the password field is left untouched, the
  PATCH payload should omit the field entirely.
- [ ] **2.4** Manually test end-to-end against a running backend: enable softphone, set
  SIP domain/port/WSS, map a user → extension + password, confirm the "(already mapped)" /
  "(مربوط مسبقاً)" label appears correctly for already-mapped users in the dropdown.
- [ ] **2.5** Regression: confirm AMI click-to-dial still works unchanged for a mapped
  user (Phase 6.4 will retest this formally).

---

## Phase 3 — Mobile (`crm_mobile`)

### iOS — PushKit native wiring (this is native Swift, separate from any Dart code)
- [ ] **3.1** Open `ios/Runner/AppDelegate.swift`. Confirm it implements
  `PKPushRegistryDelegate` and `CallkitIncomingAppDelegate`.
- [ ] **3.2** Confirm `PKPushRegistry` is created in `didFinishLaunchingWithOptions` with
  `desiredPushTypes = [.voIP]`.
- [ ] **3.3** Confirm `pushRegistry(_:didUpdate:for:)` both (a) calls
  `SwiftFlutterCallkitIncomingPlugin.sharedInstance?.setDevicePushTokenVoIP(deviceToken)`
  **and** (b) forwards the token up to Dart so it can be sent to
  `POST /api/integrations/pbx/softphone/devices/`.
- [ ] **3.4** Confirm `pushRegistry(_:didReceiveIncomingPushWith:for:completion:)` calls
  `showCallkitIncoming(data, fromPushKit: true)` for **every** VoIP push received,
  including stale/duplicate/test ones. Apple can revoke VoIP push entitlement for apps
  that receive a VoIP push without reporting a call — there is no "silently ignore"
  option. If a push turns out to be stale (see 3.8 race handling), end the reported call
  immediately rather than not reporting it at all.
- [ ] **3.5** Confirm `pushRegistry(_:didInvalidatePushTokenFor:)` clears the token both in
  the plugin and via a DELETE/clear call to the backend.
- [ ] **3.6** Confirm `Info.plist` has `UIBackgroundModes` containing `voip` and
  `remote-notification`, Push Notifications capability is enabled on the bundle ID in the
  Apple Developer portal, and a VoIP Services Certificate is installed/referenced for the
  `APNS_VOIP_*` env vars to actually work.
- [ ] **3.7** Audio handoff: confirm `RTCAudioSession` manual-audio mode is configured so
  CallKit (not WebRTC directly) owns the audio session activation on answer. Manually test:
  answer a call via the lock screen, confirm two-way audio within a few seconds — this is
  the single most common "CallKit answers, no audio" bug in this exact stack.

### Android — native wiring
- [ ] **3.8** Open `android/.../MainActivity`. Confirm it registers a
  `CallkitEventCallback` (`FlutterCallkitIncomingPlugin.registerEventCallback`) handling
  `ACCEPT`/`DECLINE`, and unregisters it in `onDestroy`.
- [ ] **3.9** Confirm `AndroidManifest.xml` declares: `INTERNET`,
  `USE_FULL_SCREEN_INTENT`, runtime `POST_NOTIFICATIONS` request (Android 13+/API 33+),
  and a foreground service type appropriate for calling if a foreground service is used
  (3.13).
- [ ] **3.10** Confirm `proguard-rules.pro` keeps `flutter_callkit_incoming`, `sip_ua`,
  and `flutter_webrtc` classes (release builds silently break this stack without it).
- [ ] **3.11** Android 14+ full-screen intent: add a runtime
  `notificationManager.canUseFullScreenIntent()` check. If false, route the user to
  `ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT` with a short in-app explainer, and fall back
  to a high-priority heads-up notification if the user declines. (Operational follow-up,
  not code: complete the Play Console FSI "calling app" declaration — note this in the
  PR description, it can't be done from Cursor.)

### Security
- [ ] **3.12** Confirm `sip_password` returned from the config endpoint is written to
  `flutter_secure_storage` (Keychain on iOS / Keystore-backed on Android), **never** to
  `SharedPreferences`, a plain Hive box, or any logger (including debug `print`/`log.d`
  calls — grep for `sip_password` across `lib/` to confirm zero log sites).
- [ ] **3.13** Confirm push payload data shown in any local notification/CallKit caller
  text never includes raw credentials — only caller name/number.

### Race-condition handling (the core risk identified in research)
- [ ] **3.14** On cold start triggered by a VoIP/FCM push, show the CallKit incoming-call
  UI **immediately and synchronously**, before SIP re-registration completes — don't wait
  on REGISTER to show the ring. Then register in the background.
- [ ] **3.15** Add a timeout (suggest 4–6s, tune after Phase 5 measurements) from
  "CallKit shown" to "INVITE received." If it expires without the matching INVITE arriving,
  end the CallKit call with a clear reason (e.g. `CXCallEndedReason.failed` /
  `missedCall`) instead of leaving a ringing screen with nothing behind it.
- [ ] **3.16** De-dupe: key incoming CallKit UI by the PBX call-id/UUID in the push
  payload. If a retransmitted push arrives for a call-id already shown, ignore it instead
  of creating a duplicate CallKit entry.

### Network resilience
- [ ] **3.17** Confirm `softphone_service.dart` listens for connectivity changes
  (`connectivity_plus` or equivalent) during an active call and triggers ICE restart on
  the `RTCPeerConnection` on Wi-Fi↔cellular handoff, rather than just silently dropping
  audio.

### Backgrounding strategy (Android)
- [ ] **3.18** Confirm a foreground service keeps the WSS SIP registration alive while the
  app is backgrounded-but-not-killed (distinguish this from a fully force-stopped app,
  which no foreground service or push can revive — that's OS policy, not a code bug).
- [ ] **3.19** Add a one-time onboarding screen (post-login or in settings) that detects
  Samsung/Xiaomi/Huawei-style OEMs and prompts the user to disable battery optimization /
  enable autostart for the app, since these OEMs force-stop apps on swipe-away by default
  and no app-side code can override that.

### Lifecycle
- [ ] **3.20** Confirm `DELETE /api/integrations/pbx/softphone/devices/` is called on
  logout, the SIP UA is stopped cleanly (`stop()`), and secure storage is cleared.
- [ ] **3.21** Add debug-only timestamp logging around: push received → CallKit shown →
  SIP REGISTER sent → 200 OK received → INVITE received → call answered. Tag with the same
  `call_id` used in backend logging (1.12) so the two can be correlated in Phase 5.
- [ ] **3.22** Re-run `flutter analyze` after all of the above. Must stay clean.

---

## Phase 4 — ZYCOO CooVox PBX (operational checklist, not code)

- [ ] **4.1** Confirm Intrusion Auto Detection/Prevention, Extension Permit IP, and
  Geo-IP policy are actually **enabled** on the Remote Access proxy — not just available
  in the firmware. Document with a screenshot or config export.
- [ ] **4.2** Check the per-extension (or ring group) no-answer/ring timeout. Compare
  against the p95 wake-to-answer latency measured in Phase 5; extend the timeout if it's
  shorter than that, or route killed-app extensions through a ring group/queue with a
  longer retry window.
- [ ] **4.3** Confirm both STUN and TURN are populated in `PbxSettings` and reachable from
  outside the office LAN — test from a phone on cellular data with Wi-Fi off, not just
  office Wi-Fi.
- [ ] **4.4** Re-verify CooCall is fully decommissioned on every extension LOOP mobile
  uses: App Extension disabled in the extension's settings, and the stale token row
  removed from **Addons → SoftPhone → List** if still present.

---

## Phase 5 — Timing instrumentation & race-condition validation

- [ ] **5.1** With 1.12 and 3.21 logging in place, run **10 real test calls** to a fully
  killed-app device — mix of Wi-Fi and cellular, both iOS and Android — and record the
  full timeline for each: RINGING event → push dispatched → push received on device →
  CallKit shown → SIP REGISTER → 200 OK → INVITE received → answered → first audio.
- [ ] **5.2** Compute p50 and p95 total "wake to answer" latency from this data.
- [ ] **5.3** Compare p95 against the PBX no-answer timeout from 4.2. If they're close,
  extend the PBX timeout, shorten cold-start time (e.g. trim init work before
  `showCallkitIncoming`), or accept and document the miss rate.
- [ ] **5.4** Write the results to `docs/SOFTPHONE_LATENCY_RESULTS.md` (a real doc file,
  not `.plan.md`) with raw numbers, not just a pass/fail — this is the evidence that Stage
  3 of the test playbook actually works under real conditions, not just in a quiet office
  with the app already warm.

---

## Phase 6 — Testing (executes and extends `SOFTPHONE_TEST_PLAYBOOK.md`)

- [ ] **6.1** Stage 0–2 exactly as documented. Log pass/fail for each numbered step.
- [ ] **6.2** Stage 3, on **real** Samsung and Xiaomi (or Huawei) hardware with **default**
  (non-developer) battery settings — not only a Pixel or emulator. Before/after each test,
  run `adb shell dumpsys package <pkg> | grep stopped` to confirm whether the app was in
  the force-stopped state, and note it.
- [ ] **6.3** Stage 3, iOS: explicitly test the "every push reports a call" rule from
  3.4 — kill the app, trigger a push for a call-id that's already ended, confirm CallKit
  briefly shows then auto-ends (per 3.15) rather than nothing happening or the app
  crashing/getting flagged.
- [ ] **6.4** Stage 4 regression exactly as documented. Log pass/fail.
- [ ] **6.5** New — Security pass: capture mobile network traffic and local logs during a
  full login → config fetch → call cycle; confirm `sip_password` appears **only** once, in
  the single config API response body, nowhere else (not in logs, not in the push
  payload, not in crash reporting breadcrumbs).
- [ ] **6.6** New — Network resilience: start a call on Wi-Fi, switch to cellular mid-call
  (and back), confirm audio survives via ICE restart (3.17) or the call fails cleanly with
  a clear UI state rather than hanging silently.

---

## Phase 7 — Commit & handoff

- [ ] **7.1** Once Phases 1–6 pass, commit `CRM-api-1` changes on a feature branch. Do not
  touch any `.plan.md` file.
- [ ] **7.2** Commit `crm_mobile` changes on a feature branch.
- [ ] **7.3** Open PRs referencing this checklist and the Phase 5/6 results.
- [ ] **7.4** Update the "Current state" section of `SOFTPHONE_FEATURE_PROMPT.md` to
  reflect what's now committed vs. still WIP (this is a regular doc, not `.plan.md`, so
  it's fine to edit).
