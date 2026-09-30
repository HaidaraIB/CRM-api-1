# Generated manually for OTPIQ registration OTP channel.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0025_leadstatus_requires_change_reason"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformOTPIQSettings",
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
                (
                    "api_key",
                    models.TextField(
                        blank=True,
                        help_text="OTPIQ API key (stored encrypted)",
                        null=True,
                    ),
                ),
                (
                    "sender_id",
                    models.CharField(
                        blank=True,
                        help_text="Optional sender ID (must be accepted in OTPIQ)",
                        max_length=11,
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Platform OTPIQ Settings",
                "verbose_name_plural": "Platform OTPIQ Settings",
                "db_table": "settings_platform_otpiq_settings",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AlterField(
            model_name="systemsettings",
            name="registration_phone_otp_channel",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("whatsapp", "WhatsApp"),
                    ("twilio_sms", "Twilio SMS"),
                    ("otpiq", "OTPIQ"),
                ],
                default="",
                help_text="Delivery channel when registration phone OTP is required.",
                max_length=20,
            ),
        ),
    ]
