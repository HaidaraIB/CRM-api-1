from django.db import migrations
from django.db.models import Q


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    db_alias = schema_editor.connection.alias
    User.objects.using(db_alias).filter(~Q(phone__isnull=True) & ~Q(phone="")).update(phone_verified=True)


def backwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    db_alias = schema_editor.connection.alias
    User.objects.using(db_alias).all().update(phone_verified=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_user_phone_verified_phoneregistrationchallenge"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
