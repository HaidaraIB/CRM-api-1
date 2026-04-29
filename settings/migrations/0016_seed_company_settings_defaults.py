# Backfill starter settings for existing tenants that never had channels configured.

from django.db import migrations


def forwards(apps, schema_editor):
    from companies.models import Company
    from settings.company_defaults import seed_company_settings

    Channel = apps.get_model("settings", "Channel")
    for company in Company.objects.iterator():
        if not Channel.objects.filter(company_id=company.pk).exists():
            seed_company_settings(company)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0015_invoice_payment_and_billing"),
    ]

    operations = [
        migrations.RunPython(forwards, noop_reverse),
    ]
