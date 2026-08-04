# 0052 only synced Django state (empty database_operations). Prod never got the columns.

from django.db import migrations, models


def _ensure_clientcall_columns(apps, schema_editor):
    ClientCall = apps.get_model("crm", "ClientCall")
    table = ClientCall._meta.db_table
    connection = schema_editor.connection

    fields_by_name = {
        "dialed_phone_number": models.CharField(
            max_length=32,
            blank=True,
            default="",
            help_text="E.164 / digits dialed or remote party for this call",
        ),
        "recording_duration_sec": models.PositiveIntegerField(blank=True, null=True),
        "recording_status": models.CharField(blank=True, default="", max_length=16),
        "recording_storage_key": models.CharField(blank=True, default="", max_length=512),
    }

    for name, field in fields_by_name.items():
        # Re-read columns each time — SQLite may rebuild the table after ADD COLUMN.
        existing = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor(), table
            )
        }
        if name in existing:
            continue
        field.set_attributes_from_name(name)
        try:
            schema_editor.add_field(ClientCall, field)
        except Exception:
            # Parallel / re-run / vendor quirks: column already present.
            existing_after = {
                column.name
                for column in connection.introspection.get_table_description(
                    connection.cursor(), table
                )
            }
            if name in existing_after:
                continue
            raise


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0052_clientcall_dialed_phone_recording_fields"),
    ]

    operations = [
        migrations.RunPython(_ensure_clientcall_columns, migrations.RunPython.noop),
    ]
