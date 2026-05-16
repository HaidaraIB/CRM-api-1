# Generated manually

from django.db import migrations, models


def backfill_patient_file_numbers(apps, schema_editor):
    Client = apps.get_model("crm", "Client")
    Company = apps.get_model("companies", "Company")
    CompanyPatientCounter = apps.get_model("companies", "CompanyPatientCounter")

    for company in Company.objects.all().iterator():
        clients = list(Client.objects.filter(company=company).order_by("id"))
        for i, client in enumerate(clients, start=1):
            client.patient_file_number = i
        if clients:
            Client.objects.bulk_update(clients, ["patient_file_number"])
        next_n = len(clients) + 1
        CompanyPatientCounter.objects.update_or_create(
            company=company,
            defaults={"next_number": next_n},
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0014_medical_specialization_companypatientcounter"),
        ("crm", "0036_client_status_entered_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="residence",
            field=models.CharField(
                blank=True,
                help_text="Address / residence (e.g. clinic patient).",
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="patient_file_number",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Per-company sequential clinic file number.",
                null=True,
            ),
        ),
        migrations.RunPython(backfill_patient_file_numbers, noop_reverse),
        migrations.AlterField(
            model_name="client",
            name="patient_file_number",
            field=models.PositiveIntegerField(
                help_text="Per-company sequential clinic file number.",
            ),
        ),
        migrations.AddConstraint(
            model_name="client",
            constraint=models.UniqueConstraint(
                fields=("company", "patient_file_number"),
                name="uniq_client_company_patient_file_number",
            ),
        ),
    ]
