from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0017_alter_company_field_visit_enabled"),
        ("integrations", "0028_remove_pbxsettings_connector_base_url"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="pbxsettings",
            name="softphone_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="pbxsettings",
            name="sip_domain",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="pbxsettings",
            name="sip_port",
            field=models.PositiveIntegerField(default=5162),
        ),
        migrations.AddField(
            model_name="pbxsettings",
            name="sip_transport",
            field=models.CharField(
                choices=[("tls", "TLS"), ("wss", "WSS")],
                default="tls",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="pbxsettings",
            name="wss_uri",
            field=models.CharField(
                blank=True,
                default="",
                help_text="WebRTC WSS URI, e.g. wss://domain:8089/ws",
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name="pbxsettings",
            name="stun_server",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="pbxsettings",
            name="turn_server",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="userpbxextension",
            name="sip_password",
            field=models.TextField(
                blank=True,
                help_text="Encrypted SIP password for softphone registration",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="userpbxextension",
            name="softphone_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name="UserSoftphoneDevice",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "platform",
                    models.CharField(
                        choices=[
                            ("ios", "iOS"),
                            ("android", "Android"),
                            ("web", "Web"),
                        ],
                        max_length=16,
                    ),
                ),
                ("device_id", models.CharField(blank=True, default="", max_length=128)),
                ("fcm_token", models.CharField(blank=True, default="", max_length=512)),
                ("voip_token", models.CharField(blank=True, default="", max_length=512)),
                ("last_registered_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="softphone_devices",
                        to="companies.company",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="softphone_devices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "integrations_user_softphone_device",
            },
        ),
        migrations.AddIndex(
            model_name="usersoftphonedevice",
            index=models.Index(
                fields=["company", "user"],
                name="integration_company_4f8a21_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="usersoftphonedevice",
            constraint=models.UniqueConstraint(
                fields=("user", "platform", "device_id"),
                name="uniq_softphone_device_user_platform_device",
            ),
        ),
    ]
