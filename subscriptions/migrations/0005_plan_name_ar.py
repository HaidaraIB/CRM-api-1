from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0004_plan_description_ar"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="name_ar",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]


