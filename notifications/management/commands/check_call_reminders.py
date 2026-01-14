"""
Management command to check for call reminders and send notifications
Usage:
    python manage.py check_call_reminders
    python manage.py check_call_reminders --minutes-before 30
    python manage.py check_call_reminders --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import ClientCall
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for call reminders and send notifications'

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

        # Find ClientCalls with follow-up dates in the next window
        calls = ClientCall.objects.filter(
            follow_up_date__gte=reminder_start,
            follow_up_date__lte=reminder_end,
            client__assigned_to__isnull=False,
        ).select_related('client', 'client__assigned_to')

        if not calls.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No call reminders found in the next {minutes_before} minutes.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for call in calls:
            lead = call.client
            if not lead.assigned_to or not lead.assigned_to.fcm_token:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send reminder to {lead.assigned_to.username} '
                        f'for call {call.id} - Reminder at {call.follow_up_date}'
                    )
                )
            else:
                try:
                    minutes_remaining = int((call.follow_up_date - now).total_seconds() / 60)
                    NotificationService.send_notification(
                        user=lead.assigned_to,
                        notification_type=NotificationType.CALL_REMINDER,
                        data={
                            'call_id': call.id,
                            'lead_id': lead.id,
                            'lead_name': lead.name,
                            'minutes_remaining': minutes_remaining,
                        },
                        lead_source=getattr(lead, 'source', None),
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent reminder to {lead.assigned_to.username} for call {call.id}'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder for call {call.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending reminder for call {call.id}: {e}')
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
