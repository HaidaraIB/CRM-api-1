from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0015_company_field_visit_location_photo_enabled"),
    ]

    operations = [
        migrations.RenameField(
            model_name="company",
            old_name="field_visit_location_photo_enabled",
            new_name="field_visit_enabled",
        ),
    ]
