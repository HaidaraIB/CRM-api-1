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
   For production customers, the app usually needs to be **Live** and permissions approved as Meta requires for your use case.

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

---

## Part D — What happens in the app

1. User clicks **Connect** on a WhatsApp integration account.  
2. `POST .../connect/` returns `embedded_signup.enabled: true` when Part B is configured.  
3. The CRM loads the **Facebook SDK** and runs **`FB.login`** with `config_id` (Embedded Signup).  
4. User completes Meta’s screens (same family as your screenshots).  
5. The browser receives `authResponse.code`; the CRM calls  
   `POST /api/integrations/accounts/{id}/whatsapp/embedded-signup/complete/`  
   with `{ "code": "..." }`.  
6. The API exchanges the code, then runs the same logic as the redirect OAuth path (tokens + `WhatsAppAccount` rows).

If `WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID` is **not** set, behavior is unchanged: **popup** opens the classic `authorization_url`.

---

## Troubleshooting

- **Token exchange fails** (`invalid redirect_uri`): set `WHATSAPP_EMBEDDED_SIGNUP_TOKEN_EXCHANGE_REDIRECT_URI` to the exact value Meta expects, or leave empty if Graph expects empty.  
- **FB.login does nothing / blocked**: check browser console, ad blockers, and Meta **allowed domains**.  
- **Embedded Signup UI does not appear**: wrong or missing `config_id`, or app not configured for Embedded Signup.  
- **`#N/A` Graph errors**: confirm app is in **Live** mode for real users when required.

Official reference: [WhatsApp Embedded Signup implementation](https://developers.facebook.com/docs/whatsapp/embedded-signup/implementation).
