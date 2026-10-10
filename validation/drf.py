"""DRF adapter: run the catalog engine from serializer.run_validation."""

from __future__ import annotations

from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.fields import empty

from validation.engine import evaluate

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
        value = super().run_validation(data)
        form_id = SERIALIZER_FORMS.get(self.__class__) or getattr(self, "validation_form", None)
        meta = getattr(self, "Meta", None)
        if not form_id and meta is not None:
            form_id = getattr(meta, "validation_form", None)
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
        )
        if issues:
            raise CatalogValidationError(issues)
        return value
