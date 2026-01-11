"""
Management command to check for task reminders and send notifications
Usage:
    python manage.py check_task_reminders
    python manage.py check_task_reminders --minutes-before 30
    python manage.py check_task_reminders --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import Task
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for task reminders and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--minutes-before',
            type=int,
            default=30,
            help='Number of minutes before reminder time to send notification (default: 30)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        minutes_before = options.get('minutes_before', 30)
        dry_run = options.get('dry_run', False)

        now = timezone.now()
        reminder_start = now + timedelta(minutes=minutes_before)
        reminder_end = now + timedelta(minutes=minutes_before + 5)  # 5 minute window

        # Find Tasks with reminders in the next window
        tasks = Task.objects.filter(
            reminder_date__gte=reminder_start,
            reminder_date__lte=reminder_end,
            deal__employee__isnull=False,
        ).select_related('deal', 'deal__employee')

        if not tasks.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No task reminders found in the next {minutes_before} minutes.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for task in tasks:
            employee = task.deal.employee
            if not employee or not employee.fcm_token:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send reminder to {employee.username} '
                        f'for task {task.id} - Reminder at {task.reminder_date}'
                    )
                )
            else:
                try:
                    NotificationService.send_notification(
                        user=employee,
                        notification_type=NotificationType.TASK_REMINDER,
                        data={
                            'task_id': task.id,
                            'task_title': task.notes or f'Task for {task.deal.client.name}',
                            'minutes_remaining': int((task.reminder_date - now).total_seconds() / 60),
                        },
                        skip_settings_check=False,  # Respect user settings
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent reminder to {employee.username} for task {task.id}'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder for task {task.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending reminder for task {task.id}: {e}')
                    )
                    skipped_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n[DRY RUN] Would send {sent_count} reminder(s), skipped {skipped_count}'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSent {sent_count} reminder(s), skipped {skipped_count}'
                )
            )
