from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import Role, User
from crm.models import Client, ClientPhoneNumber
from products.models import Supplier
from validation.phones import is_dialable, storable_phone, to_e164, to_wire_digits

E164 = "+9647715952998"


@pytest.mark.parametrize(
    "raw",
    [
        "+9647715952998", "9647715952998", "07715952998", "009647715952998", " 964 771-595-2998 ",
        "٩٦٤٧٧١٥٩٥٢٩٩٨", "+96407715952998", "96407715952998", "+964 0771 595 2998",
    ],
)
def test_every_spelling_reaches_one_e164(raw):
    assert to_e164(raw) == E164
    assert to_wire_digits(raw) == E164[1:]


@pytest.mark.parametrize("raw", ["", None])
def test_empty_stays_empty(raw):
    assert to_e164(raw) == ""
    assert to_wire_digits(raw) == ""


@pytest.mark.parametrize("raw", ["104", "anonymous", "<unknown>", ""])
def test_non_numbers_are_stored_as_received(raw):
    assert not is_dialable(raw)
    assert storable_phone(raw) == raw


def test_storable_phone_keeps_null():
    assert storable_phone(None) is None


@pytest.mark.django_db
def test_direct_writes_store_e164_outside_serializers(company):
    client = Client.objects.create(name="WhatsApp", company=company, phone_number="9647715952998")
    row = ClientPhoneNumber.objects.create(client=client, phone_number="9647715952998", phone_type="mobile")
    supplier = Supplier.objects.create(name="Acme", company=company, phone="07715952998")

    for obj, value in ((client, client.phone_number), (row, row.phone_number), (supplier, supplier.phone)):
        obj.refresh_from_db()
        assert value == E164
    assert client.phone_number == row.phone_number == supplier.phone == E164
    assert row.phone_normalized == "9647715952998"


@pytest.mark.django_db
def test_extension_stays_as_received_on_write(company):
    client = Client.objects.create(name="PBX", company=company, phone_number="104")
    client.refresh_from_db()
    assert client.phone_number == "104"


@pytest.mark.django_db
def test_lead_stored_as_digits_can_be_edited_in_any_format(authenticated_admin, company):
    client = Client.objects.create(name="Lead", company=company)
    Client.objects.filter(pk=client.pk).update(phone_number="9647715952998")

    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/", {"name": "Renamed", "phone_number": "9647715952998"}, format="json"
    )

    assert response.status_code == 200, response.content
    client.refresh_from_db()
    assert client.phone_number == E164


@pytest.mark.django_db
def test_user_patch_stores_e164_and_multipart_works(authenticated_admin, owner_user):
    response = authenticated_admin.patch(
        f"/api/v1/users/{owner_user.id}/", {"phone": "9647715952998"}, format="multipart"
    )

    assert response.status_code == 200, response.content
    owner_user.refresh_from_db()
    assert owner_user.phone == E164


@pytest.mark.django_db
@pytest.mark.parametrize("stored", ["9647715952998", "+9647715952998", "07715952998"])
def test_duplicate_user_phone_is_caught_across_formats(authenticated_admin, company, owner_user, stored):
    User.objects.create_user(
        username="holder", email="holder@test.com", password="testpass123", company=company,
        role=Role.EMPLOYEE.value, phone=stored,
    )

    response = authenticated_admin.patch(
        f"/api/v1/users/{owner_user.id}/", {"phone": E164}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_user_can_resave_own_phone_in_another_format(authenticated_admin, owner_user):
    owner_user.phone = E164
    owner_user.save()

    response = authenticated_admin.patch(
        f"/api/v1/users/{owner_user.id}/", {"phone": "9647715952998"}, format="json"
    )

    assert response.status_code == 200, response.content


@pytest.mark.django_db
def test_every_user_write_stores_e164(company):
    created = User.objects.create_user(
        username="direct", email="direct@test.com", password="testpass123", company=company,
        phone="07715952998",
    )
    created.refresh_from_db()
    assert created.phone == E164


@pytest.mark.django_db
def test_audit_reports_formats_and_duplicates_without_writing_or_leaking(company):
    for name, phone in (("a", "9647715952998"), ("b", E164), ("c", "07701234567"), ("d", "+99912")):
        user = User.objects.create_user(
            username=name, email=f"{name}@test.com", password="testpass123", company=company,
        )
        User.objects.filter(pk=user.pk).update(phone=phone)
    before = list(User.objects.order_by("id").values_list("phone", flat=True))

    out = StringIO()
    call_command("audit_phone_numbers", stdout=out)
    text = out.getvalue()

    assert "users sharing one number in different formats: 1 groups" in text
    assert "local_07" in text
    assert "9647715952998" not in text
    assert list(User.objects.order_by("id").values_list("phone", flat=True)) == before
