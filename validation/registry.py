"""Form-schema registry. Strategies for custom rules live in custom_rules."""

from __future__ import annotations

from validation.schema_dsl import FormSchema

_FORMS: dict[str, FormSchema] = {}


def register_form(
    form_id: str,
    fields: dict,
    *,
    public: bool = False,
    aliases: dict | None = None,
) -> FormSchema:
    if form_id in _FORMS:
        raise ValueError(f"Form {form_id!r} is already registered")
    schema = FormSchema(id=form_id, fields=fields, public=public, aliases=aliases or {})
    _FORMS[form_id] = schema
    return schema


def get_form(form_id: str) -> FormSchema:
    try:
        return _FORMS[form_id]
    except KeyError as exc:
        raise KeyError(form_id) from exc


def all_forms() -> dict[str, FormSchema]:
    return dict(_FORMS)


def public_forms() -> dict[str, FormSchema]:
    return {key: form for key, form in _FORMS.items() if form.public}


def clear_forms() -> None:
    """Test helper."""
    _FORMS.clear()
