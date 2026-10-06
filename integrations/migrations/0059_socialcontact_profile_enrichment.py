# Generated manually for social contact profile enrichment fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0058_quick_replies'),
    ]

    operations = [
        migrations.AddField(
            model_name='socialcontact',
            name='avatar_path',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Storage key for the mirrored avatar file (e.g. social_profiles/…). Empty when none.',
                max_length=512,
            ),
        ),
        migrations.AddField(
            model_name='socialcontact',
            name='name_manually_set',
            field=models.BooleanField(
                default=False,
                help_text='When True, automated enrichment must not overwrite name.',
            ),
        ),
        migrations.AddField(
            model_name='socialcontact',
            name='profile_fetch_status',
            field=models.CharField(
                blank=True,
                choices=[('ok', 'ok'), ('failed', 'failed'), ('unavailable', 'unavailable')],
                default='',
                help_text='Last Graph/profile enrichment outcome.',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='socialcontact',
            name='name',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Display name from Meta / WhatsApp / agent override.',
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name='socialcontact',
            name='profile_pic_url',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Stable public URL for the avatar (our storage), not a Meta CDN link.',
            ),
        ),
    ]
