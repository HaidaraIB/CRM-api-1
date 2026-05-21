from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0037_client_residence_patient_file_number"),
    ]

    operations = [
        migrations.AlterField(
            model_name="client",
            name="type",
            field=models.CharField(
                choices=[
                    ("fresh", "FRESH"),
                    ("hot", "HOT"),
                    ("cold", "COLD"),
                ],
                max_length=20,
            ),
        ),
    ]
