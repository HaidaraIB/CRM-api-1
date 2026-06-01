# ZYCOO PBX × LOOP CRM — Full Integration Guide

> **Who is this for?** Admins, IT staff, and CRM users setting up phone calls with a ZYCOO CooVox PBX.
>
> **How to read this:** Written in plain language. Think of the phone system like a toy phone on your desk, the CRM like a notebook about your customers, and the connector like a helper who runs between them carrying messages.

---

## Table of contents

1. [The big picture (30-second version)](#1-the-big-picture-30-second-version)
2. [The three friends in this story](#2-the-three-friends-in-this-story)
3. [What the integration can do](#3-what-the-integration-can-do)
4. [Who turns what on? (Admin vs tenant)](#4-who-turns-what-on-admin-vs-tenant)
5. [How to enable/disable from Admin Panel](#5-how-to-enabledisable-from-admin-panel)
6. [How the tenant turns it on in CRM](#6-how-the-tenant-turns-it-on-in-crm)
7. [Full setup — step by step](#7-full-setup--step-by-step)
8. [The LAN connector section explained](#8-the-lan-connector-section-explained)
9. [How each feature works inside](#9-how-each-feature-works-inside)
10. [API reference (for developers)](#10-api-reference-for-developers)
11. [Troubleshooting](#11-troubleshooting)
12. [Glossary](#12-glossary)

---

## 1. The big picture (30-second version)

Your **office phone system (PBX)** lives inside your building on the local network (example: `192.168.1.100`).

Your **CRM (LOOP)** lives on the internet (cloud).

They cannot talk to each other directly — like two kids in different houses with no phone line between them.

So we add a **helper program (LAN connector)** on a computer **inside the office**. That helper:

- **Listens** when the PBX says “someone is calling!”
- **Tells the CRM** on the internet
- **Asks the CRM** “does anyone want to make a call?” and **tells the PBX** to dial

```
  Office building                         Internet (cloud)
 ┌─────────────────┐                    ┌─────────────────┐
 │  ZYCOO PBX      │  Push events       │                 │
 │  192.168.1.100  │ ──────────────►    │   LOOP CRM      │
 │  (desk phones)  │                    │   (leads, calls)│
 └────────┬────────┘                    └────────▲────────┘
          │                                     │
          │ AMI (dial commands)                 │ HTTPS
          │                                     │
 ┌────────▼────────┐  forwards events + polls   │
 │ LAN Connector   │ ──────────────────────────┘
 │ (office PC)     │
 │ port 8787       │
 └─────────────────┘
```

---

## 2. The three friends in this story

### Friend 1 — The PBX (ZYCOO CooVox)

- The box that makes desk phones ring.
- Knows extensions (101, 102, …).
- Knows when calls start, get answered, end, or are missed.
- Can **push** messages (Push Events) and accept **dial commands** (AMI).

**Where you configure it:** CooVox web admin → Addons → API

### Friend 2 — The CRM (LOOP)

- Knows your leads, phone numbers, and call history.
- Shows **screen pop** when a known number calls.
- Lets agents click **“Dial via PBX”** from a lead page.
- Stores **call reports** and auto-logs calls on the lead timeline.

**Where you configure it:** CRM → Integrations → PBX / ZYCOO

### Friend 3 — The LAN Connector (small Python program)

- Runs on **one office PC** on the same Wi‑Fi/LAN as the PBX.
- Receives PBX events on **port 8787**.
- Forwards them to the CRM using a secret **Connector API key**.
- Every few seconds, checks CRM for **dial commands** and sends them to the PBX via AMI.

**Where it lives:** `CRM-api-1/scripts/pbx_connector/`

---

## 3. What the integration can do

| Feature | What the user sees | What happens behind the scenes |
|--------|-------------------|-------------------------------|
| **Screen pop** | Modal / notification: “Incoming call from 0770…” with **Open lead** | PBX event → connector → CRM matches phone to lead → push notification |
| **Missed call alert** | “Missed call from …” notification | Same flow, missed/disposition events |
| **Auto call log** | Call appears on lead timeline automatically | On hangup, CRM creates a `ClientCall` linked to the PBX record |
| **Click-to-dial** | Phone icon on lead → desk phone rings, then customer is dialed | CRM queues command → connector polls → AMI Originate |
| **Call reports** | Reports → Call Reports (totals, inbound/outbound, per agent) | Aggregates stored `PbxCallRecord` rows |
| **Recordings** | Recording URL stored in call notes (when PBX sends it) | Parsed from hangup/CDR payload |

---

## 4. Who turns what on? (Admin vs tenant)

There are **three switches**. All must allow use before calls work end-to-end.

```
┌──────────────────────────────────────────────────────────────┐
│ 1. PLAN (Admin Panel → Subscriptions → Edit plan)            │
│    Feature flag: integration_pbx                             │
│    “Is PBX included in this customer’s subscription?”       │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. SYSTEM POLICY (Admin Panel → Settings → Integrations)     │
│    Platform: pbx                                             │
│    Global on/off + optional per-company override             │
│    “Are we allowing PBX for everyone / this tenant?”         │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. TENANT SETTING (CRM app → Integrations → PBX / ZYCOO)     │
│    Checkbox: Enable PBX integration (is_enabled)             │
│    “This company wants to use their phone system now.”       │
└──────────────────────────────────────────────────────────────┘
```

**If an admin disables PBX**, the CRM automatically sets `PbxSettings.is_enabled = false` for affected companies (same as WhatsApp, Twilio, OpenAI, etc.).

---

## 5. How to enable/disable from Admin Panel

### A. Include PBX in a subscription plan

1. Open **Admin Panel** → **Subscriptions**.
2. Create or edit a plan.
3. Under **Feature flags**, check or uncheck **PBX / ZYCOO** (`integration_pbx`).
4. Save the plan.

- **Checked** → companies on that plan *may* use PBX (if system policy also allows).
- **Unchecked** → CRM shows “not included in your plan” and blocks PBX API calls.

### B. Global or per-tenant access control

1. Open **Admin Panel** → **Settings** → **Integrations** tab.
2. Find the **PBX / ZYCOO** block (same layout as Meta, WhatsApp, Twilio, …).
3. **Global Active** — master switch for all tenants.
4. **Global deactivation message** — shown in CRM when globally off.
5. **Per company** — pick a tenant, toggle access, optional custom message.
6. Click **Save**.

**Typical uses:**

| Scenario | What to do |
|----------|------------|
| Roll out PBX to everyone | Plan ✓ + Global Active ✓ |
| Beta test with one company | Global off + enable exception for that company |
| Remove PBX from one bad payer | Global on + disable that company |
| Remove PBX product-wide | Uncheck on all plans + Global off |

---

## 6. How the tenant turns it on in CRM

After admin allows PBX:

1. Log into **CRM** (web or mobile).
2. Go to **Integrations → PBX / ZYCOO**.
3. Fill in:
   - PBX host (LAN IP, e.g. `192.168.1.100`)
   - AMI port (usually `5038`)
   - AMI username & password
4. Check **Enable PBX integration**.
5. Optionally enable **Auto log calls** and **Screen pop**.
6. Click **Save**.
7. Copy **Connector API key** → paste into connector `config.json`.
8. Map **User extensions** (CRM user ↔ desk extension).
9. Start the connector on the office PC.
10. Confirm **Connector status: Online**.

---

## 7. Full setup — step by step

### Step 0 — Prerequisites

- [ ] ZYCOO CooVox PBX on LAN with admin access
- [ ] LOOP CRM company with PBX allowed (admin steps above)
- [ ] One always-on Windows/Linux PC on the **same network** as the PBX
- [ ] Outbound HTTPS from that PC to your CRM API URL
- [ ] AMI enabled on PBX (Addons → API → AMI)
- [ ] Push Events enabled (Addons → API → Push Event)

### Step 1 — PBX: AMI user

**Path:** Addons → API → AMI

| Field | Example |
|-------|---------|
| Username | `loopcrm` |
| Password | strong password |
| Allowed IP | connector PC IP only, e.g. `192.168.1.50/32` |

Default port: **5038** (TCP).

### Step 2 — PBX: Push Event URL

**Path:** Addons → API → Push Event

| Field | Value |
|-------|-------|
| URL | `http://192.168.1.50:8787` (connector PC IP + port) |
| Events | All **call-related** events (not just AgentLogin) |

> **Do not** point Push Event directly at `localhost` or the CRM cloud URL unless Remote Access is working and you know what you’re doing. The normal cloud setup uses the **connector**.

### Step 3 — CRM: PBX settings

**Path:** Integrations → PBX / ZYCOO

Enter AMI credentials and enable integration. Save.

### Step 4 — Install the connector

```bash
cd CRM-api-1/scripts/pbx_connector
pip install -r requirements.txt
copy config.example.json config.json   # Windows
# cp config.example.json config.json   # Linux/Mac
```

Edit `config.json`:

```json
{
  "api_base_url": "https://api.your-crm-domain.com",
  "connector_api_key": "PASTE_FROM_CRM_SETTINGS",
  "pbx_host": "192.168.1.100",
  "ami_port": 5038,
  "ami_username": "loopcrm",
  "ami_password": "your-ami-password",
  "listen_host": "0.0.0.0",
  "listen_port": 8787,
  "poll_interval_sec": 3
}
```

Run:

```bash
python connector.py
```

Run as a Windows Service or systemd unit in production.

### Step 5 — Map extensions

**CRM:** Integrations → PBX / ZYCOO → User extensions

| CRM user | Extension |
|----------|-----------|
| Ahmed | 101 |
| Sara | 102 |

Needed for screen pop (which agent gets the popup) and click-to-dial (which phone rings first).

### Step 6 — Test

| Test | Expected result |
|------|-----------------|
| Connector running | CRM shows **Connector status: Online** |
| Inbound call from known lead number | Screen pop + notification |
| Call completes | Timeline entry on lead (if auto-log on) |
| Click **Dial via PBX** on lead | Agent extension rings, then customer |
| Reports → Call Reports | Stats appear after hangup events |

---

## 8. The LAN connector section explained

This read-only card in CRM is **generated automatically** — you never type those values.

### Webhook URL

```
https://api.example.com/api/integrations/webhooks/pbx/{webhook_token}/
```

- Unique **per company** (`webhook_token` in database).
- Used for **direct** PBX → cloud webhook (rare; needs Remote Access / VPN).
- In the standard connector setup, ZYCOO points to the **connector** (`:8787`), not this URL.
- Shown for reference, debugging, or advanced LAN/VPN setups.

### Connector API key

- Secret password between **connector program** and **CRM API**.
- Sent as: `Authorization: Bearer {connector_api_key}`
- Copy into `config.json` on the office PC.
- **Rotate key** invalidates the old key — update `config.json` after rotating.

### Connector status (Online / Offline)

- **Online** = CRM received a heartbeat or command poll in the last **3 minutes**.
- **Offline** = connector not running, wrong key, or PC cannot reach CRM API.

**How status updates:**

```
connector.py loop every ~3 seconds:
  POST /api/integrations/pbx/connector/heartbeat/
  GET  /api/integrations/pbx/connector/commands/
       → CRM sets connector_last_seen_at = now
```

---

## 9. How each feature works inside

### Screen pop (incoming call)

```
1. Customer calls → PBX creates event (Newchannel / Ring)
2. PBX POST → connector :8787
3. Connector POST → CRM /pbx/connector/events/
4. CRM parses phone number, finds matching lead
5. CRM finds agent by extension mapping
6. CRM sends notification (pbx_incoming_call)
7. Web: PbxScreenPopListener shows modal
   Mobile: notification opens lead
```

### Auto call logging

```
1. Call ends → Hangup event with duration/disposition
2. Same path to CRM
3. If auto_log_calls = true and lead matched:
   → Creates ClientCall with source = pbx
   → Links to PbxCallRecord
   → Appears on lead timeline
```

### Click-to-dial

```
1. User clicks Dial via PBX on lead page
2. CRM POST /integrations/pbx/dial/
3. Creates PbxDialCommand (status: pending)
4. Connector polls GET /pbx/connector/commands/
5. Connector AMI Originate: agent ext rings, then dials customer
6. Connector POST .../commands/{id}/ack/ with success/failure
```

### Call reports

```
CRM aggregates PbxCallRecord rows (hangup events)
→ /integrations/pbx/reports/summary/
→ /integrations/pbx/reports/agents/
→ Call Reports page in CRM
```

---

## 10. API reference (for developers)

### Tenant endpoints (authenticated)

| Method | Path | Purpose |
|--------|------|---------|
| GET/PUT | `/api/integrations/pbx/settings/` | Read/update PBX config |
| POST | `/api/integrations/pbx/settings/rotate-connector-key/` | New connector API key |
| GET/POST | `/api/integrations/pbx/extensions/` | List/create extension maps |
| DELETE | `/api/integrations/pbx/extensions/{id}/` | Remove mapping |
| POST | `/api/integrations/pbx/dial/` | Queue click-to-dial |
| GET | `/api/integrations/pbx/reports/summary/` | Call stats |
| GET | `/api/integrations/pbx/reports/agents/` | Per-extension stats |
| GET | `/api/integrations/policy/` | Effective enable/disable per platform |

### Connector endpoints (Bearer connector_api_key)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/integrations/pbx/connector/heartbeat/` | Keep-alive |
| POST | `/api/integrations/pbx/connector/events/` | Forward PBX events |
| GET | `/api/integrations/pbx/connector/commands/` | Fetch pending dials |
| POST | `/api/integrations/pbx/connector/commands/{id}/ack/` | Report dial result |

### Direct webhook (webhook_token in URL)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/integrations/webhooks/pbx/{webhook_token}/` | PBX → CRM direct (optional) |

### Key database models

| Model | Table | Purpose |
|-------|-------|---------|
| `PbxSettings` | `integrations_pbx_settings` | Per-company config + secrets |
| `UserPbxExtension` | — | CRM user ↔ extension |
| `PbxCallRecord` | — | Raw CDR/event storage |
| `PbxDialCommand` | — | Click-to-dial queue |

### Entitlement keys

| Layer | Key |
|-------|-----|
| Plan feature | `integration_pbx` |
| System policy platform | `pbx` |
| Tenant toggle | `PbxSettings.is_enabled` |

### Code locations

| Area | Path |
|------|------|
| Connector script | `CRM-api-1/scripts/pbx_connector/connector.py` |
| Event processing | `CRM-api-1/integrations/services/pbx_handler.py` |
| Policy / gates | `CRM-api-1/integrations/policy.py` |
| API views | `CRM-api-1/integrations/views/pbx.py` |
| CRM settings UI | `CRM-project/components/integrations/PbxSettingsForm.tsx` |
| Screen pop | `CRM-project/components/PbxScreenPopListener.tsx` |
| Call reports | `CRM-project/pages/CallReportsPage.tsx` |
| Admin plan toggle | `CRM-admin-panel/components/PlanModal.tsx` |
| Admin policy toggle | `CRM-admin-panel/pages/SystemSettings.tsx` |

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **Connector Offline** | Script not running | Start `connector.py` on office PC |
| | Wrong `api_base_url` | Use public CRM API URL, not `localhost` (unless API runs on same machine) |
| | Wrong API key | Copy fresh key from CRM; restart connector |
| | Firewall blocking outbound HTTPS | Allow connector PC → CRM API |
| **No screen pop** | Extension not mapped | Map user in User extensions |
| | Screen pop disabled | Enable in PBX settings |
| | Phone not on any lead | Add number to lead profile |
| | PBX disabled by admin | Check plan + Settings → Integrations |
| **No events at all** | Push Event URL wrong | Must be `http://connector-ip:8787` |
| | Windows firewall on 8787 | Allow inbound TCP 8787 on connector PC |
| | PBX events too narrow | Enable all call events, not only login/logoff |
| **Click-to-dial fails** | AMI blocked | Check allowed IP, username, password |
| | Agent not registered | Desk phone/extension must be online on PBX |
| | No extension for user | Map CRM user to extension |
| **Webhook URL shows localhost** | Dev environment | Normal on local dev; production shows real domain |
| **403 / integration disabled** | Admin turned off PBX | Re-enable in Admin Panel |
| **Plan blocked** | `integration_pbx` off on plan | Admin enables on subscription plan |

### Quick health checklist

- [ ] Admin: plan includes PBX
- [ ] Admin: Integrations → PBX globally (or company) enabled
- [ ] Tenant: Enable PBX integration checked
- [ ] Connector process running
- [ ] CRM shows **Online**
- [ ] Push Event URL = connector IP:8787
- [ ] AMI credentials match CRM settings
- [ ] At least one user extension mapped

---

## 12. Glossary

| Term | Simple meaning |
|------|----------------|
| **PBX** | Office phone system (the “switchboard”) |
| **Extension** | Short internal number (101, 102) for a desk phone |
| **AMI** | Language the CRM connector uses to tell PBX “dial this number” |
| **Push Event** | PBX shouting “something happened!” over HTTP |
| **LAN connector** | Helper app in the office that bridges PBX and cloud CRM |
| **Screen pop** | CRM window popping up with the caller’s lead record |
| **CDR** | Call detail record — who called, how long, answered or missed |
| **Webhook token** | Secret ID in URL so CRM knows which company’s PBX sent data |
| **Connector API key** | Secret password for the connector to talk to CRM |

---

## Related docs

- Technical setup checklist: [`ZYCOO_PBX_SETUP.md`](./ZYCOO_PBX_SETUP.md)
- Connector config example: [`../scripts/pbx_connector/config.example.json`](../scripts/pbx_connector/config.example.json)

---

*Last updated: LOOP CRM ZYCOO PBX integration v1*
