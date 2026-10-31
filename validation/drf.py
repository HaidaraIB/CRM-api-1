"""DRF adapter: run the catalog engine from serializer.run_validation."""

from __future__ import annotations

from collections.abc import Mapping

from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.fields import empty

from validation.engine import evaluate
from validation.messages import default_message
from validation.phones import is_real_number, to_e164, to_wire_digits
from validation.registry import get_form

# Twilio issues test and short-range numbers a numbering plan does not list.
PROVIDER_OWNED_NUMBER_FORMS = {"twilio_settings.update"}

# serializer class -> form id. Filled by bindings.install().
SERIALIZER_FORMS: dict[type, str] = {}


class CatalogValidationError(ValidationError):
    def __init__(self, catalog_issues: dict):
        self.catalog_issues = catalog_issues
        detail = {}
        for field, items in catalog_issues.items():
            key = "non_field_errors" if field in ("non_field", "non_field_errors") else field
            detail[key] = [
                ErrorDetail(item.get("message") or item["code"], code=item["code"])
                for item in items
            ]
        super().__init__(detail)


def _normalize_phones(fields: dict, data):
    """Copy of `data` with every phone_e164 value in +E.164, before anything reads it."""
    if not isinstance(data, Mapping):
        return data
    copied = data.copy()
    for key, field in fields.items():
        for name in (key, field.ui_key):
            if not name or name not in copied:
                continue
            value = copied[name]
            if field.fields:
                copied[name] = _normalize_phones(field.fields, value)
            elif field.item is not None and field.item.fields and isinstance(value, list):
                copied[name] = [_normalize_phones(field.item.fields, row) for row in value]
            elif isinstance(value, str) and any(rule.type == "phone_e164" for rule in field.rules):
                copied[name] = to_e164(value)
    return copied


def _phone_values(fields: dict, data, prefix: str = ""):
    """Yield (error path, value) for every phone_e164 field in a payload."""
    if not isinstance(data, Mapping):
        return
    for key, field in fields.items():
        name = key if key in data else field.ui_key if field.ui_key in data else None
        if name is None:
            continue
        path = f"{prefix}.{key}" if prefix else key
        value = data[name]
        if field.fields:
            yield from _phone_values(field.fields, value, path)
        elif field.item is not None and field.item.fields and isinstance(value, list):
            for index, row in enumerate(value):
                yield from _phone_values(field.item.fields, row, f"{path}.{index}")
        elif isinstance(value, str) and value and any(rule.type == "phone_e164" for rule in field.rules):
            yield path, value


def _stored_phones(instance) -> set[str]:
    """Numbers already on the row, so an old invalid one cannot block unrelated edits."""
    if instance is None:
        return set()
    stored = {getattr(instance, name, None) for name in ("phone", "phone_number")}
    rows = getattr(instance, "phone_numbers", None)
    if rows is not None:
        stored.update(row.phone_number for row in rows.all())
    return {to_wire_digits(value) for value in stored if value}


def _unreal_phone_issues(form_id: str, data, instance) -> dict:
    if form_id in PROVIDER_OWNED_NUMBER_FORMS:
        return {}
    stored = _stored_phones(instance)
    code = "validation.phone_e164"
    return {
        path: [{"code": code, "params": {}, "message": default_message(code)}]
        for path, value in _phone_values(get_form(form_id).fields, data)
        if to_wire_digits(value) not in stored and not is_real_number(value)
    }


class CatalogValidatedSerializerMixin:
    """
    Opt a serializer into catalog checks via `validation_form` (set by bindings).

    Runs after DRF field validation. The server enforces every catalog rule
    whenever the field is present — including format/range/one_of rules marked
    `client_only` (that flag never meant "the server may accept a malformed
    value"). The one exception is `required`/`required_if`: `client_only` on
    those two rule types specifically means "the client should nudge for this,
    but the server genuinely treats it as optional" (see engine._rule_active),
    so presence itself is never enforced for those.
    """

    def run_validation(self, data=empty):
        form_id = SERIALIZER_FORMS.get(self.__class__) or getattr(self, "validation_form", None)
        meta = getattr(self, "Meta", None)
        if not form_id and meta is not None:
            form_id = getattr(meta, "validation_form", None)
        if form_id:
            data = _normalize_phones(get_form(form_id).fields, data)
        value = super().run_validation(data)
        if not form_id or not isinstance(value, dict):
            return value
        context = {}
        serializer_context = getattr(self, "context", None) or {}
        flags = serializer_context.get("validation_flags") or {}
        if isinstance(flags, dict):
            context.update(flags)
        request = serializer_context.get("request")
        user = getattr(request, "user", None) if request is not None else None
        if user is not None and getattr(user, "is_authenticated", False):
            context["user"] = user
        issues = evaluate(
            form_id,
            value,
            context=context,
            partial=bool(getattr(self, "partial", False)),
            # False: required/required_if marked client_only stay server-optional
            # (see engine._rule_active) — every other client_only rule type is
            # enforced unconditionally by the engine regardless of this flag.
            enforce_client_only=False,
        ) or _unreal_phone_issues(form_id, data, getattr(self, "instance", None))
        if issues:
            raise CatalogValidationError(issues)
        return value
