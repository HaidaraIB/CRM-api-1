from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0014_medical_specialization_companypatientcounter"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="field_visit_location_photo_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Allow optional client location photo on field visits (subject to platform policy).",
            ),
        ),
    ]
