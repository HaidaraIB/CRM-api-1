from django.db import migrations


def migrate_platform_login_2fa_to_owners(apps, schema_editor):
    SystemSettings = apps.get_model("settings", "SystemSettings")
    User = apps.get_model("accounts", "User")
    Company = apps.get_model("companies", "Company")

    settings = SystemSettings.objects.filter(pk=1).first()
    if not settings or getattr(settings, "login_two_factor_required", True):
        return

    owner_ids = list(
        Company.objects.exclude(owner_id=None).values_list("owner_id", flat=True)
    )
    if owner_ids:
        User.objects.filter(id__in=owner_ids).update(login_two_factor_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0029_user_login_two_factor_enabled"),
        ("settings", "0021_systemsettings_platform_auth_policies"),
    ]

    operations = [
        migrations.RunPython(
            migrate_platform_login_2fa_to_owners,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="systemsettings",
            name="login_two_factor_required",
        ),
    ]
