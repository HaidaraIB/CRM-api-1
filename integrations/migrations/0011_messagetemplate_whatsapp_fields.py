# Migration for WhatsApp template fields (language, header, footer, buttons)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0010_add_message_template_meta_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='messagetemplate',
            name='language',
            field=models.CharField(blank=True, default='en_US', help_text='لغة القالب لواتساب (مثل en_US, ar)', max_length=20),
        ),
        migrations.AddField(
            model_name='messagetemplate',
            name='header_type',
            field=models.CharField(blank=True, default='none', help_text='نوع الرأس: none, text, image, video, document', max_length=20),
        ),
        migrations.AddField(
            model_name='messagetemplate',
            name='header_text',
            field=models.TextField(blank=True, default='', help_text='نص الرأس عند اختيار header_type=text'),
        ),
        migrations.AddField(
            model_name='messagetemplate',
            name='footer',
            field=models.TextField(blank=True, default='', help_text='نص التذييل'),
        ),
        migrations.AddField(
            model_name='messagetemplate',
            name='buttons',
            field=models.JSONField(blank=True, default=list, help_text="قائمة أزرار: [{type: phone|url|reply, button_text, phone?, url?}]"),
        ),
    ]
