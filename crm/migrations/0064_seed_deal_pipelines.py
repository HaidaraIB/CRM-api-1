"""Seed a default pipeline per company and map legacy Deal.stage values onto it."""

from django.db import migrations


def seed_pipelines(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    from companies.models import Company as LiveCompany
    from settings.deal_pipeline_defaults import map_company_legacy_deals

    for row in Company.objects.all().iterator():
        company = LiveCompany.objects.filter(pk=row.pk).first()
        if company is not None:
            map_company_legacy_deals(company)


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0063_deal_pipelines"),
        ("settings", "0027_deal_pipelines"),
    ]

    operations = [
        migrations.RunPython(seed_pipelines, migrations.RunPython.noop),
    ]
