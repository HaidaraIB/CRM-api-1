"""Rename untouched English starter rows on existing companies."""

from django.db import migrations


def forwards(apps, schema_editor):
    from companies.models import Company
    from settings.company_defaults import localize_existing_seed_names

    for company in Company.objects.iterator():
        localize_existing_seed_names(company)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0027_deal_pipelines"),
        ("crm", "0064_seed_deal_pipelines"),
    ]

    operations = [
        migrations.RunPython(forwards, noop_reverse),
    ]
