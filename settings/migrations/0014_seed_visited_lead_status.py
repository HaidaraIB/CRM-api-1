# Generated manually for post-visit automation lead status

from django.db import migrations, models


VISITED_KEY = "visited"


def seed_visited_lead_statuses(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    LeadStatus = apps.get_model("settings", "LeadStatus")

    for company in Company.objects.filter(
        specialization__in=("real_estate", "services")
    ):
        if LeadStatus.objects.filter(
            company=company, automation_key=VISITED_KEY
        ).exists():
            continue
        loose = (
            LeadStatus.objects.filter(company=company, automation_key__isnull=True)
            .filter(models.Q(name__iexact="Visited"))
            .first()
        )
        if loose:
            loose.automation_key = VISITED_KEY
            loose.save(update_fields=["automation_key"])
            continue
        LeadStatus.objects.create(
            company=company,
            name="Visited",
            category="active",
            color="#6366f1",
            is_default=False,
            is_hidden=False,
            is_active=True,
            automation_key=VISITED_KEY,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0013_visittype_leadstatus_automation_key_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_visited_lead_statuses, noop_reverse),
    ]
