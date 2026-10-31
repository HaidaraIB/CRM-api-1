import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from accounts.models import User
from crm.models import Client, ClientPhoneNumber

E164 = "+9647715952998"
DIGITS = "9647715952998"


def run(*args):
    out = StringIO()
    call_command("convert_phone_numbers", *args, stdout=out)
    return out.getvalue()


def stored_lead(company, phone):
    client = Client.objects.create(name="Lead", company=company)
    Client.objects.filter(pk=client.pk).update(phone_number=phone)
    return client


def stored_user(company, name, phone):
    user = User.objects.create_user(
        username=name, email=f"{name}@test.com", password="testpass123", company=company
    )
    User.objects.filter(pk=user.pk).update(phone=phone)
    return user


@pytest.mark.django_db
def test_default_is_a_dry_run(company):
    lead = stored_lead(company, DIGITS)

    output = run()

    lead.refresh_from_db()
    assert lead.phone_number == DIGITS
    assert "'would_convert': 1" in output
    assert "Dry run" in output


@pytest.mark.django_db
def test_apply_requires_an_undo_file():
    with pytest.raises(CommandError):
        run("--apply")


@pytest.mark.django_db
def test_apply_converts_and_restore_puts_it_back(company, tmp_path):
    lead = stored_lead(company, DIGITS)
    row = ClientPhoneNumber.objects.create(client=lead, phone_number=E164, phone_type="mobile")
    ClientPhoneNumber.objects.filter(pk=row.pk).update(phone_number=DIGITS)
    undo = tmp_path / "undo.json"

    run("--apply", "--undo-file", str(undo))

    lead.refresh_from_db()
    row.refresh_from_db()
    assert lead.phone_number == row.phone_number == E164
    assert row.phone_normalized == DIGITS
    assert len(json.loads(undo.read_text())) == 2

    run("--restore", str(undo))

    lead.refresh_from_db()
    row.refresh_from_db()
    assert lead.phone_number == row.phone_number == DIGITS


@pytest.mark.django_db
def test_second_apply_changes_nothing(company, tmp_path):
    stored_lead(company, DIGITS)
    undo = tmp_path / "undo.json"
    run("--apply", "--undo-file", str(undo))

    run("--apply", "--undo-file", str(undo))

    assert json.loads(undo.read_text()) == []


@pytest.mark.django_db
def test_local_07_needs_its_own_flag(company, tmp_path):
    lead = stored_lead(company, "07715952998")
    undo = tmp_path / "undo.json"

    run("--apply", "--undo-file", str(undo))
    lead.refresh_from_db()
    assert lead.phone_number == "07715952998"

    run("--apply", "--include-local-07", "--undo-file", str(undo))
    lead.refresh_from_db()
    assert lead.phone_number == E164


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["+99912", "104", "غير محدد"])
def test_invalid_and_non_phone_values_are_left_alone(company, tmp_path, value):
    lead = stored_lead(company, value)

    run("--apply", "--include-local-07", "--undo-file", str(tmp_path / "undo.json"))

    lead.refresh_from_db()
    assert lead.phone_number == value


@pytest.mark.django_db
def test_users_sharing_a_number_are_held_but_others_convert(company, tmp_path):
    first = stored_user(company, "first", DIGITS)
    second = stored_user(company, "second", E164)
    other = stored_user(company, "other", "9647701234567")

    output = run("--apply", "--undo-file", str(tmp_path / "undo.json"))

    first.refresh_from_db()
    second.refresh_from_db()
    other.refresh_from_db()
    assert first.phone == DIGITS
    assert second.phone == E164
    assert other.phone == "+9647701234567"
    assert "held_duplicate_user" in output


@pytest.mark.django_db
def test_a_conflict_skips_that_row_and_the_run_continues(company, tmp_path):
    lead = stored_lead(company, E164)
    kept = ClientPhoneNumber.objects.create(client=lead, phone_number=E164, phone_type="mobile")
    other_lead = stored_lead(company, "9647701234567")
    clash = ClientPhoneNumber.objects.create(client=other_lead, phone_number="+9647701234567", phone_type="mobile")
    ClientPhoneNumber.objects.filter(pk=clash.pk).update(phone_number=DIGITS, phone_normalized="stale")

    output = run("--apply", "--undo-file", str(tmp_path / "undo.json"))

    clash.refresh_from_db()
    other_lead.refresh_from_db()
    assert clash.phone_number == DIGITS
    assert other_lead.phone_number == "+9647701234567"
    assert kept.phone_number == E164
    assert "conflict_skipped" in output
