from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0020_plan_entitlements_and_usage_counters"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="plan",
            name="storage",
        ),
    ]

