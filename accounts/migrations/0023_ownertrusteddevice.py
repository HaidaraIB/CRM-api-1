from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0022_alter_user_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="OwnerTrustedDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("user_agent_hash", models.CharField(blank=True, default="", max_length=64)),
                ("ip_address", models.CharField(blank=True, default="", max_length=64)),
                ("trusted_until", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="owner_trusted_devices",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "owner_trusted_devices",
                "ordering": ["-updated_at"],
                "indexes": [
                    models.Index(fields=["user", "trusted_until"], name="owner_trust_user_id_9ddd84_idx"),
                    models.Index(fields=["user", "revoked_at"], name="owner_trust_user_id_3bc810_idx"),
                ],
            },
        ),
    ]
