"""
Read-only report on stored phone formats, run before converting rows to +E.164.

Writes nothing. Prints counts and masked samples, never a full number.

    python manage.py audit_phone_numbers
    python manage.py audit_phone_numbers --json
"""

import json
from collections import Counter, defaultdict

import phonenumbers
from django.core.management.base import BaseCommand

from accounts.models import User
from crm.models import Client, ClientPhoneNumber
from demo_bookings.models import DemoBooking
from products.models import Supplier
from real_estate.models import Owner
from services.models import ServiceProvider
from integrations.services.phone_match import canonical_phone_key
from validation.phones import digits_only, is_dialable, to_e164, to_wire_digits

SOURCES = (
    ("users", User, "phone"),
    ("leads", Client, "phone_number"),
    ("lead_phones", ClientPhoneNumber, "phone_number"),
    ("suppliers", Supplier, "phone"),
    ("owners", Owner, "phone"),
    ("service_providers", ServiceProvider, "phone"),
    ("demo_bookings", DemoBooking, "phone"),
)


def mask(value: str) -> str:
    text = str(value)
    return f"{text[:3]}…{text[-2:]}" if len(text) > 5 else "…"


def classify(value: str) -> str:
    """already_e164 / convertible / local_07 / invalid / not_a_phone."""
    if not is_dialable(value):
        return "not_a_phone"
    e164 = to_e164(value)
    try:
        valid = phonenumbers.is_valid_number(phonenumbers.parse(e164))
    except phonenumbers.NumberParseException:
        valid = False
    if not valid:
        return "invalid"
    if value == e164:
        return "already_e164"
    # Assumes Iraq, which is only right for Iraqi tenants: needs a person's eye.
    return "local_07" if digits_only(value).startswith("07") else "convertible"


def lead_number_health() -> dict:
    """What the duplicate protection depends on; every count should end at 0."""
    stale_keys = 0
    holders = defaultdict(set)
    rows_by_lead = defaultdict(set)
    rows = ClientPhoneNumber.objects.values_list("company_id", "client_id", "phone_number", "phone_normalized")
    for company_id, client_id, phone, key in rows.iterator():
        stale_keys += (key or "") != (canonical_phone_key(phone) or "")
        if is_dialable(phone):
            holders[(company_id, to_wire_digits(phone))].add(client_id)
            rows_by_lead[client_id].add(to_wire_digits(phone))

    without_row = 0
    leads = Client.objects.exclude(phone_number__isnull=True).exclude(phone_number="")
    for company_id, client_id, phone in leads.values_list("company_id", "id", "phone_number").iterator():
        if is_dialable(phone):
            holders[(company_id, to_wire_digits(phone))].add(client_id)
            without_row += to_wire_digits(phone) not in rows_by_lead[client_id]

    return {
        "stale_phone_keys": stale_keys,
        "leads_with_number_but_no_phone_row": without_row,
        "numbers_on_more_than_one_lead": sum(len(ids) > 1 for ids in holders.values()),
    }


class Command(BaseCommand):
    help = "Read-only audit of stored phone formats. Writes nothing."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print the report as JSON.")

    def handle(self, *args, **options):
        report = {}
        for label, model, column in SOURCES:
            counts = Counter()
            samples = defaultdict(list)
            rows = (
                model.objects.exclude(**{f"{column}__isnull": True})
                .exclude(**{column: ""})
                .values_list(column, flat=True)
            )
            for value in rows.iterator():
                kind = classify(value)
                counts[kind] += 1
                if len(samples[kind]) < 5:
                    samples[kind].append(mask(value))
            report[label] = {
                "total": sum(counts.values()),
                "counts": dict(counts),
                "samples": {k: v for k, v in samples.items() if k != "already_e164"},
            }

        by_number = defaultdict(list)
        users = User.objects.exclude(phone__isnull=True).exclude(phone="")
        for user_id, phone in users.values_list("id", "phone").iterator():
            if is_dialable(phone):
                by_number[to_wire_digits(phone)].append(user_id)
        report["duplicate_users"] = sorted(ids for ids in by_number.values() if len(ids) > 1)
        report["lead_number_health"] = lead_number_health()

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2))
            return
        for label, _model, _column in SOURCES:
            section = report[label]
            self.stdout.write(f"{label}: {section['total']} {section['counts']}")
            for kind, values in section["samples"].items():
                self.stdout.write(f"    {kind}: {', '.join(values)}")
        duplicates = report["duplicate_users"]
        self.stdout.write(f"users sharing one number in different formats: {len(duplicates)} groups")
        for ids in duplicates:
            self.stdout.write(f"    user ids: {ids}")
        for name, count in report["lead_number_health"].items():
            self.stdout.write(f"{name}: {count}")
