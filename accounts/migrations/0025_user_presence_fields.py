from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0024_rename_owner_trust_user_id_9ddd84_idx_owner_trust_user_id_ed195d_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="last_seen_source",
            field=models.CharField(
                choices=[("web", "Web"), ("mobile", "Mobile"), ("unknown", "Unknown")],
                default="unknown",
                max_length=16,
            ),
        ),
    ]
