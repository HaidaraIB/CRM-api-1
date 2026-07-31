# Company-wide uniqueness on normalized lead phone numbers.

from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


def _digits_only(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def _is_dialable(phone: str) -> bool:
    cleaned = (phone or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in ("<unknown>", "unknown", "anonymous", "s", "h", "i"):
        return False
    return len(_digits_only(cleaned)) >= 7


def _canonical_phone_key(phone: str) -> str:
    """Mirror integrations.services.phone_match.canonical_phone_key (migration-safe)."""
    if not _is_dialable(phone):
        return ""
    to = (phone or "").strip().replace(" ", "").replace("-", "")
    if to.startswith("07") and len(to) >= 10:
        to = "+964" + to[1:]
    elif not to.startswith("+"):
        to = "+" + to
    return _digits_only(to)


def backfill_phone_normalized(apps, schema_editor):
    Client = apps.get_model("crm", "Client")
    ClientPhoneNumber = apps.get_model("crm", "ClientPhoneNumber")

    # Ensure every dialable Client.phone_number has a ClientPhoneNumber row.
    for client in Client.objects.exclude(phone_number__isnull=True).exclude(phone_number=""):
        if not _is_dialable(client.phone_number):
            continue
        if ClientPhoneNumber.objects.filter(client_id=client.id).exists():
            continue
        ClientPhoneNumber.objects.create(
            client_id=client.id,
            company_id=client.company_id,
            phone_number=client.phone_number,
            phone_normalized=_canonical_phone_key(client.phone_number),
            phone_type="mobile",
            is_primary=True,
        )

    # Backfill company + normalized on existing rows (bulk; bypasses model.save).
    for row in ClientPhoneNumber.objects.select_related("client").iterator(chunk_size=500):
        company_id = row.client.company_id if row.client_id else None
        normalized = _canonical_phone_key(row.phone_number)
        updates = []
        if row.company_id != company_id:
            row.company_id = company_id
            updates.append("company_id")
        if row.phone_normalized != normalized:
            row.phone_normalized = normalized
            updates.append("phone_normalized")
        if updates:
            row.save(update_fields=updates)

    # Demote extras so UniqueConstraint can be applied: keep lowest id per
    # (company_id, phone_normalized); clear phone_normalized on the rest.
    groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    for row in ClientPhoneNumber.objects.exclude(phone_normalized="").exclude(
        company_id__isnull=True
    ).values_list("id", "company_id", "phone_normalized"):
        groups[(row[1], row[2])].append(row[0])

    demoted = 0
    for (_company_id, _key), ids in groups.items():
        if len(ids) < 2:
            continue
        ids_sorted = sorted(ids)
        for extra_id in ids_sorted[1:]:
            ClientPhoneNumber.objects.filter(pk=extra_id).update(phone_normalized="")
            demoted += 1
    if demoted:
        print(
            f"crm.0049: cleared phone_normalized on {demoted} legacy duplicate "
            f"ClientPhoneNumber row(s) so uniq_company_phone_normalized can apply."
        )

    orphaned = ClientPhoneNumber.objects.filter(company_id__isnull=True).count()
    if orphaned:
        ClientPhoneNumber.objects.filter(company_id__isnull=True).delete()
        print(f"crm.0049: deleted {orphaned} ClientPhoneNumber row(s) with null company.")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    # PostgreSQL rejects ALTER TABLE in the same transaction as prior DML on that
    # table ("pending trigger events"). Commit each operation separately.
    atomic = False

    dependencies = [
        ("companies", "0001_initial"),
        ("crm", "0048_client_mujeb_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientphonenumber",
            name="company",
            field=models.ForeignKey(
                help_text="Denormalized from client.company for company-wide phone uniqueness.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="client_phone_numbers",
                to="companies.company",
            ),
        ),
        migrations.AddField(
            model_name="clientphonenumber",
            name="phone_normalized",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Digits-only E.164 key for company-wide uniqueness (empty = not enforced).",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_phone_normalized, noop_reverse),
        migrations.AlterField(
            model_name="clientphonenumber",
            name="company",
            field=models.ForeignKey(
                help_text="Denormalized from client.company for company-wide phone uniqueness.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="client_phone_numbers",
                to="companies.company",
            ),
        ),
        migrations.AddConstraint(
            model_name="clientphonenumber",
            constraint=models.UniqueConstraint(
                condition=models.Q(("phone_normalized__gt", "")),
                fields=("company", "phone_normalized"),
                name="uniq_company_phone_normalized",
            ),
        ),
    ]
