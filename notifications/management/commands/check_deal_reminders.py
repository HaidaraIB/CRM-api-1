"""
Management command to check for deal reminders and send notifications
Usage:
    python manage.py check_deal_reminders
    python manage.py check_deal_reminders --hours-before 1
    python manage.py check_deal_reminders --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import Deal
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for deal reminders and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours-before',
            type=int,
            default=1,
            help='Number of hours before reminder time to send notification (default: 1)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        hours_before = options.get('hours_before', 1)
        dry_run = options.get('dry_run', False)

        now = timezone.now()
        reminder_start = now + timedelta(hours=hours_before)
        reminder_end = now + timedelta(hours=hours_before + 1)  # 1 hour window

        # Note: Deal model doesn't have reminder_date field currently
        # This command assumes you'll add it or use another field like start_date
        # For now, we'll check deals with start_date in the next hour
        deals = Deal.objects.filter(
            start_date__gte=reminder_start.date() if reminder_start else None,
            start_date__lte=reminder_end.date() if reminder_end else None,
            employee__isnull=False,
            stage__in=['in_progress', 'on_hold'],  # Only active deals
        ).select_related('employee', 'client')

        if not deals.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No deal reminders found in the next {hours_before} hours.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for deal in deals:
            if not deal.employee or not deal.employee.fcm_token:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send reminder to {deal.employee.username} '
                        f'for deal {deal.id} ({deal.client.name})'
                    )
                )
            else:
                try:
                    NotificationService.send_notification(
                        user=deal.employee,
                        notification_type=NotificationType.DEAL_REMINDER,
                        data={
                            'deal_id': deal.id,
                            'deal_title': f'{deal.client.name} - {deal.value or 0}',
                        },
                        skip_settings_check=False,  # Respect user settings
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent reminder to {deal.employee.username} for deal {deal.id}'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder for deal {deal.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending reminder for deal {deal.id}: {e}')
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
