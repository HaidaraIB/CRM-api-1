# Generated manually for idempotent payment → period apply

from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Coalesce


def backfill_applied_at_for_completed(apps, schema_editor):
    Payment = apps.get_model("subscriptions", "Payment")
    Payment.objects.filter(
        payment_status="completed",
        applied_at__isnull=True,
    ).update(applied_at=Coalesce(F("updated_at"), F("created_at")))


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0025_perf_indexes_subscription_and_client_activity"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="applied_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When this payment's effect was applied to the subscription period.",
                null=True,
            ),
        ),
        migrations.RunPython(backfill_applied_at_for_completed, noop_reverse),
    ]
