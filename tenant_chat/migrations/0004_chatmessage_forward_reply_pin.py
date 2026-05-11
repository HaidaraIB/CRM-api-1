import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tenant_chat", "0003_chatconversationreadstate"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="reply_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="tenant_chat.chatmessage",
            ),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="forwarded_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="forwards",
                to="tenant_chat.chatmessage",
            ),
        ),
        migrations.CreateModel(
            name="ChatPinnedMessage",
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
                ("pinned_at", models.DateTimeField(auto_now_add=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_pins",
                        to="tenant_chat.chatconversation",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pins",
                        to="tenant_chat.chatmessage",
                    ),
                ),
                (
                    "pinned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "tenant_chat_pinned_messages",
                "ordering": ["-pinned_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="chatpinnedmessage",
            constraint=models.UniqueConstraint(
                fields=("conversation", "message"),
                name="uniq_tenant_chat_pin_per_message",
            ),
        ),
    ]
