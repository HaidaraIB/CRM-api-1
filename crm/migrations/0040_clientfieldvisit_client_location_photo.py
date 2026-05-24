# Generated manually for client location photo on field visits

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0039_client_location_and_field_visit"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientfieldvisit",
            name="client_location_photo",
            field=models.ImageField(
                blank=True,
                help_text="Optional photo of the client location at visit time.",
                null=True,
                upload_to="field_visit_location_photos/",
            ),
        ),
    ]
