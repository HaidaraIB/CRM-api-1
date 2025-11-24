from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0003_broadcast_paymentgateway_plan_clients_plan_storage_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="description_ar",
            field=models.TextField(blank=True, default=""),
        ),
    ]

