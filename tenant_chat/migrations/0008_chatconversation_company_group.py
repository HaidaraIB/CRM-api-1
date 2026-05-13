# Generated manually: company-wide group thread + DM partial constraints

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


def backfill_company_groups(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    ChatConversation = apps.get_model("tenant_chat", "ChatConversation")
    for c in Company.objects.all().iterator():
        ChatConversation.objects.get_or_create(
            company_id=c.pk,
            kind="company_group",
            defaults={
                "participant_low_id": None,
                "participant_high_id": None,
            },
        )


def reverse_company_groups(apps, schema_editor):
    ChatConversation = apps.get_model("tenant_chat", "ChatConversation")
    ChatConversation.objects.filter(kind="company_group").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tenant_chat", "0007_chatmessage_attachment_dimensions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="chatconversation",
            name="uniq_tenant_chat_pair_per_company",
        ),
        migrations.RemoveConstraint(
            model_name="chatconversation",
            name="tenant_chat_low_lt_high",
        ),
        migrations.AddField(
            model_name="chatconversation",
            name="kind",
            field=models.CharField(
                choices=[("direct", "Direct"), ("company_group", "Company group")],
                db_index=True,
                default="direct",
                max_length=20,
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name="chatconversation",
            name="participant_high",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="chatconversation",
            name="participant_low",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="chatconversation",
            constraint=models.UniqueConstraint(
                condition=Q(kind="direct"),
                fields=("company", "participant_low", "participant_high"),
                name="uniq_tenant_chat_dm_triplet",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatconversation",
            constraint=models.UniqueConstraint(
                condition=Q(kind="company_group"),
                fields=("company",),
                name="uniq_tenant_chat_company_group_per_company",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatconversation",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        kind="direct",
                        participant_low_id__isnull=False,
                        participant_high_id__isnull=False,
                        participant_low_id__lt=F("participant_high_id"),
                    )
                    | Q(
                        kind="company_group",
                        participant_low_id__isnull=True,
                        participant_high_id__isnull=True,
                    )
                ),
                name="tenant_chat_kind_participant_rules",
            ),
        ),
        migrations.RunPython(backfill_company_groups, reverse_company_groups),
    ]
