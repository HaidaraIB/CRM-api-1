import pytest
from rest_framework import status

from accounts.models import Role, SupervisorPermission, User
from accounts.supervisor_permissions import ensure_supervisor_permissions_for_company
from conftest import api_body
from validation import bindings
from validation.drf import SERIALIZER_FORMS
from validation.registry import get_form


@pytest.mark.django_db
def test_ensure_supervisor_permissions_creates_missing_profile(company):
    orphan = User.objects.create_user(
        username="orphan_supervisor",
        email="orphan_supervisor@test.com",
        password="testpass123",
        company=company,
        role=Role.SUPERVISOR.value,
    )
    assert not SupervisorPermission.objects.filter(user=orphan).exists()

    created = ensure_supervisor_permissions_for_company(company)

    assert created == 1
    sp = SupervisorPermission.objects.get(user=orphan)
    assert sp.is_active is True


@pytest.mark.django_db
def test_supervisor_list_backfills_orphan(authenticated_admin, company):
    orphan = User.objects.create_user(
        username="orphan_supervisor_list",
        email="orphan_supervisor_list@test.com",
        password="testpass123",
        company=company,
        role=Role.SUPERVISOR.value,
    )
    assert not SupervisorPermission.objects.filter(user=orphan).exists()

    response = authenticated_admin.get("/api/v1/supervisors/")
    assert response.status_code == status.HTTP_200_OK

    data = api_body(response)
    results = data.get("results", data)
    user_ids = {item["user"]["id"] for item in results}
    assert orphan.id in user_ids
    assert SupervisorPermission.objects.filter(user=orphan).exists()


def _phone_rule_fields(fields):
    for name, field in fields.items():
        if any(rule.type == "phone_e164" for rule in field.rules):
            yield name


@pytest.mark.django_db
def test_no_serializer_strips_plus_before_catalog_phone_rule():
    """Catalog rules run after validate_<field>, so those must keep the submitted +E.164."""
    bindings.install()
    checked = 0
    for serializer_class, form_id in SERIALIZER_FORMS.items():
        for name in _phone_rule_fields(get_form(form_id).fields):
            validate = getattr(serializer_class(), f"validate_{name}", None)
            if validate is None:
                continue
            checked += 1
            assert validate("+9647715952998").startswith("+"), serializer_class.__name__
    assert checked > 0


def _supervisor_payload(**overrides):
    return {
        "username": "new_supervisor",
        "email": "new_supervisor@test.com",
        "password": "Str0ng-Pass-991",
        "first_name": "New",
        "last_name": "Supervisor",
        "phone": "+9647715952998",
        **overrides,
    }


@pytest.mark.django_db
@pytest.mark.parametrize("sent", ["+9647715952998", "9647715952998", "07715952998"])
def test_create_supervisor_stores_e164_from_any_spelling(authenticated_admin, sent):
    response = authenticated_admin.post("/api/v1/supervisors/", _supervisor_payload(phone=sent), format="json")

    assert response.status_code == status.HTTP_201_CREATED, response.content
    assert User.objects.get(username="new_supervisor").phone == "+9647715952998"


@pytest.mark.django_db
def test_create_supervisor_rejects_malformed_phone(authenticated_admin):
    response = authenticated_admin.post(
        "/api/v1/supervisors/", _supervisor_payload(phone="not-a-phone-1"), format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not User.objects.filter(username="new_supervisor").exists()


@pytest.mark.django_db
def test_create_supervisor_rejects_duplicate_phone_in_any_format(authenticated_admin, company):
    User.objects.create_user(
        username="existing_phone_user",
        email="existing_phone_user@test.com",
        password="testpass123",
        company=company,
        phone="9647715952998",
    )

    response = authenticated_admin.post("/api/v1/supervisors/", _supervisor_payload(), format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not User.objects.filter(username="new_supervisor").exists()
