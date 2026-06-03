from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0027_pbx_recording_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pbxsettings",
            name="connector_base_url",
        ),
    ]
