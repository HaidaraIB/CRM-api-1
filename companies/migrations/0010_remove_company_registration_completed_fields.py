# Generated manually for removing duplicate registration state (use Subscription as source of truth)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0009_company_free_trial_consumed"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="company",
            name="registration_completed",
        ),
        migrations.RemoveField(
            model_name="company",
            name="registration_completed_at",
        ),
    ]
