from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0051_messagetemplate_header_media"),
    ]

    operations = [
        migrations.AlterField(
            model_name="socialmessage",
            name="external_message_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Meta message id (mid). Meta redelivers aggressively — this dedupes.",
                max_length=255,
            ),
        ),
    ]
