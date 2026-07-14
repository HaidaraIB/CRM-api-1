from django.db import migrations, models


def migrate_cache_auth_policies_to_db(apps, schema_editor):
    SystemSettings = apps.get_model("settings", "SystemSettings")
    settings, _ = SystemSettings.objects.get_or_create(pk=1)

    try:
        from django.core.cache import cache
    except Exception:
        return

    update_fields = []

    phone_required = cache.get("platform_whatsapp_otp_required_override", None)
    if phone_required is not None:
        settings.registration_phone_otp_required = bool(phone_required)
        update_fields.append("registration_phone_otp_required")

    phone_channel = cache.get("registration_phone_otp_channel", None)
    if phone_channel in ("whatsapp", "twilio_sms"):
        settings.registration_phone_otp_channel = phone_channel
        update_fields.append("registration_phone_otp_channel")

    email_required = cache.get("registration_email_verification_required_override", None)
    if email_required is not None:
        settings.registration_email_verification_required = bool(email_required)
        update_fields.append("registration_email_verification_required")

    login_two_factor = cache.get("login_two_factor_required_override", None)
    if login_two_factor is not None:
        settings.login_two_factor_required = bool(login_two_factor)
        update_fields.append("login_two_factor_required")

    if update_fields:
        settings.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0020_systemsettings_maintenance_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="login_two_factor_required",
            field=models.BooleanField(
                default=True,
                help_text="Require email 2FA for company owners after login (web and mobile).",
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="registration_email_verification_required",
            field=models.BooleanField(
                default=False,
                help_text="Require email verification before company registration.",
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="registration_phone_otp_channel",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("whatsapp", "WhatsApp"),
                    ("twilio_sms", "Twilio SMS"),
                ],
                default="",
                help_text="Delivery channel when registration phone OTP is required.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="registration_phone_otp_required",
            field=models.BooleanField(
                default=False,
                help_text="Require phone OTP verification before company registration.",
            ),
        ),
        migrations.RunPython(
            migrate_cache_auth_policies_to_db,
            migrations.RunPython.noop,
        ),
    ]
