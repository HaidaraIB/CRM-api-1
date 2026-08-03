# 0052 only synced Django state (empty database_operations). Prod never got the columns.

from django.db import migrations, models


def _ensure_clientcall_columns(apps, schema_editor):
    ClientCall = apps.get_model("crm", "ClientCall")
    table = ClientCall._meta.db_table
    connection = schema_editor.connection
    existing = {
        column.name
        for column in connection.introspection.get_table_description(
            connection.cursor(), table
        )
    }

    fields = [
        models.CharField(
            max_length=32,
            blank=True,
            default="",
            help_text="E.164 / digits dialed or remote party for this call",
        ),
        models.PositiveIntegerField(blank=True, null=True),
        models.CharField(blank=True, default="", max_length=16),
        models.CharField(blank=True, default="", max_length=512),
    ]
    names = [
        "dialed_phone_number",
        "recording_duration_sec",
        "recording_status",
        "recording_storage_key",
    ]

    for name, field in zip(names, fields):
        if name in existing:
            continue
        field.set_attributes_from_name(name)
        schema_editor.add_field(ClientCall, field)


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0052_clientcall_dialed_phone_recording_fields"),
    ]

    operations = [
        migrations.RunPython(_ensure_clientcall_columns, migrations.RunPython.noop),
    ]
