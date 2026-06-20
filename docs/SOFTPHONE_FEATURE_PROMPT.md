# LOOP CRM Mobile Softphone (ZYCOO CooVox) — Full Feature Context for AI Assistants

Use this document as the single source of context when working on, debugging, testing, or extending the LOOP **mobile** embedded softphone feature.

> **Scope decision (current):** Web browser calling was **removed** from `CRM-project`. The web CRM keeps **click-to-dial (AMI)** only. Softphone server settings and extension mapping remain in the web **admin UI** because the mobile app reads them from the backend.

---

## 1. Product goal

LOOP CRM already integrates with **ZYCOO CooVox T100** PBX for:
- CDR ingest, screen pop, click-to-dial (AMI), call recordings, LAN connector

**This feature adds an embedded SIP/WebRTC softphone in the mobile app** so agents can make and receive calls from `crm_mobile`:
- **Foreground** — register over WSS, dial from lead profile, answer inbound
- **Lock-screen / killed app** — CRM VoIP push → CallKit (iOS) / full-screen incoming (Android) → re-register → answer

**Customer PBX:** `shatalarab.uae.zycoo.com` (TLS port `5162`, WSS `wss://shatalarab.uae.zycoo.com:8089/ws`)

**Reference competitor:** ZYCOO CooCall (PBX-native push). ZYCOO does not expose a public third-party push token API, so LOOP uses **CRM-mediated VoIP push** until ZYCOO partnership.

---

## 2. Repository layout

| Repo | Role |
|------|------|
| `CRM-api-1` | Django backend: models, APIs, encrypted SIP passwords, VoIP/FCM push on ringing |
| `CRM-project` | React web CRM: **PBX admin only** (softphone server + extension mapping). **No SIP client.** Click-to-dial unchanged. |
| `crm_mobile` | Flutter app: sip_ua + flutter_callkit_incoming, push wake-up, in-call UI |
| `CRM-admin-panel` | **Not touched** |

---

## 3. Architecture

```
Registration (app open or after push wake):
  Mobile app → PBX: SIP REGISTER (ext + password) via WSS
  PBX → Mobile: 200 OK

Outbound from lead profile:
  Mobile → PBX: SIP INVITE customer_number
  PBX → Mobile: RTP audio (WebRTC/SRTP)

Inbound when app killed:
  PBX → LAN Connector → CRM POST /connector/events
  CRM pbx_handler on RINGING → APNs VoIP (iOS) + high-priority FCM (Android)
  Mobile CallKit UI → SIP REGISTER → answer INVITE

Web CRM (unchanged for calling):
  Lead page → AMI click-to-dial (rings registered endpoint / desk phone)
```

### Client stack

| Platform | SIP transport | Incoming when killed | Library |
|----------|---------------|----------------------|---------|
| **Web** | N/A — no softphone | N/A | AMI click-to-dial only |
| **Mobile** | WSS (sip_ua 1.0.1 has no native TLS SIP on 5162) | Yes via CRM push | `sip_ua` + `flutter_callkit_incoming` |

**Critical constraint:** Only **one SIP registration per extension**. CooCall must be off before LOOP mobile uses the same extension.

---

## 4. Backend (`CRM-api-1`)

### Models (`integrations/models.py`)

**PbxSettings** (company-level):
- `softphone_enabled`, `sip_domain`, `sip_port`, `sip_transport`, `wss_uri`, `stun_server`, `turn_server`

**UserPbxExtension** (per-user):
- `sip_password` (encrypted), `softphone_enabled`

**UserSoftphoneDevice**:
- `user`, `platform` (ios/android/web), `fcm_token`, `voip_token`, `last_registered_at`

**Migration:** `integrations/migrations/0029_softphone_integration.py`

