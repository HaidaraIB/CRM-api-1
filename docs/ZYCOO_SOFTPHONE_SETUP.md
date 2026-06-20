# ZYCOO CooVox Softphone — PBX Prerequisites (Mobile)

Setup your CooVox T100/T100-S/T100-A4 before enabling the LOOP **mobile** embedded softphone.

> **Web CRM:** No browser softphone. Agents use the **LOOP mobile app** for embedded calling, or **click-to-dial** in the web CRM (AMI). Admin configures softphone server settings in **Integrations → PBX / ZYCOO**.

## 1. Remote Access (SIP Proxy)

Path: **Addons → Remote Access**

1. Enable the ZYCOO SIP Proxy service.
2. Confirm **Domain Server** shows your external domain (e.g. `shatalarab.uae.zycoo.com`).
3. Confirm **TLS port 5162** is active.
4. Click **Sync Certificate** if clients report SSL errors.

## 2. SoftPhone addon

Path: **Addons → SoftPhone → Settings**

1. **Enable** push notification support.
2. Set **Server** to your proxy domain (e.g. `shatalarab.uae.zycoo.com`).
3. Set **Port** to `5162` (TLS).

> CooCall uses PBX-native push. LOOP mobile uses **CRM-mediated VoIP push** until ZYCOO exposes a third-party token API.

## 3. Per-extension settings

Path: **Telephony → Extensions → IP Extensions → Edit**

For each CRM agent extension:

| Setting | Value |
|---------|-------|
| Password | Set a known SIP password (admin copies into CRM) |
| WebRTC | **Enabled** (required for WSS/WebRTC registration) |
| Transport Protocol | **TLS** |
| App Extension | Disable if migrating from CooCall to LOOP |

## 4. CooCall migration policy

**Only one SIP client can register per extension at a time.**

Before an agent uses LOOP mobile softphone:

1. Uninstall or log out of CooCall on that extension.
2. Delete the extension row in **Addons → SoftPhone → List** if it still shows CooCall tokens.
3. Map the extension in CRM with the SIP password.

## 5. Firewall / NAT

| Port | Protocol | Purpose |
|------|----------|---------|
| 5162 | TCP/TLS | Remote SIP (reference; mobile app uses WSS) |
| 8089 | TCP/TLS | WSS WebRTC (mobile registration + media) |
| UDP RTP range | UDP | Audio media (per ZYCOO manual) |

Leave **ICE Enable** on in PBX SIP settings (default).

## 6. Existing LOOP connector (unchanged)

Keep the LAN connector running for:

- CDR / call events
- Screen pop
- Web click-to-dial (AMI)
- Call recordings

Push Event URL: `http://<connector-pc-ip>:8787`

## 7. CRM configuration (web admin)

Path: **Integrations → PBX / ZYCOO**

1. Enable PBX integration.
2. Enable **Embedded softphone (mobile app)**.
3. Set SIP domain, port `5162`, WSS URI (e.g. `wss://shatalarab.uae.zycoo.com:8089/ws`).
4. Map each user → extension + SIP password + enable softphone.

## Platform summary

| Platform | Calling method | Works when app/tab closed? |
|----------|----------------|----------------------------|
| Web CRM | AMI click-to-dial only | N/A |
| Mobile LOOP | Embedded softphone (WSS + CallKit) | Yes — via VoIP push (iOS cert required) |
