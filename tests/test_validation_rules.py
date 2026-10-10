"""Catalog rule engine tests."""

import pytest

from validation.engine import evaluate
from validation.schemas import load_all


@pytest.fixture(scope="module", autouse=True)
def _schemas():
    load_all()


def test_required_and_max_length():
    errors = evaluate("tag.upsert", {"name": ""}, enforce_client_only=True)
    assert errors["name"][0]["code"] == "validation.required"

    errors = evaluate("tag.upsert", {"name": "x" * 300}, enforce_client_only=True)
    assert errors["name"][0]["code"] == "validation.max_length"
    assert errors["name"][0]["params"]["max"] == 255


def test_email_phone_and_password_policy():
    errors = evaluate(
        "auth.forgot_password",
        {"email": "not-an-email"},
        enforce_client_only=True,
    )
    assert errors["email"][0]["code"] == "validation.email"

    errors = evaluate("profile.update", {"phone": "12345"}, enforce_client_only=True)
    assert any(item["code"] == "validation.phone_e164" for item in errors["phone"])

    errors = evaluate(
        "auth.change_password",
        {"current_password": "Validpass1", "new_password": "12345678", "confirm_password": "12345678"},
        enforce_client_only=True,
    )
    assert errors["new_password"][0]["code"] == "validation.password_policy"

    errors = evaluate(
        "auth.change_password",
        {"current_password": "Validpass1", "new_password": "Validpass1", "confirm_password": "Otherpass1"},
        enforce_client_only=True,
    )
    assert errors["confirm_password"][0]["code"] == "validation.matches_field"


def test_conditional_and_partial_update():
    errors = evaluate(
        "lead.upsert",
        {"name": "Ada"},
        context={},
        enforce_client_only=True,
    )
    assert "company" not in errors

    errors = evaluate(
        "lead.upsert",
        {"name": "Ada"},
        context={"requireCompany": True},
        enforce_client_only=True,
    )
    assert errors["company"][0]["code"] == "validation.required"

    errors = evaluate("tag.upsert", {}, partial=True, enforce_client_only=True)
    assert errors == {}


def test_arabic_indic_digits_and_nested_register():
    errors = evaluate(
        "auth.two_factor",
        {"code": "١٢٣٤٥٦"},
        enforce_client_only=True,
    )
    assert errors == {}

    errors = evaluate("auth.register", {}, enforce_client_only=True)
    assert errors["company"][0]["code"] == "validation.required"
    assert errors["owner"][0]["code"] == "validation.required"


def test_server_never_enforces_presence_for_client_only_required_fields():
    # `CatalogValidatedSerializerMixin` calls `evaluate(..., enforce_client_only=False)` —
    # this is the real, audited behavior: every `required()`/`required_if` rule marked
    # `client_only=True` across the catalog maps to a field that's genuinely optional
    # server-side (nullable DB column, write-only secret that need not be resubmitted,
    # etc). The client still nudges for these; the server must not reject their absence.
    errors = evaluate("lead.upsert", {"name": "Ada"}, enforce_client_only=False)
    assert errors == {}


def test_server_still_enforces_format_rules_on_client_only_fields_when_present():
    # `client_only` only ever exempts `required`/`required_if`. Every other rule type
    # (format, range, one_of, pattern, ...) is enforced whenever the field is present,
    # regardless of `client_only` — there's no reason to silently accept a malformed
    # value just because the UI didn't double-check it first.
    errors = evaluate(
        "lead.upsert",
        {"name": "Ada", "phone_number": "not-a-phone"},
        enforce_client_only=False,
    )
    assert errors["phone_number"][0]["code"] == "validation.phone_e164"


def test_enforce_client_only_true_shows_the_full_client_contract():
    # Vector/catalog-export tooling (build_vectors, conformance tests) wants the "what
    # should the client require" view — the opt-in flag still works for that.
    errors = evaluate("lead.upsert", {"name": "Ada"}, enforce_client_only=True)
    assert errors["phone_number"][0]["code"] == "validation.required"
    assert errors["communication_way"][0]["code"] == "validation.required"
    assert errors["status"][0]["code"] == "validation.required"


def test_partial_update_does_not_demand_untouched_required_fields():
    # PATCHing a single field must not suddenly require every other required
    # field on the resource — `partial=True` skips `required` for absent keys.
    errors = evaluate(
        "lead.upsert",
        {"notes": "called back"},
        partial=True,
        enforce_client_only=True,
    )
    assert errors == {}

    errors = evaluate(
        "deal.upsert",
        {"description": "updated notes"},
        partial=True,
        enforce_client_only=True,
    )
    assert errors == {}

    errors = evaluate(
        "auth.register",
        {"company": {"name": "Acme"}},
        partial=True,
        enforce_client_only=True,
    )
    assert "owner" not in errors


def test_every_form_vector_round_trips():
    from validation.catalog import build_vectors

    failures = []
    for vector in build_vectors():
        actual = evaluate(
            vector["form"],
            vector["data"],
            context=vector.get("context") or {},
            partial=vector.get("partial", False),
            enforce_client_only=True,
        )
        actual_codes = {path: [item["code"] for item in items] for path, items in actual.items()}
        if actual_codes != vector["expect"]:
            failures.append((vector["form"], vector["data"], vector["expect"], actual_codes))
    assert failures == []
