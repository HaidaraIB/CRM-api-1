from django.db import migrations, models


def populate_fcm_tokens(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.all().only("id", "fcm_token", "fcm_tokens"):
        fcm_tokens = user.fcm_tokens if isinstance(user.fcm_tokens, list) else []
        seen = set()
        normalized = []
        for token in fcm_tokens:
            if not isinstance(token, str):
                continue
            t = token.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            normalized.append(t)
        legacy = (user.fcm_token or "").strip()
        if legacy and legacy not in seen:
            normalized.append(legacy)
        if normalized != fcm_tokens:
            user.fcm_tokens = normalized
            user.save(update_fields=["fcm_tokens"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_user_presence_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="fcm_tokens",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of Firebase Cloud Messaging tokens for multi-device push notifications",
            ),
        ),
        migrations.RunPython(populate_fcm_tokens, migrations.RunPython.noop),
    ]
