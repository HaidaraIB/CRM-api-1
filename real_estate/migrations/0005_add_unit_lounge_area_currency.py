# Generated manually for Unit lounge, area, currency fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('real_estate', '0004_alter_developer_code_alter_owner_code_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='unit',
            name='lounge',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='unit',
            name='area',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='unit',
            name='currency',
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
    ]
