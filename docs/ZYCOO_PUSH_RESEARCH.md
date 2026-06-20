# ZYCOO Native Push — Research Notes

## Goal

Match CooCall lock-screen incoming call speed by having the PBX push directly to LOOP mobile, instead of CRM relay (PBX → connector → CRM → FCM/APNs).

## What we know

- CooVox stores push tokens in **Addons → SoftPhone → List** when CooCall registers.
- Token JSON includes `fcmToken`, `voipToken` (iOS), `tpnsToken`, `hwToken` (Android variants).
- ZYCOO does **not** publish a public REST API for third-party apps to register these tokens.
- CooCall privacy policy states tokens are exchanged only between CooCall and the customer's CooVox PBX.

## Current LOOP fallback

CRM sends high-priority FCM (Android) and APNs VoIP push (iOS) on earliest `ringing` PBX events. Mobile shows CallKit UI, re-registers SIP, and answers.

Latency: ~1–5 seconds vs CooCall's near-instant PBX-native push.

## Recommended actions

1. **Contact ZYCOO support** — request third-party softphone push API for CooVox T100 series. Reference SoftPhone List token format and LOOP CRM bundle ID `com.loopcrm.mobile`.
2. **Network capture** — register CooCall on a test extension; capture HTTPS/SIP traffic to find the token registration endpoint.
3. **If API found** — add `POST /integrations/pbx/softphone/zycoo-register/` proxy in CRM-api-1 that forwards device tokens to PBX.

## Environment variables (iOS VoIP push via CRM)

```env
APNS_VOIP_KEY_PATH=/path/to/AuthKey_XXXX.p8
APNS_VOIP_KEY_ID=XXXXXXXXXX
APNS_VOIP_TEAM_ID=XXXXXXXXXX
APNS_BUNDLE_ID=com.loopcrm.mobile
```

Until ZYCOO native push is available, configure these for CRM-mediated iOS lock-screen ringing on the **mobile app** only. Web CRM does not use softphone push.
