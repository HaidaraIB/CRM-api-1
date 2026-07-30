# WhatsApp Embedded Signup — setup checklist (LOOP CRM)

This matches Meta’s guided flow (business info, WABA, phone verification, permissions) shown in the Embedded Signup UI. Your CRM uses it when **`WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID`** is set on the API.

---

## Part A — Meta Developer (one-time per app)

1. **App type**  
   Use a Meta app with the **WhatsApp** product added.

2. **Facebook Login for Business**  
   - In the app: add/configure **Facebook Login for Business** (or the Embedded Signup entry under WhatsApp product — follow Meta’s current menu labels).  
   - Create a **configuration** that includes WhatsApp assets and the permissions your solution needs (see [Embedded Signup](https://developers.facebook.com/docs/whatsapp/embedded-signup/)).

3. **Copy `config_id`**  
   After saving the configuration, copy the **Configuration ID** (`config_id`). You will paste it into the API environment as `WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID`.

4. **App ID / secret**  
   You already use **`WHATSAPP_CLIENT_ID`** and **`WHATSAPP_CLIENT_SECRET`** (or Meta fallbacks). The same app ID is used by the Facebook JS SDK on the frontend (returned in the connect API response).

5. **Valid OAuth redirect URIs**  
   Keep your existing server redirect for the **non-embedded** path:  
   `https://<API_HOST>/api/integrations/accounts/oauth/callback/whatsapp/`  
   (Used when Embedded Signup is disabled or for testing.)

6. **Domains (frontend)**  
   In the Meta app **Settings → Basic**: add the **website / OAuth redirect** domains that host your CRM (e.g. `loop-crm.app`). The FB SDK loads on that origin.

7. **Publish / access**  
   For production customers, the app usually needs to be **Live** and permissions approved as Meta requires for your use case. Tech Provider + Advanced access to `whatsapp_business_messaging` and `whatsapp_business_management` are required for customer onboarding (including coexistence).

8. **Webhook fields (required for coexistence)**  
   In **App Dashboard → WhatsApp → Configuration**, subscribe your callback URL to at least:

   | Field | Purpose |
   |-------|---------|
   | `messages` | Inbound messages + delivery statuses |
   | `history` | Chat history sync after coexistence onboard |
   | `smb_app_state_sync` | WhatsApp Business app contacts sync |
   | `smb_message_echoes` | Mirror outbound messages sent from the phone app |
   | `account_update` | Partner removed / offboard events |

   Confirm with: `.venv\Scripts\python.exe manage.py whatsapp_debug_check`

---

## Part B — API environment (`CRM-api-1`)

Set in `.env` (or host env) and **restart** the API:

| Variable | Purpose |
|----------|---------|
| `WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID` | From Meta Login for Business configuration (required to turn on Embedded Signup in CRM). |
| `WHATSAPP_CLIENT_ID` | Meta app ID (must match the app where `config_id` was created). |
| `WHATSAPP_CLIENT_SECRET` | App secret (token exchange). |
| `WHATSAPP_EMBEDDED_SIGNUP_TOKEN_EXCHANGE_REDIRECT_URI` | Optional. Default is **empty**. Meta’s token exchange for `FB.login` + `code` often uses an **empty** `redirect_uri`. If Graph returns an error, set this to the value Meta’s error message expects (see Meta docs / error text). |

Then verify:

```text
.venv\Scripts\python.exe manage.py whatsapp_debug_check
```

You should see Embedded Signup **enabled** when `config_id` and app id are present.

---

## Part C — Frontend (`CRM-project`)

No extra env is **required** for the app id: the connect response includes `embedded_signup.app_id` and `config_id`.

Ensure users open the CRM on an **HTTPS** origin allowed in the Meta app.

The CRM launches Embedded Signup with coexistence extras:

```js
extras: {
  setup: {},
  featureType: "whatsapp_business_app_onboarding",
  sessionInfoVersion: "3",
}
```

That unlocks Meta’s dual setup screen: **Connect your existing WhatsApp Business app** or **Start with a new WhatsApp phone number**.

---

## Part D — What happens in the app

1. User clicks **Connect** on a WhatsApp integration account.  
2. `POST .../connect/` returns `embedded_signup.enabled: true` when Part B is configured.  
3. The CRM loads the **Facebook SDK** and runs **`FB.login`** with `config_id` + coexistence `extras`.  
4. User completes Meta’s screens (new Cloud number **or** existing WhatsApp Business app).  
5. The browser receives `authResponse.code` and a `WA_EMBEDDED_SIGNUP` session event (`FINISH` or `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`).  
6. The CRM calls  
   `POST /api/integrations/accounts/{id}/whatsapp/embedded-signup/complete/`  
   with `{ "code": "...", "waba_id", "phone_number_id", "signup_event" }`.  
7. The API exchanges the code, upserts `WhatsAppAccount` rows, subscribes the WABA (`POST /{waba-id}/subscribed_apps` — hard requirement; connect is marked error if all subscribe calls fail), and:
   - **Cloud API numbers:** `POST /{phone_number_id}/register` (avoids Graph `#133010 Account not registered`)
   - **Coexistence:** SMB contacts + history sync (`POST /{phone_number_id}/smb_app_data`) within 24h — **do not** `/register`
8. Coexistence is detected from `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING` **or** Graph `is_on_biz_app=true` if the browser postMessage event was missed.
9. Repair helper on the API host: `python manage.py whatsapp_repair_subscriptions [--register] [--company-id N]`

If `WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID` is **not** set, behavior is unchanged: **popup** opens the classic `authorization_url`.

---

## Part E — Coexistence (WhatsApp Business app onboarding)

Official Meta guide: [Onboard WhatsApp Business app users](https://developers.facebook.com/docs/whatsapp/embedded-signup/custom-flows/onboarding-business-app-users/).

### Requirements

- Tech Provider (or Solution Partner) status.
- Customer uses **WhatsApp Business app** ≥ 2.24.17 (not consumer WhatsApp).
- Webhook fields from Part A §8 subscribed.
- Start contacts/history sync within **24 hours** of onboarding (CRM does this in the complete endpoint).

### Product constraints

- Coexistence WABAs are **1 phone number : 1 WABA**. Adding another number to that WABA fails with “Cannot add phone number…” — expected. Use standard Cloud Embedded Signup for additional numbers (separate WABA).
- Keep the WhatsApp Business app installed; do not delete the account.
- Throughput for coexistence numbers is fixed at **20 mps**.
- Companion devices unlink on onboard and must be re-linked (Windows / WearOS unsupported for mirroring).
- Customers disconnect from **Settings → Account → Business Platform → Disconnect** in the Business app; CRM receives `account_update` / `PARTNER_REMOVED` and marks the account disconnected.

### Verify after a test connect

1. Meta UI shows the dual setup screen.  
2. Graph: `GET /{phone_number_id}?fields=is_on_biz_app,platform_type` → `is_on_biz_app: true`, `platform_type: CLOUD_API`.  
3. Integration account metadata includes `coexistence: true` and `coexistence_smb_sync`.  
4. Contacts / history / echo webhooks land in CRM chat threads.

---

## Troubleshooting

- **Token exchange fails** (`invalid redirect_uri`): set `WHATSAPP_EMBEDDED_SIGNUP_TOKEN_EXCHANGE_REDIRECT_URI` to the exact value Meta expects, or leave empty if Graph expects empty.  
- **FB.login does nothing / blocked**: check browser console, ad blockers, and Meta **allowed domains**.  
- **Embedded Signup UI does not appear**: wrong or missing `config_id`, or app not configured for Embedded Signup.  
- **No “Connect existing WhatsApp Business app” screen**: CRM must send `featureType: whatsapp_business_app_onboarding` (already in `whatsappEmbeddedSignup.ts`); confirm Tech Provider status.  
- **No contacts/history after coexistence**: subscribe webhook fields in Part A §8; keep the Business app open during sync; check logs for `WhatsApp SMB sync`.  
- **History empty with error 2593109**: customer declined history sharing in the Business app — expected.  
- **`#N/A` Graph errors**: confirm app is in **Live** mode for real users when required.
- **Outbound works but customer replies never appear in Messaging Center**:
  1. Run `.venv\Scripts\python.exe manage.py whatsapp_debug_check` — check webhook URL, secrets, and inbound vs outbound message counts.
  2. Confirm Meta callback is `{API_BASE_URL}/api/integrations/webhooks/whatsapp/` and the WABA is subscribed to **`messages`** (plus coexistence fields if using the Business app).
  3. Confirm the tenant `WhatsAppAccount.phone_number_id` is **not** the same as Platform WhatsApp (`PLATFORM_WHATSAPP_PHONE_NUMBER_ID`) — platform PID diverts replies to the admin chat, not Messaging Center.
  4. Confirm `WhatsAppAccount.status` is `connected` (Disconnect clears tokens and status; reconnect if needed).
  5. In the CRM chat thread, use **Refresh** or wait a few seconds — Messaging Center polls for new messages while the chat is open.

Official reference: [WhatsApp Embedded Signup implementation](https://developers.facebook.com/docs/whatsapp/embedded-signup/implementation).
