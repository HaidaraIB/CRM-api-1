# Mujeb Integration

Submit leads from Mujeb chat / AI flows into Loop CRM via a **dedicated inbound endpoint**.
Tenants authenticate with the same per-company Lead API keys (`crm_lk_…`) used by the Custom Lead API.
Leads created through this path are labeled `source=mujeb`.

## Architecture

1. Loop CRM publishes one **Mujeb mini app** (configured once in Mujeb).
2. Each CRM tenant generates a Lead API key under **Integrations → Mujeb** (or Lead API).
3. Tenant installs the mini app in Mujeb and pastes the key into Auth.
4. Flows map chat variables into the **Create Lead** action; Mujeb POSTs to this endpoint.

## Endpoint

```
POST {API_BASE_URL}/api/v1/integrations/leads/mujeb/
Content-Type: application/json
```

Example base URL: `https://your-api.example.com`

## Authentication

Send your company API key using **one** of:

- Header: `Authorization: Bearer crm_lk_...`
- Header: `X-Lead-Api-Key: crm_lk_...`

Keys are created in the CRM under **Integrations → Mujeb** (or **Integrations → Lead API**). The full secret is shown **once** when you generate or rotate a key.

Do not send the global `X-API-Key` (mobile/web/admin) on this endpoint.

## Request body

Same schema as the Custom Lead API:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lead full name |
| `phone` | Recommended | Phone number |
| `external_id` | Recommended | Unique submission id (idempotency) |
| `email` | No | Stored in notes |
| `notes` | No | Free text |
| `campaign_id` | No | Must belong to your company |
| `communication_way_id` | No | Channel id for your company |
| `status_id` | No | Lead status id; else company default |
| `priority` | No | `low`, `medium` (default), `high` |
| `type` | No | `fresh` (default), `hot`, `cold` |
| `custom_fields` | No | JSON object; appended to notes |

### Example

```json
{
  "name": "Hassan Alsaadi",
  "phone": "+9647700000001",
  "external_id": "mujeb-chat-abc123",
  "email": "hassan@example.com",
  "notes": "Interested in maintenance",
  "custom_fields": {
    "region": "Baghdad",
    "service": "Maintenance"
  }
}
```

## Responses

All responses use the CRM envelope: `{ "success": true|false, "data"?: ..., "error"?: { "code", "message" } }`.

| HTTP | Meaning |
|------|---------|
| `201` | Lead created (`source=mujeb`) |
| `200` | Duplicate `external_id` (same `client_id` returned) |
| `400` | Validation error |
| `401` | Missing or invalid API key |
| `403` | Mujeb integration disabled or plan lead quota exceeded |
| `429` | Rate limit exceeded (~120 req/min/IP) |

### Success (201)

```json
{
  "success": true,
  "data": {
    "client_id": 42,
    "patient_file_number": 1001,
    "created_at": "2026-07-25T12:00:00+00:00",
    "duplicate": false
  }
}
```

## Config (tenant JWT)

```
GET {API_BASE_URL}/api/v1/integrations/accounts/mujeb-config/
```

Returns `endpoint_url`, active key prefixes, `integration_status`, and `last_received_at`.
Key create / rotate / revoke reuse Lead API key endpoints:

- `POST /api/v1/integrations/accounts/lead-api-keys/`
- `POST /api/v1/integrations/accounts/lead-api-keys/<id>/rotate/`
- `DELETE /api/v1/integrations/accounts/lead-api-keys/<id>/`

## Mini app setup checklist (Loop CRM app on Mujeb)

1. **Create mini app** (V2) named **Loop CRM**.
2. **Auth** → API Key type → input “Lead API Key” saved to an app field (e.g. `lead_api_key`).
3. **Action** → **Create Lead** with inputs matching the table above (`name` required).
4. **Action subflow** → External request:
   - Method: `POST`
   - URL: `{API_BASE_URL}/api/v1/integrations/leads/mujeb/`
   - Header: `Authorization: Bearer {{lead_api_key}}`
   - Header: `Content-Type: application/json`
   - Body: JSON mapped from action inputs
5. **Install draft** on a test agent, paste a staging `crm_lk_` key, run a flow, confirm lead appears with source **Mujeb**.
6. **Publish** so tenants can install from Mujeb Integrations.

## cURL

```bash
curl -X POST "https://your-api.example.com/api/v1/integrations/leads/mujeb/" \
  -H "Authorization: Bearer crm_lk_YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"name":"Jane Doe","phone":"+9647700000001","external_id":"mujeb-001"}'
```
