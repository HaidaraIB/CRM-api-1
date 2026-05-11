import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tenant_chat", "0002_rename_tenant_chat_co_company_b85287_idx_tenant_chat_company_0aae53_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatConversationReadState",
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
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="read_states",
                        to="tenant_chat.chatconversation",
                    ),
                ),
                (
                    "last_read_message",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="tenant_chat.chatmessage",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "tenant_chat_read_states",
            },
        ),
        migrations.AddConstraint(
            model_name="chatconversationreadstate",
            constraint=models.UniqueConstraint(
                fields=("conversation", "user"),
                name="uniq_tenant_chat_read_per_user",
            ),
        ),
    ]
