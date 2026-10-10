"""Turn DRF error payloads into stable `validation.*` codes."""

from __future__ import annotations

from typing import Any

_CODE_ALIASES = {
    "required": "validation.required",
    "blank": "validation.required",
    "null": "validation.required",
    "min_length": "validation.min_length",
    "max_length": "validation.max_length",
    "min_value": "validation.number_range",
    "max_value": "validation.number_range",
    "invalid": "validation.invalid",
    "invalid_choice": "validation.one_of",
    "invalid_email": "validation.email",
}

# Keys that carry a business code / metadata, not field validation errors.
# Also imported by crm_saas_api/exception_handler.py as the canonical list —
# keep it here, don't fork a second copy.
SKIP_KEYS = frozenset(
    {
        "error",
        "message",
        "detail",
        "code",
        "error_key",
        "subscriptionId",
        "subscription_id",
        "paymentToken",
        "hint",
        "actions",
        "change_credentials_note",
        "verify_email_url",
        "verify_phone_url",
    }
)
_SKIP_KEYS = SKIP_KEYS


def normalize_code(code: str | None) -> str:
    raw = (code or "invalid").strip()
    if raw.startswith("validation."):
        return raw
    return _CODE_ALIASES.get(raw, f"validation.{raw}")


def issue_from_detail(value: Any, params: dict | None = None) -> dict[str, Any]:
    code = normalize_code(getattr(value, "code", None) if not isinstance(value, dict) else value.get("code"))
    message = value.get("message") if isinstance(value, dict) else str(value)
    merged = dict(params or {})
    if isinstance(value, dict):
        extra = value.get("params") or {}
        if isinstance(extra, dict):
            merged = {**extra, **merged}
    return {"code": code, "params": merged, "message": message}


def split_catalog_issues(catalog_issues: dict) -> tuple[dict[str, list], list]:
    fields: dict[str, list] = {}
    non_field: list = []
    for key, items in (catalog_issues or {}).items():
        normalized = [issue_from_detail(item) if not _is_issue(item) else item for item in items]
        if key in ("non_field", "non_field_errors"):
            non_field.extend(normalized)
        else:
            fields[key] = normalized
    return fields, non_field


def _is_issue(item: Any) -> bool:
    return isinstance(item, dict) and "code" in item and "message" in item


def coded_from_drf(data: Any) -> tuple[dict[str, list], list]:
    fields: dict[str, list] = {}
    non_field: list = []
    _walk(data, "", fields, non_field)
    return fields, non_field


def _walk(data: Any, prefix: str, fields: dict, non_field: list) -> None:
    if isinstance(data, dict):
        if _is_issue(data) and "params" in data:
            _store(prefix, [data], fields, non_field)
            return
        for key, value in data.items():
            if key in _SKIP_KEYS:
                continue
            if key in ("non_field_errors", "non_field"):
                _walk(value, "non_field", fields, non_field)
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            _walk(value, path, fields, non_field)
        return
    if isinstance(data, (list, tuple)):
        if data and all(not isinstance(item, (list, dict)) for item in data):
            _store(prefix, [issue_from_detail(item) for item in data], fields, non_field)
            return
        for item in data:
            _walk(item, prefix, fields, non_field)
        return
    if prefix:
        _store(prefix, [issue_from_detail(data)], fields, non_field)
    elif data not in (None, ""):
        non_field.append(issue_from_detail(data))


def _store(prefix: str, items: list, fields: dict, non_field: list) -> None:
    if not prefix or prefix in ("non_field", "non_field_errors"):
        non_field.extend(items)
        return
    fields.setdefault(prefix, []).extend(items)
