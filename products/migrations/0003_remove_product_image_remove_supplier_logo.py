# Generated manually

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0002_product_cost_product_description_product_image_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='product',
            name='image',
        ),
        migrations.RemoveField(
            model_name='supplier',
            name='logo',
        ),
    ]