### API endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/integrations/pbx/softphone/config/?platform=ios\|android` | SIP creds + URIs for mobile |
| POST | `/api/integrations/pbx/softphone/devices/` | Register FCM + VoIP tokens |
| DELETE | `/api/integrations/pbx/softphone/devices/` | Unregister on logout |
| GET/POST | `/api/integrations/pbx/extensions/` | List / create extension mappings |
| PATCH/DELETE | `/api/integrations/pbx/extensions/{id}/` | Edit / remove mapping |

### Services

- `integrations/services/softphone_config.py` — `build_softphone_config()`, `user_softphone_ready()`
- `integrations/services/softphone_push.py` — APNs VoIP + high-priority FCM
- `integrations/services/pbx_handler.py` — on `RINGING`, sends softphone push + screen pop

### Notifications

- `SOFTPHONE_INCOMING_CALL` (`notifications/migrations/0013_softphone_incoming_call_type.py`)
- `send_softphone_call_push()` in `notifications/services.py`

### Env vars (iOS lock-screen)

```env
APNS_VOIP_KEY_PATH=
APNS_VOIP_KEY_CONTENT=
APNS_VOIP_KEY_ID=
APNS_VOIP_TEAM_ID=
APNS_BUNDLE_ID=com.loopcrm.mobile
APNS_VOIP_USE_SANDBOX=false
```

### Tests

- `integrations/tests/test_softphone_config.py`
- `integrations/tests/test_pbx_extension_update.py`

---

## 5. Web (`CRM-project`) — admin only

**Removed (do not re-add without explicit request):**
- `sip.js`, `SoftphoneBar`, `SoftphoneContext`, `useSoftphone`, `sipClient.ts`
- Softphone call buttons on leads
- `getSoftphoneConfigAPI` / `registerSoftphoneDeviceAPI` in `services/api.ts`

**Still present (needed for mobile):**
- `components/integrations/PbxSettingsForm.tsx`:
  - **Enable embedded softphone (mobile app)** checkbox
  - Mobile softphone server fields: SIP domain, port, WSS URI, STUN/TURN
  - User extension mapping: user, extension, SIP password, softphone toggle
  - **Edit extension** (pencil): reassign user, change number/password, toggle softphone
  - Softphone status column; user dropdown shows `(already mapped)` / `(مربوط مسبقاً)`
- `updatePbxExtensionAPI` (PATCH) in `services/api.ts`
- Web calling: **PBX click-to-dial only** via `LeadContactPhone` + `usePbxDialEnabled`

**Admin UI:** Integrations → PBX / ZYCOO

---

## 6. Mobile (`crm_mobile`)

### Dependencies

- `sip_ua: ^1.0.1`, `flutter_webrtc`, `flutter_callkit_incoming`, `permission_handler`

### Key files

- `lib/services/softphone_service.dart`
- `lib/services/softphone_push_handler.dart`
- `lib/widgets/softphone/softphone_overlay.dart`
- `lib/services/api_service.dart` — `getSoftphoneConfig()`, `registerSoftphoneDevice()`
- `lib/screens/home/home_screen.dart`, `lib/screens/leads/lead_profile_screen.dart`

### sip_ua 1.0.1 notes

- `addSipUaHelperListener` / `removeSipUaHelperListener`
- `TransportType.WS` with `wss://` URL (no WSS/TCP_TLS enum)
- `buildCallOptions(true)` positional; `stop()`/`mute()` return void
- API config prefers WSS for mobile (`softphone_config.py`)

### flutter_callkit_incoming 2.5.8

- Use `callingNotification`, not `notification`

---

## 7. PBX prerequisites

See `docs/ZYCOO_SOFTPHONE_SETUP.md`. Summary:
- Remote Access on, domain `shatalarab.uae.zycoo.com`, TLS 5162
- Per extension: SIP password, WebRTC on, TLS; CooCall off
- Firewall: 5162, 8089 WSS, UDP RTP
- LAN connector stays running

---

## 8. Configuration checklist (mobile testing)

