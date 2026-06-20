# LOOP CRM Embedded Softphone — Build & Review Checklist (v2, reconciled against SOFTPHONE_FEATURE_PROMPT.md)

**Scope:** CRM-api-1 (Django) · CRM-project (React + SIP.js) · crm_mobile (Flutter + sip_ua + CallKit). CRM-admin-panel is explicitly out of scope.
**PBX:** ZYCOO CooVox T100 @ shatalarab.uae.zycoo.com — TLS :5162, WSS wss://shatalarab.uae.zycoo.com:8089/ws
**Existing integration this builds on top of:** AMI-based CDR ingest, screen pop, click-to-dial, recordings, and a LAN Connector — already in production before this feature.

This is a review checklist against work that's already built, not a build-from-zero tutorial. Items reference your actual files/models/endpoints so it's directly actionable for Cursor.

---

## 0. The thing most likely to bite you: one extension, multiple clients

`UserPbxExtension` is one row per user — one extension number, one SIP password. Both `sipClient.ts` (web) and `softphone_service.dart` (mobile) register **the same credentials** over WSS, independently of each other, whenever each is active. The doc's framing — "one SIP registration per extension, CooCall must be off" — only names CooCall as the thing to turn off. Nothing in the doc says you've confirmed what happens when **your own** web client and **your own** mobile client try to hold that one extension's registration at the same time.

That's a real scenario, not an edge case: an agent has the CRM open in their browser at their desk (web softphone registered) and also has the mobile app installed and foregrounded (mobile softphone registers too, e.g. they background-checked something). Concretely test, before anything else:

- [ ] Register web (open the softphone bar) and, with that still live, open and foreground the mobile app on the **same** user/extension. Watch the PBX's extension status page — does it show one contact or two?
- [ ] With both apparently registered, place an inbound call to that extension. Does it ring both, ring only one (which one — first or most-recently-registered?), or do neither ring reliably?
- [ ] Let both sit idle for 2–3 REGISTER refresh cycles (so each client re-registers a couple of times) and watch for "flapping" — one client's badge going Registered → Unregistered → Registered as the other's refresh silently displaces it.
- [ ] Confirm the actual behavior, then make it deliberate: either (a) document "web and mobile are mutually exclusive per user, last one to register wins" and make the UI honest about that (the softphone bar should be able to show "active on another device," not just "offline"), or (b) if it turns out to genuinely break things, that's when a second per-user extension + ZYCOO's ring-group/One Number Station feature becomes worth the schema change — don't build that preemptively, but know it's the fallback if (a) tests badly.

This is the single highest-value thing to verify before broader rollout, because it's invisible in single-device testing (Stage 3 in your own test plan only covers mobile foreground in isolation) and only shows up once a real agent has both.

---

## 1. PBX-side (ZYCOO CooVox T100)

Most of this is already in `docs/ZYCOO_SOFTPHONE_SETUP.md` / your prerequisites list — flagging the parts worth double-checking rather than re-deriving the whole setup:

- [ ] Your prereq doc says "WebRTC enabled, TLS transport" per extension, while the platform table says both web and mobile actually connect via WSS :8089, not TLS-SIP :5162. Confirm precisely what "TLS transport" means at the extension level on this T100 firmware — if it's a separate underlying-transport field from the WebRTC/WS toggle, make sure it's not silently constraining something. If `build_softphone_config()` ever reads `PbxSettings.sip_transport` (which defaults to TLS and is "backend-only, not shown in UI") to construct anything returned to clients, double check it isn't leaking a TLS scheme/port into a config response that should be WSS-only.
- [ ] `PbxSettings` has `stun_server` / `turn_server` fields — confirm they're actually populated and a TURN server is actually reachable, not just present as empty strings. Test media (not just registration) from a real NAT'd network — home WiFi or a phone hotspot, not the office LAN where the PBX lives — since "registers fine, no audio" is the single most common WebRTC-softphone failure mode and won't show up testing from the same network as the PBX.
- [ ] Firewall: 5162 TCP/TLS, 8089 WSS, and the RTP UDP range are all open — your doc already lists this, just confirm it's been verified on the actual production firewall, not just a test box.
- [ ] CooCall disabled on test/pilot extensions specifically (not just "we turned it off once") — re-verify on every extension you onboard, since it's easy for a desk phone or a re-scanned QR code to silently re-enable Remote Extension/CooCall registration on an extension months later.
- [ ] Click-to-dial-via-AMI regression, specifically combined with softphone: today, click-to-dial presumably rings whatever's currently registered to that extension (desk phone, CooCall, etc.). Now that the same extension can also be registered via WebRTC, confirm AMI-originated click-to-dial still rings the right place for (a) an agent who has the web softphone open, and (b) an agent who hasn't enabled softphone at all and is still on a desk phone. This is exactly the kind of thing Stage 5 ("regression: click-to-dial... still work") needs to test *with the softphone simultaneously registered*, not in isolation.

