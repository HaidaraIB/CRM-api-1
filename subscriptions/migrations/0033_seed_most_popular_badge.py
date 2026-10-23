"""
Preserve today's pricing-page look: CRM-project used to mark the highest-tier
visible paid plan as "Most popular" with a client-side heuristic. That badge is
now admin-controlled, so seed it once on the same plan.
"""

from django.db import migrations


def seed_badge(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "Plan")
    if Plan.objects.exclude(badge="").exists():
        return
    visible = list(Plan.objects.filter(visible=True).order_by("price_monthly", "id"))
    if len(visible) < 2:
        return
    paid = [p for p in visible if float(p.price_monthly or 0) > 0 or float(p.price_yearly or 0) > 0]
    if not paid:
        return
    # Same pick as the old client heuristic: highest tier, cheapest on ties.
    top = paid[0]
    for p in paid[1:]:
        if p.tier > top.tier:
            top = p
    top.badge = "most_popular"
    top.save(update_fields=["badge"])


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0032_plan_offers_promotions"),
    ]

    operations = [
        migrations.RunPython(seed_badge, migrations.RunPython.noop),
    ]
