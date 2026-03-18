"""
Management command to check for deal reminders and send notifications
Usage:
    python manage.py check_deal_reminders
    python manage.py check_deal_reminders --minutes-before 15
    python manage.py check_deal_reminders --window-minutes 15
    python manage.py check_deal_reminders --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.contrib.contenttypes.models import ContentType
from crm.models import Deal
from notifications.services import NotificationService
from notifications.models import NotificationType, ReminderDispatchLog
from accounts.event_emails import send_followup_reminder_email
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for deal reminders and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--minutes-before',
            type=int,
            default=15,
            help='Number of minutes before reminder time to send notification (default: 15)',
        )
        parser.add_argument(
            '--window-minutes',
            type=int,
            default=15,
            help='Window size in minutes starting at (now + minutes-before). Default: 15. Should be >= cron interval to avoid missing reminders.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        minutes_before = options.get('minutes_before', 15)
        window_minutes = options.get('window_minutes', 15)
        dry_run = options.get('dry_run', False)

        now = timezone.now()
        reminder_start = now + timedelta(minutes=minutes_before)
        reminder_end = reminder_start + timedelta(minutes=window_minutes)

        # Deals with reminder_date in the next window
        deals = Deal.objects.filter(
            reminder_date__gte=reminder_start,
            reminder_date__lt=reminder_end,
            employee__isnull=False,
            stage__in=['in_progress', 'on_hold'],  # Only active deals
        ).select_related('employee', 'client')

        if not deals.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No deal reminders found in the next {minutes_before} minutes.')
            )
            return

        sent_count = 0
        skipped_count = 0
        dedup_skipped = 0
        ct = ContentType.objects.get_for_model(Deal)

        for deal in deals:
            if not deal.employee:
                skipped_count += 1
                continue
            user = deal.employee
            scheduled_for = deal.reminder_date

            log_row, created = ReminderDispatchLog.objects.get_or_create(
                user=user,
                notification_type=NotificationType.DEAL_REMINDER,
                content_type=ct,
                object_id=str(deal.id),
                scheduled_for=scheduled_for,
                minutes_before=minutes_before,
                defaults={"push_sent": False, "email_sent": False},
            )
            if not created and log_row.push_sent and log_row.email_sent:
                dedup_skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send reminder to {user.username} '
                        f'for deal {deal.id} ({deal.client.name})'
                    )
                )
            else:
                try:
                    NotificationService.send_notification(
                        user=user,
                        notification_type=NotificationType.DEAL_REMINDER,
                        data={
                            'deal_id': deal.id,
                            'deal_title': f'{deal.client.name} - {deal.value or 0}',
                            'minutes_before': minutes_before,
                        },
                        skip_settings_check=False,  # Respect user settings
                    )
                    log_row.push_sent = True

                    email_ok = send_followup_reminder_email(
                        user,
                        reminder_kind="deal",
                        title=f"Deal follow-up: {deal.client.name}",
                        lead_name=deal.client.name,
                        scheduled_for=scheduled_for,
                        minutes_before=minutes_before,
                        language=getattr(user, "language", "ar") or "ar",
                    )
                    if email_ok:
                        log_row.email_sent = True
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent reminder to {user.username} for deal {deal.id}'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder for deal {deal.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending reminder for deal {deal.id}: {e}')
                    )
                    log_row.last_error = str(e)
                    skipped_count += 1
                finally:
                    log_row.save(update_fields=["push_sent", "email_sent", "last_error", "updated_at"])

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n[DRY RUN] Would send {sent_count} reminder(s), skipped {skipped_count}, dedup_skipped {dedup_skipped}'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSent {sent_count} reminder(s), skipped {skipped_count}, dedup_skipped {dedup_skipped}'
                )
            )
