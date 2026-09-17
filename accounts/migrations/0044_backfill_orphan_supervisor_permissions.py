from django.db import migrations


def backfill_orphan_supervisor_permissions(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    SupervisorPermission = apps.get_model("accounts", "SupervisorPermission")

    existing_user_ids = set(
        SupervisorPermission.objects.values_list("user_id", flat=True)
    )
    orphans = User.objects.filter(role="supervisor").exclude(id__in=existing_user_ids)
    for user in orphans.iterator(chunk_size=200):
        SupervisorPermission.objects.create(user_id=user.id, is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0043_supervisor_manage_social_inbox"),
    ]

    operations = [
        migrations.RunPython(
            backfill_orphan_supervisor_permissions,
            migrations.RunPython.noop,
        ),
    ]