1. `.\.venv\Scripts\python.exe manage.py migrate` (CRM-api-1)
2. Deploy API
3. **Web admin** → Integrations → PBX / ZYCOO:
   - Enable PBX + **Enable embedded softphone (mobile app)**
   - SIP domain, port `5162`, WSS `wss://shatalarab.uae.zycoo.com:8089/ws`
   - Map user → extension + SIP password + softphone enabled
4. **Mobile:** `flutter pub get` → `flutter run` → log in as mapped user
5. Verify `GET /api/integrations/pbx/softphone/config/?platform=android` (or ios)
6. Foreground: dial from lead, answer inbound
7. Lock-screen: set `APNS_VOIP_*`, kill app, call extension

---

## 9. Testing stages

See `docs/SOFTPHONE_TEST_PLAYBOOK.md` (mobile-focused).

| Stage | What to verify |
|-------|----------------|
| 0 | PBX prep, CooCall off |
| 1 | Credentials API returns JSON for mapped user |
| 2 | Mobile foreground: register, outbound, inbound |
| 3 | Mobile killed: VoIP push → CallKit (~5s) |
| 4 | Regression: web click-to-dial, screen pop, recordings |

---

## 10. Known limitations

- **No web softphone** — intentional; web uses AMI click-to-dial
- **One registration per extension** — CooCall conflicts
- **sip_ua** — no native TLS SIP on 5162; uses WSS
- **iOS lock-screen** — requires APNS VoIP cert on CRM server
- **ZYCOO native push** — research track (`docs/ZYCOO_PUSH_RESEARCH.md`)
- Work is on feature branch `feature/mobile-softphone` (API + mobile); web admin unchanged except audit-verified PBX form
- Real-device latency validation pending — fill `docs/SOFTPHONE_LATENCY_RESULTS.md` after Phase 5/6 on hardware

---

## 11. Key files quick reference

| Repo | Files |
|------|-------|
| CRM-api-1 | `integrations/models.py`, `views/pbx.py`, `services/softphone_*.py`, `pbx_handler.py` |
| CRM-project | `components/integrations/PbxSettingsForm.tsx`, `services/api.ts` (extension APIs only) |
| crm_mobile | `lib/services/softphone_*.dart`, `lib/widgets/softphone/` |

---

## 12. Handoff prompt for Claude

Copy the block below and fill in **My task**.

---

**Context:** LOOP CRM mobile softphone for ZYCOO CooVox. Read `CRM-api-1/docs/SOFTPHONE_FEATURE_PROMPT.md`.

**Repos:**
- `CRM-api-1` — Django backend (softphone APIs, push, models)
- `CRM-project` — web admin for PBX/softphone settings only; **no web SIP client**
- `crm_mobile` — Flutter softphone (sip_ua + CallKit)

**PBX:** `shatalarab.uae.zycoo.com:5162`, WSS `wss://shatalarab.uae.zycoo.com:8089/ws`

**Current state:** Backend hardened (offboarding, dead VoIP tokens, TURN ephemeral creds, structured push logging, 27 integration tests green). Mobile has PushKit/CallKit native wiring, secure SIP password storage, race handling (dedupe + 5s INVITE timeout), ICE restart on network handoff, Android FSI check, OEM battery onboarding. Web softphone **deleted**; web keeps AMI click-to-dial. `flutter analyze` clean on mobile. Device latency playbook pending — see `docs/SOFTPHONE_LATENCY_RESULTS.md`.

**My task:** [e.g. test mobile foreground on device, fix lock-screen incoming, deploy API, commit and PR]

**Constraints:**
- Do not add web softphone (sip.js) unless explicitly requested
- One SIP registration per extension — CooCall must be off
- Mobile uses WSS, not TLS SIP on 5162 (sip_ua limitation)
- Python in CRM-api-1: `.\.venv\Scripts\python.exe`
- Do not edit `.plan.md` plan files

**Docs:** `docs/ZYCOO_SOFTPHONE_SETUP.md`, `docs/SOFTPHONE_TEST_PLAYBOOK.md`, `docs/ZYCOO_PUSH_RESEARCH.md`

---
