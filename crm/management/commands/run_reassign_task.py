"""
Management command to run the re-assign task manually
This can be called from cron job instead of using django-q2
Usage: python manage.py run_reassign_task
"""
from django.core.management.base import BaseCommand
from crm.tasks import re_assign_inactive_clients


class Command(BaseCommand):
    help = 'Run the re-assign inactive clients task (for use with cron)'

    def handle(self, *args, **options):
        try:
            result = re_assign_inactive_clients()
            self.stdout.write(
                self.style.SUCCESS(f'Task completed: {result}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error running task: {str(e)}')
            )
            raise

