# LOOP Mobile Softphone — Test Playbook

> Web browser softphone is **out of scope**. Web CRM uses AMI click-to-dial only. Configure softphone via **Integrations → PBX / ZYCOO** in the web admin; test calling on **crm_mobile**.

## Stage 0 — PBX prep

1. Pick test extension (e.g. `101`) not registered on CooCall.
2. Set SIP password in CooVox; enable WebRTC + TLS on that extension.
3. Confirm Remote Access proxy connected (`shatalarab.uae.zycoo.com`).
4. Verify LOOP PBX connector is online (screen pop / CDR).

## Stage 1 — Backend credentials API

1. Deploy CRM-api-1 with migration `0029_softphone_integration` applied.
2. Web admin → Integrations → PBX: enable **embedded softphone (mobile app)**, set domain/port/WSS.
3. Map test user → extension + SIP password + softphone enabled.
4. As mapped user, call:
   - `GET /api/integrations/pbx/softphone/config/?platform=android` (or `ios`)
5. **Pass:** JSON with `extension`, `sip_password`, `wss_uri`, `sip_uri`. Unmapped user → `no_extension`.

## Stage 2 — Mobile foreground

1. `flutter pub get` → install dev build on device; grant microphone.
2. Log in as mapped user; confirm SIP registered (overlay/status).
3. Outbound from lead profile → two-way audio.
4. Inbound with app open → answer works + screen pop if number matches lead.

## Stage 3 — Lock screen / killed app

1. Set `APNS_VOIP_*` env vars on CRM server (iOS).
2. Confirm device registered tokens: `POST /api/integrations/pbx/softphone/devices/`.
3. Kill LOOP app completely; call test extension from external phone.
4. **Pass:** CallKit / full-screen UI within ~5s; answer has audio.

## Stage 4 — Regression

1. Web click-to-dial still works for mapped users (AMI, not softphone).
2. Call reports and recording playback unchanged.
3. Screen pop FCM still navigates when softphone disabled on user.
4. CooCall off on test extension — no registration conflicts.

## Stage 5 — Mobile test matrix (manual QA)

Run on a **NAT network** (home WiFi or cellular hotspot), not the same LAN as the PBX.

|  | WiFi | Cellular |
|--|------|----------|
| **Foreground — iOS** | [ ] inbound rings, answer audio | [ ] inbound rings, answer audio |
| **Foreground — Android** | [ ] inbound rings, answer audio | [ ] inbound rings, answer audio |
| **Background — iOS** | [ ] CallKit within ~5s | [ ] CallKit within ~5s |
| **Background — Android** | [ ] full-screen incoming UI | [ ] full-screen incoming UI |
| **Killed — iOS** | [ ] VoIP push wakes app | [ ] VoIP push wakes app |
| **Killed — Android** | [ ] FCM data push wakes app | [ ] FCM data push wakes app |

Additional cases:

- [ ] Incoming call while device locked (iOS + Android)
- [ ] Incoming call while cellular call active (Android)
- [ ] Fresh install — push arrives before app ever opened
- [ ] Decline from CallKit ends native UI with no ghost audio
- [ ] Hangup from in-app ends CallKit UI
- [ ] Logout unregisters device (`DELETE /softphone/devices/`) and stops SIP

### Pre-flight (automated)

```bash
# Backend — config + WSS probe + extension readiness (run on server with prod DB)
.venv/Scripts/python.exe manage.py softphone_diagnose
.venv/Scripts/python.exe manage.py softphone_diagnose --username=agent_username

# Backend — APNs VoIP env check (run on server)
.venv/Scripts/python.exe manage.py softphone_apns_check

# Backend tests
.venv/Scripts/python.exe -m pytest integrations/tests/test_softphone_config.py integrations/tests/test_softphone_push.py integrations/tests/test_pbx_extension_update.py -q

# Mobile static analysis
cd crm_mobile && flutter analyze
```
