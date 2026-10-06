from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0059_client_source_social'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='avatar_path',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Optional avatar storage path/URL copied from a social contact on convert.',
                max_length=512,
            ),
        ),
    ]
