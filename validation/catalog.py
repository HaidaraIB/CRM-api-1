"""Build the public catalog document and conformance vectors."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from validation.registry import all_forms
from validation.schema_dsl import Field, FormSchema


def catalog_document(forms: dict[str, FormSchema] | None = None) -> dict[str, Any]:
    selected = forms if forms is not None else all_forms()
    body = {form_id: form.to_json() for form_id, form in selected.items()}
    version = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return {"version": version, "forms": body}


def _sample_for(field: Field, fields: dict[str, Field] | None = None) -> Any:
    if field.sample is not None:
        return field.sample
    if field.type == "object" and field.fields:
        return {key: _sample_for(child, field.fields) for key, child in field.fields.items()}
    if field.type == "array":
        minimum = 0
        for spec in field.rules:
            if spec.type == "array" and spec.params and spec.params.get("min"):
                minimum = int(spec.params["min"])
            if spec.type == "required":
                minimum = max(minimum, 1)
        if minimum <= 0:
            return []
        item = field.item or Field(type="string", label_key="item", sample="item")
        return [_sample_for(item, fields) for _ in range(minimum)]
    if field.type == "number":
        return 1
    if field.type == "integer":
        return 1
    if field.type == "boolean":
        return True
    if field.type == "date":
        return "2026-10-09"
    if any(spec.type == "email" for spec in field.rules):
        return "user@example.com"
    if any(spec.type == "phone_e164" for spec in field.rules):
        return "+15551234567"
    if any(spec.type == "username" for spec in field.rules):
        return "user_name"
    if any(spec.type == "slug" for spec in field.rules):
        return "acme"
    if any(spec.type == "url" for spec in field.rules):
        return "https://example.com"
    if any(spec.type == "password_policy" for spec in field.rules):
        return "Validpass1"
    if any(spec.type == "one_of" for spec in field.rules):
        for spec in field.rules:
            if spec.type == "one_of" and spec.params and spec.params.get("values"):
                return spec.params["values"][0]
    for spec in field.rules:
        if spec.type == "number_range" and spec.params and spec.params.get("min") is not None:
            return spec.params["min"]
    return "Sample"


def _apply_matches(data: dict, fields: dict[str, Field]) -> None:
    for key, field in fields.items():
        for spec in field.rules:
            if spec.type == "matches_field" and spec.params:
                other = spec.params.get("field")
                if other in data:
                    data[key] = data[other]
        if field.type == "object" and field.fields and isinstance(data.get(key), dict):
            _apply_matches(data[key], field.fields)


def valid_sample(form: FormSchema) -> dict:
    data = {key: _sample_for(field, form.fields) for key, field in form.fields.items()}
    _apply_matches(data, form.fields)
    return data


def expected_required_codes(form: FormSchema) -> dict[str, list[str]]:
    """Codes produced for an empty payload with client rules enabled and no context flags."""
    from validation.engine import evaluate

    issues = evaluate(form.id, {}, context={}, partial=False, enforce_client_only=True)
    return {path: [item["code"] for item in items] for path, items in issues.items()}


def build_vectors() -> list[dict[str, Any]]:
    vectors: list[dict[str, Any]] = []
    for form_id, form in all_forms().items():
        vectors.append(
            {
                "form": form_id,
                "data": {},
                "context": {},
                "partial": False,
                "expect": expected_required_codes(form),
            }
        )
        vectors.append(
            {
                "form": form_id,
                "data": valid_sample(form),
                "context": {},
                "partial": False,
                "expect": {},
            }
        )
    return vectors
