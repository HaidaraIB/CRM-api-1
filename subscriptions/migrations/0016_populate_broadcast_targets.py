# Data migration: copy target -> targets for existing broadcasts

from django.db import migrations


def populate_targets(apps, schema_editor):
    Broadcast = apps.get_model("subscriptions", "Broadcast")
    for b in Broadcast.objects.all():
        if not getattr(b, "targets", None) or len(b.targets) == 0:
            b.targets = [b.target] if b.target else ["all"]
            b.save(update_fields=["targets"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0015_add_broadcast_targets"),
    ]

    operations = [
        migrations.RunPython(populate_targets, noop),
    ]
