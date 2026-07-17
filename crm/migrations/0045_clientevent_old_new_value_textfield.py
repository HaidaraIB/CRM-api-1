from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0044_widen_phone_and_meta_leadgen_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="clientevent",
            name="old_value",
            field=models.TextField(
                blank=True,
                help_text="Old value before the change",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="clientevent",
            name="new_value",
            field=models.TextField(
                blank=True,
                help_text="New value after the change",
                null=True,
            ),
        ),
    ]
