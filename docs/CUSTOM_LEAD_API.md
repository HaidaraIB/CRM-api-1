# Custom Lead API

Submit leads from your own website, mobile app, or backend into the CRM using a **per-company API key**.

## Endpoint

```
POST {API_BASE_URL}/api/v1/integrations/leads/inbound/
Content-Type: application/json
```

Example base URL: `https://your-api.example.com`

## Authentication

Send your company API key using **one** of:

- Header: `Authorization: Bearer crm_lk_...`
- Header: `X-Lead-Api-Key: crm_lk_...`

Keys are created in the CRM under **Integrations → Lead API**. The full secret is shown **once** when you generate or rotate a key.

Do not send the global `X-API-Key` (mobile/web/admin) on this endpoint.

## Request body

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lead full name |
| `phone` | Recommended | Phone number |
| `external_id` | Recommended | Your unique submission id (idempotency) |
| `email` | No | Stored in notes |
| `notes` | No | Free text |
| `campaign_id` | No | Must belong to your company |
| `communication_way_id` | No | Channel id (`settings.Channel`) for your company |
| `status_id` | No | Lead status id for your company |
| `priority` | No | `low`, `medium` (default), `high` |
| `type` | No | `fresh` (default), `hot`, `cold` |
| `custom_fields` | No | JSON object; appended to notes |

### Example

```json
{
  "name": "Jane Doe",
  "phone": "+9647700000001",
  "external_id": "form-submission-uuid-123",
  "email": "jane@example.com",
  "notes": "Interested in villa",
  "priority": "high",
  "type": "fresh",
  "custom_fields": {
    "budget": "50000",
    "city": "Baghdad"
  }
}
```

## Responses

All responses use the CRM envelope: `{ "success": true|false, "data"?: ..., "error"?: { "code", "message" } }`.

| HTTP | Meaning |
|------|---------|
| `201` | Lead created |
| `200` | Duplicate `external_id` (same `client_id` returned) |
| `400` | Validation error |
| `401` | Missing or invalid API key |
| `403` | Integration disabled or plan lead quota exceeded |
| `429` | Rate limit exceeded |

### Success (201)

```json
{
  "success": true,
  "data": {
    "client_id": 42,
    "patient_file_number": 1001,
    "created_at": "2026-05-25T12:00:00+00:00",
    "duplicate": false
  }
}
```

### Idempotent replay (200)

```json
{
  "success": true,
  "data": {
    "client_id": 42,
    "patient_file_number": 1001,
    "created_at": "2026-05-25T12:00:00+00:00",
    "duplicate": true
  }
}
```

## Code samples

### cURL

```bash
curl -X POST "https://your-api.example.com/api/v1/integrations/leads/inbound/" \
  -H "Authorization: Bearer crm_lk_YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"name":"Jane Doe","phone":"+9647700000001","external_id":"sub-001"}'
```

### JavaScript (fetch)

```javascript
const res = await fetch("https://your-api.example.com/api/v1/integrations/leads/inbound/", {
  method: "POST",
  headers: {
    "Authorization": "Bearer crm_lk_YOUR_KEY_HERE",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    name: "Jane Doe",
    phone: "+9647700000001",
    external_id: "sub-001",
  }),
});
const json = await res.json();
```

### PHP

```php
$ch = curl_init("https://your-api.example.com/api/v1/integrations/leads/inbound/");
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_HTTPHEADER => [
        "Authorization: Bearer crm_lk_YOUR_KEY_HERE",
        "Content-Type: application/json",
    ],
    CURLOPT_POSTFIELDS => json_encode([
        "name" => "Jane Doe",
        "phone" => "+9647700000001",
        "external_id" => "sub-001",
    ]),
    CURLOPT_RETURNTRANSFER => true,
]);
$response = curl_exec($ch);
curl_close($ch);
```

## Idempotency

If you send the same `external_id` twice, the API returns **200** with the existing `client_id` and `duplicate: true`. Always use a stable id per form submission (UUID, order id, etc.).

## Security

- Use **HTTPS** only in production.
- Store API keys in environment variables or a secrets manager, not in client-side code if the form is public (use your backend as a proxy).
- Rotate keys from Integrations if a key is exposed.
- Revoke unused keys.

## Managing keys (CRM UI)

Company **admins** can:

- View endpoint URL and masked keys: `GET /api/v1/integrations/accounts/lead-api-config/`
- Create key: `POST /api/v1/integrations/accounts/lead-api-keys/` with `{ "name": "Website" }`
- Rotate: `POST /api/v1/integrations/accounts/lead-api-keys/{id}/rotate/`
- Revoke: `DELETE /api/v1/integrations/accounts/lead-api-keys/{id}/`

These routes require a normal CRM user JWT and active subscription.

## Error codes

| code | Description |
|------|-------------|
| `missing_api_key` | No Bearer / X-Lead-Api-Key header |
| `invalid_api_key` | Unknown or revoked key |
| `invalid_json` | Body is not valid JSON |
| `integration_disabled` | Lead API disabled by plan or admin policy |
| `plan_quota_max_clients_exceeded` | Company reached lead limit |
| `admin_required` | Non-admin tried to manage keys |
