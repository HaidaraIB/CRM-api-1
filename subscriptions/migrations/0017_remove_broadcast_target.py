# Remove legacy single target column; use targets only

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0016_populate_broadcast_targets"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="broadcast",
            name="target",
        ),
    ]
