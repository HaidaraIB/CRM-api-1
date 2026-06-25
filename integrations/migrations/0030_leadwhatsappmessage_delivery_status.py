from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0029_softphone_integration"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadwhatsappmessage",
            name="delivery_status",
            field=models.CharField(
                blank=True,
                help_text="Meta delivery status: sent, delivered, read, failed",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="leadwhatsappmessage",
            name="delivery_error",
            field=models.CharField(
                blank=True,
                help_text="Meta error message when delivery_status=failed",
                max_length=512,
                null=True,
            ),
        ),
    ]
