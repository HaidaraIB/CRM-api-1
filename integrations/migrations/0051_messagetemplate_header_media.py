from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0050_social_inbox_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="messagetemplate",
            name="header_media",
            field=models.FileField(
                blank=True,
                help_text="Media file for image/video/document template headers.",
                max_length=500,
                null=True,
                upload_to="whatsapp_templates/headers/%Y/%m/",
            ),
        ),
        migrations.AddField(
            model_name="messagetemplate",
            name="header_media_mime",
            field=models.CharField(
                blank=True,
                default="",
                help_text="MIME type of header_media (image/jpeg, video/mp4, …).",
                max_length=128,
            ),
        ),
    ]
