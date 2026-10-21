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
                help_text="Legacy object path; unused (files live in the attachment FileField).",
                max_length=512,
            ),
        ),
    ]
