# ZYCOO CooVox PBX — LOOP CRM Setup Guide

> **Full guide (plain language + admin panel + architecture):** see [`ZYCOO_PBX_INTEGRATION_FULL_GUIDE.md`](./ZYCOO_PBX_INTEGRATION_FULL_GUIDE.md)

This guide covers wiring a **ZYCOO CooVox** IP PBX (Asterisk-based) to **LOOP CRM** using **AMI**, **Push Events**, and the **LOOP PBX Connector** (required when CRM is cloud-hosted).

## Architecture

```
PBX (192.168.1.x) ──Push Event──► Connector (LAN) ──HTTPS──► CRM API
PBX ◄──AMI──► Connector ◄──poll dial commands── CRM API
CRM ──FCM──► Mobile / Web screen pop
```

## 1. PBX identification

From your CooVox admin dashboard:

| Field | Example |
|-------|---------|
| Model | CooVox-T100 / T100-S / T100-A4 |
| Software | v4.0.5.p2 |
| LAN IP | 192.168.1.100 |

## 2. AMI (click-to-dial)

**Path:** Addons → API → AMI

| Field | Value |
|-------|-------|
| Username | e.g. `loopcrm` |
| Password | Strong password |
| Allow IP/Subnet | Connector machine IP only, e.g. `192.168.1.50/32` |

If AMI Account Settings are empty, the AMI interface is closed.

Default AMI port: **5038** (TCP).

## 3. Push Event (real-time call events)

**Path:** Addons → API → Push Event

| Field | Value |
|-------|-------|
| URL | Connector endpoint, e.g. `http://192.168.1.50:8787/zycoo` |
| Events | Enable all **call-related** events (incoming, answered, hangup, CDR, recording). Do not limit to AgentLogin/AgentLogoff only. |

Push Event sends **HTTP POST** JSON to the configured URL when selected events occur.

### Sample payloads (reference — verify against your PBX)

ZYCOO payloads may use form fields or JSON. The CRM parser accepts both and maps common Asterisk-style keys.

**Incoming / ringing (example):**

```json
{
  "Event": "Newchannel",
  "Uniqueid": "1700000000.123",
  "CallerIDNum": "07701234567",
  "CallerIDName": "07701234567",
  "Exten": "101",
  "Channel": "SIP/101-00000001",
  "Context": "from-trunk"
}
```

**Call answered (example):**

```json
{
  "Event": "BridgeEnter",
  "Uniqueid": "1700000000.123",
  "ConnectedLineNum": "07701234567",
  "Exten": "101"
}
```

**Hangup / CDR (example):**

```json
{
  "Event": "Hangup",
  "Uniqueid": "1700000000.123",
  "CallerIDNum": "07701234567",
  "ConnectedLineNum": "101",
  "Duration": "45",
  "Billsec": "38",
  "Disposition": "ANSWERED",
  "RecordingFile": "/var/spool/asterisk/monitor/recording.wav"
}
```

**Agent login (from your screenshot):**

```json
{
  "Event": "AgentLogin",
  "Agent": "101"
}
```

> **Important:** Capture one live POST body per event type from your PBX logs during testing and compare with these samples. Update the connector log if field names differ.

## 4. Extension → CRM user mapping

1. Note each agent extension under **Telephony → Extensions** on ZYCOO.
2. In LOOP CRM: **Integrations → PBX / ZYCOO → User extensions**.
3. Map each CRM user to the **same** extension number (e.g. User Ahmed → `101`).
4. **Register a device** for that extension:
   - **Desk phone:** IP phone registers to the extension via SIP.
   - **Mobile agent:** a SIP client (e.g. ZYCOO’s app) — extension, server, password, then **Register** until connected.
5. AMI connector login alone is **not** enough; click-to-dial rings `PJSIP/{extension}`, which must exist and have a registered endpoint.

## 5. LOOP PBX Connector (cloud CRM)

Install on a PC/server on the **same LAN** as the PBX.

```bash
cd CRM-api-1/scripts/pbx_connector
pip install -r requirements.txt
copy config.example.json config.json
# Edit config.json with your values
python connector.py
```