---

## 2. Backend — CRM-api-1

### 2.1 Models & config delivery
- [ ] `UserSoftphoneDevice.platform` includes `web` alongside `ios`/`android`, with an `fcm_token` field that applies to it. Is this actually wired up anywhere — does `pbx_handler.py`'s RINGING handler send anything to `web`-platform devices via FCM Web Push? If yes: is there a service worker in CRM-project that shows a notification from it, which would partially solve "no inbound when tab closed"? If no: this looks like the lowest-effort way to close your biggest documented limitation (§10 in your doc), since the device-registration plumbing already exists — worth a deliberate decision rather than leaving it as unused schema.
- [ ] `GET /api/integrations/pbx/softphone/config/?platform=web|ios|android` — confirm it's actually scoped to the requesting user's own mapping (no way to pass another user's id and get their SIP password), and that the response never gets cached somewhere (CDN, browser HTTP cache) in a way that could serve stale or cross-user credentials.
- [ ] Encrypted `sip_password` on `UserPbxExtension` — confirm the encryption key management (where the key lives, how it rotates) is something other than "same secret as everything else, never rotated." Also confirm the PATCH endpoint that updates the password actually re-encrypts and that there's no path where it's briefly logged in plaintext (request logging middleware, DRF browsable API in non-prod, etc.).

### 2.2 Push pipeline (`softphone_push.py`, `pbx_handler.py`)
- [ ] Your own test plan (Stage 4) literally flags "needs `APNS_VOIP_*` on server" as a precondition for testing killed-state mobile calling — confirm these are actually set in the deployed environment (`APNS_VOIP_KEY_PATH`/`APNS_VOIP_KEY_CONTENT`, `APNS_VOIP_KEY_ID`, `APNS_VOIP_TEAM_ID`, `APNS_BUNDLE_ID`, `APNS_VOIP_USE_SANDBOX`) before treating Stage 4 as done, not just present in `.env.example`.
- [ ] `APNS_VOIP_USE_SANDBOX` — confirm this is `false` in production and that you have a way to actually test against sandbox APNs in staging without accidentally pushing sandbox-signed payloads to production devices (mismatched environment is a classic "push silently never arrives" bug).
- [ ] Short push expiration on the APNs VoIP send — a call push that arrives after the caller hung up is worse than none; and Apple tracks whether VoIP pushes actually result in a reported CallKit call, so consistently sending pushes for calls that get cancelled before delivery risks the app's VoIP push entitlement over time.
- [ ] FCM side: confirm it's a high-priority **data** message, not a notification message — notification-type FCM messages can be delayed/collapsed by Android in ways that defeat the "wake the phone for a ringing call" purpose.
- [ ] Token lifecycle: `last_registered_at` exists on `UserSoftphoneDevice` — is anything actually pruning devices that haven't refreshed in a long time, or reacting to APNs/FCM "this token is dead" responses? A growing pile of dead tokens being pushed to every call is wasted latency at best.
- [ ] The chain is `PBX → LAN Connector → CRM POST /connector/events → pbx_handler on RINGING → push`. That connector already exists for CDR/screen-pop/click-to-dial, where a missed or delayed event is a minor data-sync annoyance. It now also carries the *only* path to waking a killed mobile app — a dropped event here is a fully silent missed call, not a delayed CDR row. Confirm: does the LAN Connector retry a failed POST to `/connector/events`? Does losing connectivity between the LAN connector and the cloud CRM for a few minutes get alerted on with urgency matching "agents can't be reached," not just logged as a sync gap? This is worth treating as a different severity class than the rest of that connector's traffic.
- [ ] Push triggering should be idempotent/deduped by a stable call identifier — if the connector or AMI layer redelivers the same RINGING event, don't double-push (double-ring on a killed phone reads as a glitch and erodes trust in the feature fast).

### 2.3 Tests
You have `test_softphone_config.py` (3 tests) and `test_pbx_extension_update.py`. Worth adding, given the above:
- [ ] A test for device-token pruning / dead-token handling on the `UserSoftphoneDevice` registration endpoints.
- [ ] A test asserting `/softphone/config/` never returns another user's extension/password regardless of query params.
- [ ] A test (even if it's just a unit test around the dedupe key, not a full integration test) confirming a duplicate RINGING event for the same call doesn't fire two pushes.
- [ ] An authorization test on the extensions PATCH/DELETE endpoints confirming a non-admin user gets rejected.

---

## 3. Web — CRM-project

- [ ] `sip.js@0.21.2` is pinned exact — good. Before any future bump, read the changelog deliberately; SIP.js has had breaking API changes between minor versions historically, and `sipClient.ts` is written against this version's specific surface.
- [ ] Multi-tab leader election: even setting mobile aside, two browser tabs of the same user both running `useSoftphone.ts` will both try to register the same extension. Confirm there's a leader-tab mechanism (BroadcastChannel/SharedWorker) so only one tab actually holds the live SIP.js `UserAgent`/WebSocket, with the others reflecting state and proxying controls. If this doesn't exist yet, it's the same root problem as §0 but guaranteed to happen the first time someone opens a lead in a new tab while the softphone bar is up in the original one.
- [ ] `SoftphoneContext.tsx` / `useSoftphoneEnabled.ts` gate on company-enabled + user-mapped-with-password — confirm the gate re-evaluates if an admin disables softphone for a user *while they're actively registered*, rather than only checking at mount/login.
- [ ] Ringtone/incoming-call audio in `SoftphoneBar.tsx` — verify it actually plays on a cold tab load given browser autoplay restrictions; a silent incoming-call bar is as bad as no bar.
- [ ] Test on Safari explicitly, not just Chrome — historically the most likely browser to diverge on WebRTC audio device handling and codec negotiation.
- [ ] `sip_password` only ever lives in React state/memory for `sipClient.ts`'s lifetime — confirm `services/api.ts` isn't caching the config response (including password) into `localStorage` anywhere as a side effect of a generic API-caching layer.
- [ ] RTL: you've already got en/ar keys in `constants.ts` — specifically check the floating `SoftphoneBar` mirrors correctly in RTL (button order, mute/hangup icon placement) and that phone numbers don't get visually reversed by bidi text rendering inside it.
- [ ] `beforeunload` best-effort unregister on tab close — confirm it exists, and separately confirm the PBX-side cleanup (registration timeout) doesn't leave a stale "Registered" badge on a *different* tab/device for the Qualify interval after a tab is killed without that handler firing (crash, force-quit, power loss).

---

## 4. Mobile — crm_mobile

### 4.1 Version pinning — fix before this ships further
- [ ] `sip_ua: ^1.0.1` is a caret range, but your own notes document version-specific API shape for exactly 1.0.1 (`TransportType` only has `TCP`/`WS`, `buildCallOptions(true)` is positional not named, `stop()`/`mute()`/`unmute()` return void). A `flutter pub upgrade` pulling in a later 1.x with a changed API surface will silently break `softphone_service.dart` in ways `flutter analyze` won't necessarily catch if the new API still type-checks differently. Pin this exact (`sip_ua: 1.0.1`), or at minimum add a comment at the top of `softphone_service.dart` listing the API assumptions so a future bump is a deliberate, tested decision.
- [ ] Same logic for `flutter_callkit_incoming: 2.5.8` — you've documented a version-specific param name (`callingNotification` not `notification`). Pin exact.

### 4.2 Background & killed-state calling
- [ ] First action on receiving the VoIP push in `softphone_push_handler.dart` should be reporting the call to CallKit/`flutter_callkit_incoming` immediately — before any SIP correlation happens. iOS will kill the app if a VoIP push doesn't result in a prompt CallKit report, and a pattern of pushes that don't result in reported calls risks losing VoIP push entitlement. (You evaluated Siprix and went with sip_ua + flutter_callkit_incoming instead — the same "report first, correlate the SIP INVITE second" requirement applies regardless of which stack delivers it.)
- [ ] `ios/Runner/Info.plist` already has `voip`/`audio` background modes — confirm the Apple Developer portal side is also done: a VoIP Services certificate (or token-based key) matching `APNS_BUNDLE_ID`, and that you're sending to the `.voip` APNs topic specifically, not the regular alert topic.
- [ ] `android/app/src/main/AndroidManifest.xml` has the foreground service + phone permissions — for Android 14 specifically, confirm the foreground service is declared with type `phoneCall` and the corresponding `FOREGROUND_SERVICE_PHONE_CALL` permission, and that `USE_FULL_SCREEN_INTENT` is declared. Since this app's core functionality is calling, it should auto-qualify for the FSI permission grant on Play Store installs, but check `canUseFullScreenIntent()` at runtime anyway and have a heads-up-notification fallback — internal/MDM-distributed builds outside the Play Store may not get the automatic grant the same way.
- [ ] Test on a Samsung and a Xiaomi (or similarly aggressive OEM) device specifically, not just Pixel/stock Android — background-kill behavior varies a lot, and CooCall's own Play Store listing tells users to manually set battery to Unrestricted and grant "Display over other apps," which is a strong hint your app will need the same onboarding nudge.
- [ ] Answered-elsewhere / cancelled-before-answer: if §0's testing shows web and mobile can both be reachable, confirm that answering on web while mobile is mid-ring actually tears down the CallKit/Android incoming-call UI rather than leaving a ghost ringing screen.
- [ ] Decline/answer/hangup from the native CallKit/notification UI correctly drives `softphone_service.dart`'s SIP signaling (and the reverse — in-app hangup ends the native call UI). This is the most common source of "app says call ended, audio is still connected" bugs in sip_ua + flutter_callkit_incoming integrations.
- [ ] `permission_handler` flows for mic, notifications, and (Android 12+) `BLUETOOTH_CONNECT` each have a real recovery path if denied, not just a silent failure to ring/answer.

### 4.3 Test matrix
- [ ] Foreground/Background/Terminated × WiFi/Cellular × iOS/Android (the 12-cell grid)
- [ ] Incoming call while device locked
- [ ] Incoming call while a regular cellular call is also active (Android)
- [ ] Fresh install, app never opened, push arrives
- [ ] The §0 concurrent-web-and-mobile scenario, explicitly

---

## 5. Security

- [ ] Encrypted `sip_password` at rest (already noted in §2.1) — confirm the encryption key isn't reused from an unrelated secret and has a rotation story.
- [ ] WSS cert at :8089 is CA-signed, not self-signed — browsers tolerate click-through, sip_ua often won't, or will fail in a way that's hard to diagnose remotely.
- [ ] API tokens used to fetch `/softphone/config/` expire and are invalidated on logout/`DELETE /softphone/devices/` — a logged-out device shouldn't still be able to silently re-register.
- [ ] Push payloads carry only what's needed to ring (caller name/number, a call id) — fetch richer details from the API after wake rather than putting more PII in the APNs/FCM payload than necessary.

---

## 6. Before you commit

This is explicitly local WIP that "was stashed and restored" — that phrase alone is worth a deliberate pass before the first commit:

- [ ] `git status` and `git diff` reviewed end to end — confirm the restore actually brought back everything, with no leftover conflict markers, no orphaned `.orig`/`.rej` files, and the file list matches the "Key files quick reference" section of your own architecture doc (models.py, views/pbx.py, serializers_pbx.py, services/softphone_config.py, softphone_push.py, pbx_handler.py, migrations/0029_softphone_integration.py on the API side; SoftphoneBar.tsx, sipClient.ts, PbxSettingsForm.tsx, useSoftphone.ts, useSoftphoneEnabled.ts on web; softphone_service.dart, softphone_push_handler.dart, softphone_overlay.dart on mobile).
- [ ] Grep for actual secret values (SIP passwords, APNs key content, FCM server keys) before committing — don't rely on `.gitignore` having caught everything after a stash/restore cycle.
- [ ] `manage.py check` and `makemigrations --check --dry-run` clean (migration `0029_softphone_integration.py` and the notifications `0013_softphone_incoming_call_type.py` both present and consistent with current models).
- [ ] `flutter analyze` already passes — also confirm no debug-level SIP tracing (sip_ua/SIP.js verbose logs, which can include auth headers) is on by default in release builds.
- [ ] Don't edit the plan file, per your own constraint, if Cursor or anyone else generates one during this work.

---

## 7. Open questions specifically worth resolving (narrowed from the generic version)

- [ ] §0's concurrent web+mobile registration behavior — confirmed by actual test, not assumption.
- [ ] Whether `UserSoftphoneDevice.platform == 'web'` is live functionality or unused schema, and if unused, whether finishing it is the cheapest fix to the "tab must be open" limitation.
- [ ] Whether `APNS_VOIP_*` is actually configured in the deployed environment, per your own Stage 4 caveat.
- [ ] Whether AMI-originated click-to-dial has been tested *while* the same extension is also softphone-registered, not just in isolation.
