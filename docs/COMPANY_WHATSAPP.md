# Company WhatsApp (admin → tenant owner)

Super Admin chat at **Admin panel → Company WhatsApp** (`/tenant-whatsapp`).

This uses **LOOP’s platform Cloud API number**, not each tenant’s Embedded Signup / coexistence connection.

App Review + Live is required for production Meta traffic, but chat stays empty until the items below are configured.

---

## 1. Platform credentials

**Admin → Settings → Platform WhatsApp**, or `.env`:

```env
PLATFORM_WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
PLATFORM_WHATSAPP_ACCESS_TOKEN=EAA...   # System User permanent token (long; not a 15-char stub)
PLATFORM_WHATSAPP_GRAPH_API_VERSION=v25.0
```

DB settings override env when set. Store DB tokens only if `INTEGRATION_ENCRYPTION_KEY` is configured.

Check (no secrets printed):

```text
.venv\Scripts\python.exe manage.py platform_whatsapp_check
```

Without a valid token, send returns `platform_whatsapp_token_invalid` / Graph OAuth errors.

---

## 2. Admin outbound template (recommended)

Cold messages need an **approved Meta template** with **one** body variable `{{1}}` (the typed message).

```env
PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME=admin_notify_1
PLATFORM_WHATSAPP_ADMIN_TEMPLATE_LANG=en
```

Or the same fields in Platform WhatsApp settings.

- Digit-only values (e.g. a pasted phone_number_id) are **ignored** — use the template **name**, not an ID.
- If no template is set, the API sends plain **session text**, which fails unless the owner already messaged the platform number within 24 hours.

OTP signup uses a separate template (`PLATFORM_WHATSAPP_OTP_TEMPLATE_NAME`), not the admin chat template.

---

## 3. Owner phone

Send uses the company **owner** user’s phone (digits only). Missing phone → `owner_phone_missing`.

---

## 4. Webhook (owner replies)

Same URL as tenant WhatsApp:

`{API_BASE_URL}/api/integrations/webhooks/whatsapp/`

Requires:

- Public HTTPS `API_BASE_URL` (not localhost)
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN` (+ `WHATSAPP_CLIENT_SECRET` for signature)
- `messages` subscribed on the WABA that owns the **platform** phone

Inbound routing: if `phone_number_id` equals the platform PID → Company WhatsApp thread (`AdminTenantWhatsAppMessage`). Do **not** connect the platform number as a tenant WhatsApp account.

---

## 5. Verify

1. `manage.py platform_whatsapp_check` — credentials + template look OK  
2. Company WhatsApp → select company → Send  
3. Outbound bubble appears (stored only after Graph success)  
4. Owner replies to the **platform** business number → refresh the chat (no live polling)

---

## Related

- Tenant Embedded Signup / coexistence: [WHATSAPP_EMBEDDED_SIGNUP.md](./WHATSAPP_EMBEDDED_SIGNUP.md)  
- Shared webhook setup: [WHATSAPP_WEBHOOK_REQUIREMENTS.md](./WHATSAPP_WEBHOOK_REQUIREMENTS.md)