**config.json fields:**

| Key | Description |
|-----|-------------|
| `api_base_url` | CRM API base, e.g. `https://api.yourcrm.com` |
| `connector_api_key` | From CRM Integrations → PBX settings |
| `webhook_token` | Shown in PBX settings (for direct cloud webhook if used) |
| `pbx_host` | `192.168.1.100` |
| `ami_port` | `5038` |
| `ami_username` / `ami_password` | AMI credentials |
| `listen_host` | `0.0.0.0` |
| `listen_port` | `8787` |
| `ftp_user` / `ftp_password` | Zycoo **FTP Server** credentials (System → Storage → FTP Storage) |
| `ftp_host` | Optional; defaults to `pbx_host` |
| `ftp_port` | `21` (plain FTP) |
| `recording_poll_interval_sec` | How often to poll CRM for pending recording jobs (default `5`) |

Run as a Windows service or systemd unit so it stays online.

## 6. Call recording via PBX FTP (required for CRM playback)

Zycoo has **no HTTP API** for recordings. LOOP pulls files from the PBX **internal FTP server**.

### On the Zycoo admin UI

1. **System → Storage → FTP Storage → FTP Server**
2. Turn **Enable Service** ON (and optionally **Allow Write** — not required for CRM pull).
3. Set a strong **Username** / **Password** (do not use defaults like `ftpuser`/`ftppass` in production).
4. Leave **FTP Uploading** OFF — that feature pushes to *your* FTP server on a **daily** schedule and is not used by LOOP.
5. Click **Submit**.

### Verify from the connector PC

After a recorded call, browse FTP (e.g. FileZilla) to:

```
/recording/<YYYYMMDD>/<extension>/<uniqueid>-....wav
```

CDR paths look like `/var/spool/asterisk/monitor/recording/...`; the connector maps them to `/recording/...`.

### Connector config

Set `ftp_user` / `ftp_password` in `config.json` (and `ftp_host` if different from `pbx_host`). Enable **Cdr** (and recording-related) Push Events so CRM receives `recording_filename`.

Expect a short delay after hangup before the `.wav` exists; the connector retries on FTP 550.

## 7. Remote Access (optional)

**Path:** Addons → Remote Access

If **Proxy Status** is `Rejected`, the PBX cannot reach a cloud URL directly. Fix via Refresh Status / Sync Certificate, or rely on the LAN connector.

If Remote Access works, you may point Push Event directly to:

```
https://<your-api>/api/integrations/webhooks/pbx/<webhook_token>/
```

and whitelist the CRM server IP in AMI (less secure; connector is preferred).

## 8. PMS (skip)

**Addons → API → PMS** is for hotel/property systems. Leave disabled for standard CRM use.

## 9. CRM configuration checklist

1. Enable PBX integration in **Integrations → PBX / ZYCOO**.
2. Copy **Webhook URL** and **Connector API key**.
3. Map user extensions.
4. Enable Zycoo **FTP Server** and put credentials in connector `config.json`.
5. Start connector; confirm **Connector last seen** updates in CRM.
6. Place a test inbound call → screen pop + auto call log on lead timeline.
7. Confirm recording status becomes ready and playback works on the lead call.
8. Test click-to-dial from a lead page.

## 10. Troubleshooting

| Issue | Check |
|-------|-------|
| No events in CRM | Connector running? Push Event URL correct? Firewall on port 8787? |
| Click-to-dial fails | AMI credentials, allowed IP, extension exists on PBX, CRM mapping matches ZYCOO, desk phone or SIP app registered? |
| `Extension does not exist` | Wrong extension in CRM mapping, or extension not created under Telephony → Extensions |
| Wrong lead on screen pop | Phone number format; add number to lead profile |
| No recording metadata | Recording enabled on PBX? `recording_filename` / CDR fields present in Push Event payload? |
| Recording stuck pending | FTP Server enabled? `ftp_user`/`ftp_password` correct? File under `/recording/YYYYMMDD/...` from connector PC? |
| Connector offline | `connector_last_seen_at` in CRM settings; network to cloud API |
