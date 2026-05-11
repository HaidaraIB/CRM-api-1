from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenant_chat", "0005_chatmessage_attachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="attachment_object_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Supabase Storage object path when TENANT_CHAT_STORAGE=supabase (empty for local FileField).",
                max_length=512,
            ),
        ),
    ]
