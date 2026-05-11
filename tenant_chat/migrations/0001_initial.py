# Generated manually for tenant_chat

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("companies", "0013_company_timezone"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatConversation",
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
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_conversations",
                        to="companies.company",
                    ),
                ),
                (
                    "participant_high",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "participant_low",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "tenant_chat_conversations",
            },
        ),
        migrations.CreateModel(
            name="ChatMessage",
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
                ("body", models.TextField(max_length=8000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="tenant_chat.chatconversation",
                    ),
                ),
                (
                    "sender",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sent_chat_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "tenant_chat_messages",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="chatconversation",
            constraint=models.UniqueConstraint(
                fields=("company", "participant_low", "participant_high"),
                name="uniq_tenant_chat_pair_per_company",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatconversation",
            constraint=models.CheckConstraint(
                condition=Q(participant_low_id__lt=F("participant_high_id")),
                name="tenant_chat_low_lt_high",
            ),
        ),
        migrations.AddIndex(
            model_name="chatconversation",
            index=models.Index(
                fields=["company", "updated_at"], name="tenant_chat_co_company_b85287_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="chatmessage",
            index=models.Index(
                fields=["conversation", "created_at"],
                name="tenant_chat_me_convers_e87ab5_idx",
            ),
        ),
    ]
