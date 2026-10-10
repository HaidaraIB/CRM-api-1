"""Declarative form-schema helpers. JSON shape is the cross-client contract."""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any


@dataclass
class Rule:
    type: str
    params: dict[str, Any] | None = None
    when: str | dict | None = None
    # Historically meant "server skips this rule"; the server now enforces
    # every rule regardless of this flag. Kept for schema authors to flag
    # rules that are genuinely UX-only (e.g. "show an error before blur")
    # and for `enforce_client_only=False` test/tooling call sites.
    client_only: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.params:
            out["params"] = self.params
        if self.when is not None:
            out["when"] = self.when
        if self.client_only:
            out["client_only"] = True
        return out


@dataclass
class Field:
    type: str
    label_key: str
    rules: list[Rule] = dc_field(default_factory=list)
    hint_key: str | None = None
    ui_key: str | None = None
    sample: Any = None
    item: Field | None = None
    fields: dict[str, Field] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type,
            "label_key": self.label_key,
            "rules": [rule.to_json() for rule in self.rules],
        }
        if self.hint_key:
            out["hint_key"] = self.hint_key
        if self.ui_key:
            out["ui_key"] = self.ui_key
        if self.item is not None:
            out["item"] = self.item.to_json()
        if self.fields:
            out["fields"] = {key: child.to_json() for key, child in self.fields.items()}
        return out


@dataclass
class FormSchema:
    id: str
    fields: dict[str, Field]
    public: bool = False
    # UI key -> catalog/API key, for error mapping.
    aliases: dict[str, str] = dc_field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "public": self.public,
            "fields": {key: field.to_json() for key, field in self.fields.items()},
        }
        if self.aliases:
            out["aliases"] = self.aliases
        return out


def rule(type_: str, params: dict | None = None, *, when=None, client_only: bool = False) -> Rule:
    return Rule(type=type_, params=params, when=when, client_only=client_only)


def required(*, client_only: bool = False, when=None) -> Rule:
    return rule("required", when=when, client_only=client_only)


def max_length(max_: int, **kwargs) -> Rule:
    return rule("max_length", {"max": max_}, **kwargs)


def min_length(min_: int, **kwargs) -> Rule:
    return rule("min_length", {"min": min_}, **kwargs)


def string_field(
    label_key: str,
    *rules: Rule,
    hint_key: str | None = None,
    ui_key: str | None = None,
    sample: Any = None,
) -> Field:
    return Field(
        type="string",
        label_key=label_key,
        rules=list(rules),
        hint_key=hint_key,
        ui_key=ui_key,
        sample=sample,
    )


def number_field(label_key: str, *rules: Rule, ui_key: str | None = None, sample: Any = None) -> Field:
    return Field(type="number", label_key=label_key, rules=list(rules), ui_key=ui_key, sample=sample)


def integer_field(label_key: str, *rules: Rule, ui_key: str | None = None, sample: Any = None) -> Field:
    return Field(type="integer", label_key=label_key, rules=list(rules), ui_key=ui_key, sample=sample)


def boolean_field(label_key: str, *rules: Rule, ui_key: str | None = None) -> Field:
    return Field(type="boolean", label_key=label_key, rules=list(rules), ui_key=ui_key, sample=True)


def date_field(label_key: str, *rules: Rule, ui_key: str | None = None) -> Field:
    return Field(type="date", label_key=label_key, rules=list(rules), ui_key=ui_key, sample="2026-10-09")


def name_field(*, max_: int = 255, min_: int = 0, client_required: bool = False, label_key: str = "name") -> Field:
    rules = [required(client_only=client_required), max_length(max_)]
    if min_ > 0:
        rules.append(min_length(min_, client_only=True))
    return string_field(label_key, *rules, sample="Sample")


def email_field(*, client_required: bool = False, label_key: str = "email") -> Field:
    return string_field(
        label_key,
        required(client_only=client_required),
        rule("email"),
        max_length(254),
        sample="user@example.com",
    )


def phone_field(*, client_required: bool = True, label_key: str = "phone", server: bool = False, ui_key: str | None = None) -> Field:
    return string_field(
        label_key,
        required(client_only=client_required),
        rule("phone_e164", client_only=not server),
        ui_key=ui_key,
        sample="+15551234567",
    )


def password_field(label_key: str = "password", *, required_field: bool = True) -> Field:
    rules = []
    if required_field:
        rules.append(required())
    rules.append(rule("password_policy", password_policy_params()))
    return string_field(label_key, *rules, sample="Validpass1")


def confirm_password_field(other: str = "password", label_key: str = "confirmPassword") -> Field:
    return string_field(
        label_key,
        required(),
        rule("matches_field", {"field": other}),
        sample="Validpass1",
    )


def password_policy_params() -> dict[str, Any]:
    """Mirror AUTH_PASSWORD_VALIDATORS. Clients enforce min length, numeric, and a short common list."""
    min_length_value = 8
    try:
        from django.conf import settings

        for entry in getattr(settings, "AUTH_PASSWORD_VALIDATORS", []):
            name = entry.get("NAME", "")
            if name.endswith("MinimumLengthValidator"):
                min_length_value = int(entry.get("OPTIONS", {}).get("min_length", 8))
    except Exception:
        pass
    return {
        "min_length": min_length_value,
        "reject_numeric": True,
        "reject_common": True,
        "reject_similar": True,
        "common_sample": [
            "password",
            "12345678",
            "123456789",
            "qwerty123",
            "11111111",
            "abc12345",
            "password1",
            "iloveyou",
        ],
    }


def object_field(label_key: str, fields: dict[str, Field], *rules: Rule) -> Field:
    return Field(type="object", label_key=label_key, rules=list(rules), fields=fields)


def array_field(
    label_key: str,
    item: Field,
    *rules: Rule,
    ui_key: str | None = None,
) -> Field:
    return Field(type="array", label_key=label_key, rules=list(rules), item=item, ui_key=ui_key)
