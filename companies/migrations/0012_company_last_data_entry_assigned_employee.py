import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0011_admintenantwhatsappmessage"),
        ("accounts", "0022_alter_user_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="last_data_entry_assigned_employee",
            field=models.ForeignKey(
                blank=True,
                help_text="Last employee assigned from data-entry round-robin.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="data_entry_round_robin_companies",
                to="accounts.user",
            ),
        ),
    ]
