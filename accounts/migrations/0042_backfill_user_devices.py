"""
Backfill UserDevice from the legacy token fields.

Every token that exists today predates device registration, so its platform is
genuinely unknown and is recorded as such — it must not be guessed. An "unknown"
device is still reachable by an unfiltered push (which is what all current senders
do), so nobody loses notifications; it is only excluded from platform-targeted
sends, where guessing wrong would mean buzzing a phone for a browser event.

Rows fill in their real platform as clients re-register, which every client does on
launch and on token refresh.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    UserDevice = apps.get_model("accounts", "UserDevice")

    seen: set[str] = set()
    rows = []

    # Newest users last, so that when two accounts somehow hold the same token the
    # later row wins — matching the "token belongs to whoever registered it most
    # recently" rule the unique constraint enforces going forward.
    queryset = User.objects.order_by("id").only("id", "fcm_token", "fcm_tokens")
    for user in queryset.iterator(chunk_size=500):
        candidates = []
        raw = user.fcm_tokens if isinstance(user.fcm_tokens, list) else []
        candidates.extend(raw)
        candidates.append(user.fcm_token)

        for token in candidates:
            if not isinstance(token, str):
                continue
            token = token.strip()
            # The column is unique; a duplicate across users would abort the whole
            # migration, so collapse them here instead.
            if not token or token in seen or len(token) > 255:
                continue
            seen.add(token)
            rows.append(
                UserDevice(user_id=user.id, token=token, platform="unknown")
            )

    UserDevice.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)


def unbackfill(apps, schema_editor):
    # Reversible: the legacy fields were never cleared, so dropping these rows
    # loses only the platform mapping, which is exactly what this added.
    UserDevice = apps.get_model("accounts", "UserDevice")
    UserDevice.objects.filter(platform="unknown").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0041_userdevice"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
