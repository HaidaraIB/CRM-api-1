from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0019_alter_company_auto_assign_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="auto_assign_algorithm",
            field=models.CharField(
                choices=[
                    ("least_busy", "Least busy (workload-based)"),
                    ("round_robin", "Round robin (take turns)"),
                ],
                default="least_busy",
                help_text="Algorithm used when auto-assigning new leads.",
                max_length=20,
            ),
        ),
    ]
