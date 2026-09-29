from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support_chat", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supportconversation",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending approval"),
                    ("open", "Open"),
                    ("resolved", "Resolved"),
                ],
                db_index=True,
                default="resolved",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="supportmessage",
            name="side",
            field=models.CharField(
                choices=[
                    ("tenant", "Tenant"),
                    ("support", "Support"),
                    ("system", "System"),
                ],
                db_index=True,
                max_length=16,
            ),
        ),
    ]
