"""Restore English seed keys so the UI can translate them either way."""

from django.db import migrations


def forwards(apps, schema_editor):
    from companies.models import Company
    from settings.company_defaults import restore_canonical_seed_names

    for company in Company.objects.iterator():
        restore_canonical_seed_names(company)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0028_localize_existing_seed_names"),
    ]

    operations = [
        migrations.RunPython(forwards, noop_reverse),
    ]
