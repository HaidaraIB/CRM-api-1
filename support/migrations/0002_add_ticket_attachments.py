# Generated manually for support ticket screenshots

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportTicketAttachment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "file",
                    models.ImageField(max_length=500, upload_to="support_tickets/%Y/%m/%d/"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="support.supportticket",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
