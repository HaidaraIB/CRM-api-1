from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("telephony", "0011_pbxcall_emergency_notified_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="pbxextension",
            name="outbound_number",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="outbound_extensions",
                to="telephony.pbxnumber",
            ),
        ),
    ]
