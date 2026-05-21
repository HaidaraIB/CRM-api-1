from django.db import migrations, models


def copy_legacy_text_to_bilingual(apps, schema_editor):
    ClientAIInsight = apps.get_model("integrations", "ClientAIInsight")
    for insight in ClientAIInsight.objects.all().iterator():
        updates = {}
        if insight.summary and not insight.summary_en:
            updates["summary_en"] = insight.summary
        if insight.summary and not insight.summary_ar:
            updates["summary_ar"] = insight.summary
        if insight.reasoning and not insight.reasoning_en:
            updates["reasoning_en"] = insight.reasoning
        if insight.reasoning and not insight.reasoning_ar:
            updates["reasoning_ar"] = insight.reasoning
        if insight.suggested_task_notes and not insight.suggested_task_notes_en:
            updates["suggested_task_notes_en"] = insight.suggested_task_notes
        if insight.suggested_task_notes and not insight.suggested_task_notes_ar:
            updates["suggested_task_notes_ar"] = insight.suggested_task_notes
        if updates:
            ClientAIInsight.objects.filter(pk=insight.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0016_rename_client_ai_i_company_8f3c2a_idx_client_ai_i_company_80fbc6_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientaiinsight",
            name="summary_en",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="clientaiinsight",
            name="summary_ar",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="clientaiinsight",
            name="reasoning_en",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="clientaiinsight",
            name="reasoning_ar",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="clientaiinsight",
            name="suggested_task_notes_en",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="clientaiinsight",
            name="suggested_task_notes_ar",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(copy_legacy_text_to_bilingual, migrations.RunPython.noop),
    ]
