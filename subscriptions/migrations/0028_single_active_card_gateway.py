"""
Collapse pre-existing databases onto one active card gateway.

Exclusivity between Stripe/PayTabs (and now Al Qaseh) was only ever enforced by
the admin panel, in two non-atomic requests that any other API client skipped -
so a deployment can already be sitting with two card gateways live. The server
now guarantees the invariant on write; this makes it true of the data that is
already there.

Keeps the most recently updated card gateway on the assumption that it is the
one the operator switched on last, and disables the rest.
"""
from django.db import migrations


def keep_one_active_card_gateway(apps, schema_editor):
    from subscriptions.gateways.registry import adapter_for_name, autodiscover

    autodiscover()
    PaymentGateway = apps.get_model("subscriptions", "PaymentGateway")

    groups = {}
    for row in PaymentGateway.objects.filter(status="active", enabled=True):
        adapter = adapter_for_name(row.name)
        group = getattr(adapter, "exclusive_group", "") if adapter else ""
        if group:
            groups.setdefault(group, []).append(row)

    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: row.updated_at, reverse=True)
        for row in rows[1:]:
            row.enabled = False
            row.status = "disabled"
            row.save(update_fields=["enabled", "status"])


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0027_payment_checkout_session_fields"),
    ]

    operations = [
        # Irreversible only in the sense that there is nothing to undo: the
        # previous state was the invariant violation.
        migrations.RunPython(keep_one_active_card_gateway, migrations.RunPython.noop),
    ]
