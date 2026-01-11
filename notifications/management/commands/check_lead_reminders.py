"""
Management command to check for lead reminders and send notifications
Usage:
    python manage.py check_lead_reminders
    python manage.py check_lead_reminders --minutes-before 30
    python manage.py check_lead_reminders --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import ClientTask
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for lead reminders and send notifications'

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

        # Find ClientTasks with reminders in the next window
        tasks = ClientTask.objects.filter(
            reminder_date__gte=reminder_start,
            reminder_date__lte=reminder_end,
            client__assigned_to__isnull=False,
        ).select_related('client', 'client__assigned_to')

        if not tasks.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No lead reminders found in the next {minutes_before} minutes.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for task in tasks:
            lead = task.client
            if not lead.assigned_to or not lead.assigned_to.fcm_token:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send reminder to {lead.assigned_to.username} '
                        f'for lead {lead.id} ({lead.name}) - Reminder at {task.reminder_date}'
                    )
                )
            else:
                try:
                    NotificationService.send_notification(
                        user=lead.assigned_to,
                        notification_type=NotificationType.LEAD_REMINDER,
                        data={
                            'lead_id': lead.id,
                            'lead_name': lead.name,
                            'reminder_time': task.reminder_date.isoformat(),
                        },
                        lead_source=getattr(lead, 'source', None),
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent reminder to {lead.assigned_to.username} for lead {lead.id} ({lead.name})'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder for lead {lead.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending reminder for lead {lead.id}: {e}')
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
