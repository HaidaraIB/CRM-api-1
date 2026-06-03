from django.db import migrations, models


def backfill_linkedid(apps, schema_editor):
    PbxCallRecord = apps.get_model("integrations", "PbxCallRecord")
    for rec in PbxCallRecord.objects.filter(linkedid="").iterator(chunk_size=500):
        payload = rec.raw_payload or {}
        lid = (
            payload.get("Linkedid")
            or payload.get("linkedid")
            or payload.get("LinkedID")
            or rec.uniqueid
        )
        if lid:
            PbxCallRecord.objects.filter(pk=rec.pk).update(
                linkedid=str(lid)[:128],
            )


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0024_remove_integrationaccount_account_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="pbxcallrecord",
            name="linkedid",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddIndex(
            model_name="pbxcallrecord",
            index=models.Index(
                fields=["company", "linkedid"],
                name="integrations_company_linkedid_idx",
            ),
        ),
        migrations.RunPython(backfill_linkedid, migrations.RunPython.noop),
    ]
