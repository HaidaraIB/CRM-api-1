"""
Move the two per-minute jobs from cron into the warm django-q cluster.

``send_scheduled_broadcasts`` and ``check_lead_arrival_escalations`` run every
minute. Under cron each run is a full cold Django boot — roughly 200MB of RSS and
seconds of CPU to import the app, connect to Postgres and Redis, and initialise
Firebase — before it does any work. That is ~2,880 process starts a day, on two
shared cores that Gunicorn, Postgres, Redis and a second Django app also use, and
the overwhelming majority of those runs find nothing to do.

The qcluster is already running and already warm. Running the same two commands as
django-q Schedules replaces the boot with a function call.

Idempotent — safe to run repeatedly, and on every deploy.

    python manage.py install_qcluster_schedules
    python manage.py install_qcluster_schedules --remove   # roll back to cron

**After installing, comment out the two matching lines in crontab_complete.txt and
`crontab -e`.** Nothing here can do that for you, and leaving both in place runs
each job twice a minute. The jobs are individually safe to double-run — broadcasts
are claimed row-by-row and escalations go through claim_dispatch — but it doubles
the work for no benefit.
"""

from django.core.management.base import BaseCommand

# name -> management command. The name is the idempotency key.
SCHEDULES = {
    "send_scheduled_broadcasts": "send_scheduled_broadcasts",
    "check_lead_arrival_escalations": "check_lead_arrival_escalations",
}

# django-q calls this with the command name; call_command does the rest. A module
# level function because django-q re-imports the reference by dotted path in the
# worker, so it must be resolvable by name there.
TASK_PATH = "notifications.management.commands.install_qcluster_schedules.run_management_command"


def run_management_command(command_name: str) -> str:
    from django.core.management import call_command

    call_command(command_name)
    return command_name


class Command(BaseCommand):
    help = "Install (or remove) django-q Schedules for the per-minute cron jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the schedules instead, to fall back to cron.",
        )

    def handle(self, *args, **options):
        from django_q.models import Schedule

        if options["remove"]:
            deleted, _ = Schedule.objects.filter(name__in=SCHEDULES).delete()
            self.stdout.write(
                self.style.WARNING(
                    f"Removed {deleted} schedule(s). Re-enable the cron lines before "
                    "relying on these jobs again."
                )
            )
            return

        for name, command_name in SCHEDULES.items():
            Schedule.objects.update_or_create(
                name=name,
                defaults={
                    "func": TASK_PATH,
                    "args": repr((command_name,)),
                    "schedule_type": Schedule.MINUTES,
                    "minutes": 1,
                    # Never run a backlog. If the cluster was down, the jobs are
                    # time-sensitive and the missed windows are gone — replaying
                    # them would send a burst of stale broadcasts and escalate
                    # arrivals that have long since been handled.
                    "repeats": -1,
                },
            )
            self.stdout.write(self.style.SUCCESS(f"Scheduled {name} every minute"))

        self.stdout.write(
            self.style.WARNING(
                "\nNow comment out these two lines in crontab (`crontab -e`), or both "
                "will run:\n"
                "  * * * * * ... manage.py send_scheduled_broadcasts\n"
                "  * * * * * ... manage.py check_lead_arrival_escalations"
            )
        )
