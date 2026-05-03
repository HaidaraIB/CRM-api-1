import django.utils.timezone
from django.db import migrations, models


def backfill_status_entered_at(apps, schema_editor):
    Client = apps.get_model("crm", "Client")
    ClientEvent = apps.get_model("crm", "ClientEvent")
    for client in Client.objects.all().iterator(chunk_size=500):
        evt_time = (
            ClientEvent.objects.filter(client_id=client.pk, event_type="status_change")
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        entered = evt_time or client.created_at
        Client.objects.filter(pk=client.pk).update(status_entered_at=entered)


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0035_client_interested_inventory"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="status_entered_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the lead entered the current status (used for stale-status auto-delete).",
                null=True,
            ),
        ),
        migrations.RunPython(backfill_status_entered_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="client",
            name="status_entered_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                help_text="When the lead entered the current status (used for stale-status auto-delete).",
            ),
        ),
        migrations.AddIndex(
            model_name="client",
            index=models.Index(
                fields=["company", "status", "status_entered_at"],
                name="idx_client_co_stat_entered",
            ),
        ),
    ]
