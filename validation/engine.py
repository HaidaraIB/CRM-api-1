"""Evaluate a catalog form against a payload. Strategy per rule type."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from validation.custom_rules import get_custom
from validation.messages import default_message
from validation.registry import get_form
from validation.schema_dsl import Field, Rule

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")


def latin_digits(value: str) -> str:
    return value.translate(_ARABIC_DIGITS)


def issue(code: str, params: dict | None = None) -> dict[str, Any]:
    params = dict(params or {})
    full = code if str(code).startswith("validation.") else f"validation.{code}"
    return {"code": full, "params": params, "message": default_message(full, params)}


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return True
    return False


def _when_matches(when, context: dict) -> bool:
    if when is None:
        return True
    if isinstance(when, str):
        return bool(context.get(when))
    if isinstance(when, dict):
        flag = when.get("flag")
        if "equals" in when:
            return context.get(flag) == when["equals"]
        if "not" in when:
            return context.get(flag) != when["not"]
        return bool(context.get(flag))
    return True


def _rule_active(rule: Rule, context: dict, *, enforce_client_only: bool) -> bool:
    # `client_only` is a presence/business-rules signal, not a format one: across
    # this catalog it is used specifically to mark a field the *client* should
    # nudge for but the server genuinely treats as optional (nullable DB column,
    # write-only secret that doesn't need resubmitting, etc.) — confirmed by
    # auditing every such field against its model. So client_only only ever
    # exempts `required`/`required_if` from server enforcement; every other rule
    # type (format, range, one_of, pattern, ...) is enforced whenever the field
    # is actually present, client_only or not — there's no legitimate reason for
    # the server to silently accept a malformed value just because the UI didn't
    # double-check it first.
    if rule.client_only and rule.type in ("required", "required_if") and not enforce_client_only:
        return False
    if rule.type == "remote":
        return False
    return _when_matches(rule.when, context)


def _lookup(data: dict, key: str, field: Field) -> tuple[Any, bool]:
    if isinstance(data, dict) and key in data:
        return data.get(key), True
    if field.ui_key and isinstance(data, dict) and field.ui_key in data:
        return data.get(field.ui_key), True
    return None, False


def _normalize(field: Field, value: Any) -> Any:
    if isinstance(value, str):
        value = latin_digits(value)
        if field.type == "string" and not any(rule.type == "password_policy" for rule in field.rules):
            value = value.strip()
        if any(rule.type == "phone_e164" for rule in field.rules):
            value = re.sub(r"[\s()\-]", "", value.strip())
        if any(rule.type == "email" for rule in field.rules):
            value = value.strip().lower()
    return value


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def _sibling(data: dict, name: str, fields: dict[str, Field]) -> Any:
    if name in data:
        return data.get(name)
    for key, field in fields.items():
        if field.ui_key == name and key in data:
            return data.get(key)
        if key == name and field.ui_key and field.ui_key in data:
            return data.get(field.ui_key)
    return data.get(name)


def _apply_rule(rule: Rule, value: Any, field: Field, data: dict, context: dict, fields: dict[str, Field]):
    handler = _RULES.get(rule.type)
    if rule.type.startswith("custom:"):
        name = rule.type.split(":", 1)[1]
        custom = get_custom(name)
        if custom is None:
            return None
        params = dict(rule.params or {})
        return custom(value, params, data, context)
    if handler is None:
        return None
    return handler(value, rule.params or {}, field, data, context, fields)


def _required(value, params, field, data, context, fields):
    if is_empty(value):
        return issue("validation.required")
    return None


def _min_length(value, params, field, data, context, fields):
    if is_empty(value) or not isinstance(value, str):
        return None
    minimum = int(params.get("min", 0))
    if len(value) < minimum:
        return issue("validation.min_length", {"min": minimum})
    return None


def _max_length(value, params, field, data, context, fields):
    if is_empty(value) or not isinstance(value, str):
        return None
    maximum = int(params.get("max", 0))
    if len(value) > maximum:
        return issue("validation.max_length", {"max": maximum})
    return None


def _pattern(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    regex = params.get("regex") or ""
    if not re.fullmatch(regex, str(value)):
        return issue("validation.pattern", {"regex": regex})
    return None


def _email(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    if not _EMAIL_RE.fullmatch(str(value).strip()):
        return issue("validation.email")
    return None


def _phone(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    text = re.sub(r"[\s()\-]", "", str(value).strip())
    if not _PHONE_RE.fullmatch(text) or len(text) < 8:
        return issue("validation.phone_e164")
    return None


def _username(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    text = str(value).strip()
    minimum = int(params.get("min", 3))
    if len(text) < minimum or not _USERNAME_RE.fullmatch(text):
        return issue("validation.username", {"min": minimum})
    return None


def _slug(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    if not _SLUG_RE.fullmatch(str(value).strip()):
        return issue("validation.slug")
    return None


def _url(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return issue("validation.url")
    return None


def _number_range(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    number = _as_decimal(value)
    if number is None:
        return issue("validation.number_range", params)
    out = {}
    if params.get("min") is not None:
        out["min"] = params["min"]
        if number < Decimal(str(params["min"])):
            return issue("validation.number_range", out)
    if params.get("max") is not None:
        out["max"] = params["max"]
        if number > Decimal(str(params["max"])):
            return issue("validation.number_range", out)
    return None


def _integer(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    if isinstance(value, bool):
        return issue("validation.integer")
    if isinstance(value, int):
        return None
    if isinstance(value, float) and value.is_integer():
        return None
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return None
    return issue("validation.integer")


def _decimal_places(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    number = _as_decimal(value)
    if number is None:
        return issue("validation.decimal_places", params)
    places = int(params.get("max", 0))
    exponent = number.as_tuple().exponent
    digits = 0 if exponent >= 0 else -exponent
    if digits > places:
        return issue("validation.decimal_places", {"places": places, "max": places})
    return None


def _one_of(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    allowed = list(params.get("values") or [])
    if value in allowed:
        return None
    if str(value) in {str(item) for item in allowed}:
        return None
    return issue("validation.one_of", {"values": allowed})


def _matches_field(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    other_name = params.get("field")
    other = _sibling(data, other_name, fields)
    if other != value:
        return issue("validation.matches_field", {"field": other_name})
    return None


def _required_if(value, params, field, data, context, fields):
    other_name = params.get("field")
    other = _sibling(data, other_name, fields)
    needed = False
    if "equals" in params:
        needed = other == params.get("equals") or str(other) == str(params.get("equals"))
    elif params.get("not_empty"):
        needed = not is_empty(other)
    elif "in" in params:
        needed = other in params.get("in") or str(other) in {str(item) for item in params.get("in")}
    if needed and is_empty(value):
        return issue("validation.required_if", {"field": other_name})
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date() if "T" in text else date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _date_range(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    current = _parse_date(value)
    if current is None:
        return issue("validation.date")
    if params.get("gte_field"):
        other = _parse_date(_sibling(data, params["gte_field"], fields))
        if other is not None and current < other:
            return issue("validation.date_range", {"gte_field": params["gte_field"]})
    if params.get("lte_field"):
        other = _parse_date(_sibling(data, params["lte_field"], fields))
        if other is not None and current > other:
            return issue("validation.date_range", {"lte_field": params["lte_field"]})
    return None


def _file(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    size = None
    mime = None
    if isinstance(value, dict):
        size = value.get("size")
        mime = value.get("type") or value.get("mime")
    else:
        size = getattr(value, "size", None)
        mime = getattr(value, "content_type", None)
    max_bytes = params.get("max_bytes")
    if max_bytes is not None and size is not None and int(size) > int(max_bytes):
        return issue("validation.file", {"reason": "too_large", "max_bytes": max_bytes})
    allowed = params.get("mime") or []
    if allowed and mime and mime not in allowed:
        return issue("validation.file", {"reason": "mime", "mime": list(allowed)})
    return None


def _password_policy(value, params, field, data, context, fields):
    if is_empty(value):
        return None
    text = str(value)
    minimum = int(params.get("min_length", 8))
    if len(text) < minimum:
        return issue("validation.password_policy", {"reason": "min_length", "min": minimum})
    if params.get("reject_numeric", True) and text.isdigit():
        return issue("validation.password_policy", {"reason": "numeric", "min": minimum})
    sample = {item.lower() for item in (params.get("common_sample") or [])}
    if params.get("reject_common") and text.lower() in sample:
        return issue("validation.password_policy", {"reason": "common", "min": minimum})
    if params.get("reject_common"):
        try:
            from django.contrib.auth.password_validation import CommonPasswordValidator

            CommonPasswordValidator()(text)
        except Exception as exc:
            messages = getattr(exc, "messages", None)
            if messages:
                return issue("validation.password_policy", {"reason": "common", "min": minimum})
    if params.get("reject_similar"):
        user = context.get("user")
        if user is not None:
            try:
                from django.contrib.auth.password_validation import UserAttributeSimilarityValidator

                UserAttributeSimilarityValidator()(text, user)
            except Exception as exc:
                messages = getattr(exc, "messages", None)
                if messages:
                    return issue("validation.password_policy", {"reason": "similar", "min": minimum})
    return None


def _array(value, params, field, data, context, fields):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return issue("validation.array")
    minimum = params.get("min")
    maximum = params.get("max")
    if minimum is not None and len(value) < int(minimum):
        return issue("validation.array", {"min": int(minimum)})
    if maximum is not None and len(value) > int(maximum):
        return issue("validation.array", {"max": int(maximum)})
    return None


_RULES = {
    "required": _required,
    "min_length": _min_length,
    "max_length": _max_length,
    "pattern": _pattern,
    "email": _email,
    "phone_e164": _phone,
    "username": _username,
    "slug": _slug,
    "url": _url,
    "number_range": _number_range,
    "integer": _integer,
    "decimal_places": _decimal_places,
    "one_of": _one_of,
    "matches_field": _matches_field,
    "required_if": _required_if,
    "date_range": _date_range,
    "file": _file,
    "password_policy": _password_policy,
    "array": _array,
}


def evaluate(
    form_id: str,
    data: dict | None,
    *,
    context: dict | None = None,
    partial: bool = False,
    enforce_client_only: bool = False,
) -> dict[str, list[dict]]:
    """Return `{field_path: [issue, ...]}`. `non_field` collects form-level issues."""
    form = get_form(form_id)
    context = context or {}
    payload = data or {}
    issues: dict[str, list[dict]] = {}
    _walk(
        form.fields,
        payload if isinstance(payload, dict) else {},
        "",
        issues,
        context,
        partial,
        enforce_client_only,
    )
    return {key: value for key, value in issues.items() if value}


def _add(issues: dict, path: str, found: dict | None) -> None:
    if not found:
        return
    key = path or "non_field"
    issues.setdefault(key, []).append(found)


def _walk(
    fields: dict[str, Field],
    data: dict,
    prefix: str,
    issues: dict,
    context: dict,
    partial: bool,
    enforce_client_only: bool,
) -> None:
    for key, field in fields.items():
        path = f"{prefix}.{key}" if prefix else key
        value, present = _lookup(data, key, field)
        if present:
            value = _normalize(field, value)
        required_failed = False
        for spec in field.rules:
            if not _rule_active(spec, context, enforce_client_only=enforce_client_only):
                continue
            if spec.type == "required" and partial and not present:
                continue
            if not present and spec.type != "required" and spec.type != "required_if":
                continue
            found = _apply_rule(spec, value, field, data, context, fields)
            if found:
                _add(issues, path, found)
                if spec.type in ("required", "required_if"):
                    required_failed = True
                    break
        if required_failed or (not present and partial):
            continue
        if not present:
            continue
        if field.type == "object" and field.fields and isinstance(value, dict):
            _walk(field.fields, value, path, issues, context, partial, enforce_client_only)
        elif field.type == "array" and field.item is not None and isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                item_path = f"{path}.{index}"
                if field.item.fields and isinstance(item, dict):
                    _walk(field.item.fields, item, item_path, issues, context, partial=False, enforce_client_only=enforce_client_only)
                else:
                    _validate_scalar(field.item, item, item_path, item if isinstance(item, dict) else data, issues, context, enforce_client_only)


def _validate_scalar(field, value, path, data, issues, context, enforce_client_only):
    value = _normalize(field, value)
    for spec in field.rules:
        if not _rule_active(spec, context, enforce_client_only=enforce_client_only):
            continue
        found = _apply_rule(spec, value, field, data if isinstance(data, dict) else {}, context, {})
        if found:
            _add(issues, path, found)
            if spec.type in ("required", "required_if"):
                break
