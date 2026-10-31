from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

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


REAL_BUT_UNLISTED = "+964123456"
NOT_REAL = "+96412345678901"


@pytest.fixture
def strict(settings):
    settings.PHONE_STRICT_VALIDATION = True


@pytest.mark.django_db
def test_create_lead_rejects_a_number_that_does_not_exist(authenticated_admin, company, strict):
    response = authenticated_admin.post(
        "/api/v1/clients/",
        {"name": "Fake", "priority": "low", "type": "fresh", "company": company.id, "phone_number": NOT_REAL},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["fields"]["phone_number"][0]["code"] == "validation.phone_e164"
    assert not Client.objects.filter(name="Fake").exists()


@pytest.mark.django_db
def test_invalid_number_in_phone_rows_is_rejected(authenticated_admin, company, strict):
    client = Client.objects.create(name="Lead", company=company, phone_number=E164)

    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/",
        {"phone_numbers": [{"phone_number": NOT_REAL, "phone_type": "mobile", "is_primary": True}]},
        format="json",
    )

    assert response.status_code == 400
    assert "phone_numbers.0.phone_number" in response.json()["error"]["fields"]


@pytest.mark.django_db
def test_lead_with_an_old_invalid_number_can_still_be_edited(authenticated_admin, company, strict):
    client = Client.objects.create(name="Lead", company=company)
    Client.objects.filter(pk=client.pk).update(phone_number=NOT_REAL)
    ClientPhoneNumber.objects.create(client=client, phone_number=NOT_REAL, phone_type="mobile", is_primary=True)

    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/",
        {
            "name": "Renamed",
            "phone_number": NOT_REAL,
            "phone_numbers": [{"phone_number": NOT_REAL, "phone_type": "mobile", "is_primary": True}],
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    client.refresh_from_db()
    assert client.name == "Renamed"


@pytest.mark.django_db
def test_real_number_is_accepted_when_strict(authenticated_admin, company, strict):
    response = authenticated_admin.post(
        "/api/v1/clients/",
        {"name": "Real", "priority": "low", "type": "fresh", "company": company.id, "phone_number": "07715952998"},
        format="json",
    )

    assert response.status_code == 201, response.content
    assert Client.objects.get(name="Real").phone_number == E164
    assert ClientPhoneNumber.objects.get(client__name="Real").phone_normalized == E164[1:]


@pytest.mark.django_db
def test_user_cannot_take_an_unreal_number_but_can_keep_an_old_one(authenticated_admin, owner_user, strict):
    url = f"/api/v1/users/{owner_user.id}/"
    assert authenticated_admin.patch(url, {"phone": NOT_REAL}, format="json").status_code == 400

    User.objects.filter(pk=owner_user.pk).update(phone=NOT_REAL)
    response = authenticated_admin.patch(url, {"first_name": "Same", "phone": NOT_REAL}, format="json")
    assert response.status_code == 200, response.content


@pytest.mark.django_db
def test_supervisor_with_an_unreal_number_is_rejected(authenticated_admin, strict):
    response = authenticated_admin.post(
        "/api/v1/supervisors/",
        {
            "username": "fake_sup", "email": "fake_sup@test.com", "password": "Str0ng-Pass-991",
            "first_name": "A", "last_name": "B", "phone": NOT_REAL,
        },
        format="json",
    )

    assert response.status_code == 400
    assert not User.objects.filter(username="fake_sup").exists()


@pytest.mark.django_db
def test_same_number_on_a_second_lead_is_refused(authenticated_admin, company, strict):
    first = Client.objects.create(name="First", company=company, phone_number=E164)
    ClientPhoneNumber.objects.create(client=first, phone_number=E164, phone_type="mobile", is_primary=True)

    for spelling in (E164, "07715952998", "+96407715952998"):
        response = authenticated_admin.post(
            "/api/v1/clients/",
            {"name": "Second", "priority": "low", "type": "fresh", "company": company.id, "phone_number": spelling},
            format="json",
        )
        assert response.status_code == 400, spelling
        assert "already" in response.content.decode().lower(), response.content
    assert not Client.objects.filter(name="Second").exists()


@pytest.mark.django_db
def test_database_refuses_a_duplicate_even_past_the_api(company):
    first = Client.objects.create(name="First", company=company)
    second = Client.objects.create(name="Second", company=company)
    ClientPhoneNumber.objects.create(client=first, phone_number=E164, phone_type="mobile")

    with pytest.raises(IntegrityError), transaction.atomic():
        ClientPhoneNumber.objects.create(client=second, phone_number="+96407715952998", phone_type="mobile")


@pytest.mark.django_db
def test_audit_reports_lead_number_health(company):
    lead = Client.objects.create(name="Lead", company=company)
    Client.objects.filter(pk=lead.pk).update(phone_number=E164)
    other = Client.objects.create(name="Other", company=company, phone_number=E164)
    row = ClientPhoneNumber.objects.create(client=other, phone_number=E164, phone_type="mobile")
    ClientPhoneNumber.objects.filter(pk=row.pk).update(phone_normalized="wrong")

    out = StringIO()
    call_command("audit_phone_numbers", stdout=out)

    assert "stale_phone_keys: 1" in out.getvalue()
    assert "leads_with_number_but_no_phone_row: 1" in out.getvalue()
    assert "numbers_on_more_than_one_lead: 1" in out.getvalue()


@pytest.mark.django_db
def test_rekey_repairs_keys_and_skips_collisions(company):
    a = Client.objects.create(name="A", company=company)
    b = Client.objects.create(name="B", company=company)
    c = Client.objects.create(name="C", company=company)
    keeper = ClientPhoneNumber.objects.create(client=a, phone_number=E164, phone_type="mobile")
    clash = ClientPhoneNumber.objects.create(client=b, phone_number="+9647701234567", phone_type="mobile")
    stale = ClientPhoneNumber.objects.create(client=c, phone_number="+9647801234567", phone_type="mobile")
    ClientPhoneNumber.objects.filter(pk=clash.pk).update(phone_number=E164, phone_normalized="old1")
    ClientPhoneNumber.objects.filter(pk=stale.pk).update(phone_normalized="old2")

    preview = StringIO()
    call_command("phone_cleanup", "rekey", stdout=preview)
    stale.refresh_from_db()
    assert stale.phone_normalized == "old2"
    assert "Would fix 1 keys, 1 left" in preview.getvalue()

    call_command("phone_cleanup", "rekey", "--commit", stdout=StringIO())
    stale.refresh_from_db()
    clash.refresh_from_db()
    assert stale.phone_normalized == "9647801234567"
    assert clash.phone_normalized == "old1"
    assert keeper.phone_normalized == E164[1:]
