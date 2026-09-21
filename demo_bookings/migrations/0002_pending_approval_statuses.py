# Generated manually for demo booking approval workflow

from django.db import migrations, models


def set_demo_booking_id_sequence(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM demo_bookings")
        max_id = cursor.fetchone()[0]
        next_start = max(max_id, 999)

        if connection.vendor == "postgresql":
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('demo_bookings', 'id'), %s, %s)",
                [next_start, max_id >= 1000],
            )
        elif connection.vendor == "sqlite":
            cursor.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'demo_bookings'"
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    "UPDATE sqlite_sequence SET seq = %s WHERE name = 'demo_bookings'",
                    [next_start],
                )
            else:
                cursor.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('demo_bookings', %s)",
                    [next_start],
                )


class Migration(migrations.Migration):

    dependencies = [
        ("demo_bookings", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="demobooking",
            name="unique_confirmed_demo_booking_starts_at",
        ),
        migrations.AlterField(
            model_name="demobooking",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("confirmed", "Confirmed"),
                    ("not_confirmed", "Not confirmed"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                    ("no_show", "No show"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="demobooking",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="pending") | models.Q(status="confirmed"),
                fields=("starts_at",),
                name="unique_active_demo_booking_starts_at",
            ),
        ),
        migrations.RunPython(set_demo_booking_id_sequence, migrations.RunPython.noop),
    ]
