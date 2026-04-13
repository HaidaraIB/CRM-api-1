from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0009_platformtwiliosettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="mobile_minimum_version_android",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Minimum Android app version (e.g. 1.2.1). Empty disables enforcement.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="mobile_minimum_version_ios",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Minimum iOS app version (e.g. 1.2.1). Empty disables enforcement.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="mobile_minimum_build_android",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional: when installed version equals minimum, require at least this build (Android versionCode).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="mobile_minimum_build_ios",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional: when installed version equals minimum, require at least this build (iOS CFBundleVersion).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="mobile_store_url_android",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Play Store URL or market:// link for forced update.",
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="mobile_store_url_ios",
            field=models.CharField(
                blank=True,
                default="",
                help_text="App Store URL for forced update.",
                max_length=512,
            ),
        ),
    ]
