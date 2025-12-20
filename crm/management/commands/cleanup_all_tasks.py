"""
Management command to cleanup all old tasks, schedules, and failures
Use this when you need to completely reset django-q2
"""
from django.core.management.base import BaseCommand
from django_q.models import Schedule, OrmQ, Failure


class Command(BaseCommand):
    help = 'Cleanup all tasks, schedules, and failures from django-q2'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force cleanup without confirmation',
        )

    def handle(self, *args, **options):
        if not options['force']:
            self.stdout.write(
                self.style.WARNING(
                    'This will delete ALL tasks, schedules, and failures. '
                    'Use --force to proceed without confirmation.'
                )
            )
            return

        # Delete all schedules
        schedule_count = Schedule.objects.count()
        Schedule.objects.all().delete()
        self.stdout.write(
            self.style.SUCCESS(f'Deleted {schedule_count} schedule(s)')
        )

        # Delete all tasks in queue
        task_count = OrmQ.objects.count()
        OrmQ.objects.all().delete()
        self.stdout.write(
            self.style.SUCCESS(f'Deleted {task_count} task(s) from queue')
        )

        # Delete all failures
        failure_count = Failure.objects.count()
        Failure.objects.all().delete()
        self.stdout.write(
            self.style.SUCCESS(f'Deleted {failure_count} failure(s)')
        )

        self.stdout.write(
            self.style.SUCCESS('\nAll tasks, schedules, and failures have been cleaned up.')
        )
        self.stdout.write(
            self.style.WARNING(
                '\nNow run: python manage.py setup_reassign_schedule'
            )
        )

