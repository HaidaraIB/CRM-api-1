"""
Management command to check for call reminders and send notifications
Usage:
    python manage.py check_call_reminders
    python manage.py check_call_reminders --minutes-before 15
    python manage.py check_call_reminders --window-minutes 15
    python manage.py check_call_reminders --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.contrib.contenttypes.models import ContentType
from crm.models import ClientCall
from notifications.services import NotificationService
from notifications.models import NotificationType, ReminderDispatchLog
from accounts.event_emails import send_followup_reminder_email
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for call reminders and send notifications'

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

        # Find ClientCalls with follow-up dates in the next window
        calls = ClientCall.objects.filter(
            follow_up_date__gte=reminder_start,
            follow_up_date__lt=reminder_end,
            client__assigned_to__isnull=False,
        ).select_related('client', 'client__assigned_to')

        if not calls.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No call reminders found in the next {minutes_before} minutes.')
            )
            return

        sent_count = 0
        skipped_count = 0
        dedup_skipped = 0
        ct = ContentType.objects.get_for_model(ClientCall)

        for call in calls:
            lead = call.client
            if not lead.assigned_to:
                skipped_count += 1
                continue
            user = lead.assigned_to
            scheduled_for = call.follow_up_date

            log_row, created = ReminderDispatchLog.objects.get_or_create(
                user=user,
                notification_type=NotificationType.CALL_REMINDER,
                content_type=ct,
                object_id=str(call.id),
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
                        f'for call {call.id} - Reminder at {call.follow_up_date}'
                    )
                )
            else:
                try:
                    minutes_remaining = int((call.follow_up_date - now).total_seconds() / 60)
                    NotificationService.send_notification(
                        user=user,
                        notification_type=NotificationType.CALL_REMINDER,
                        data={
                            'call_id': call.id,
                            'lead_id': lead.id,
                            'lead_name': lead.name,
                            'minutes_remaining': minutes_remaining,
                            'minutes_before': minutes_before,
                        },
                        lead_source=getattr(lead, 'source', None),
                    )
                    log_row.push_sent = True

                    email_ok = send_followup_reminder_email(
                        user,
                        reminder_kind="call",
                        title=f"Call follow-up: {lead.name}",
                        lead_name=lead.name,
                        scheduled_for=scheduled_for,
                        minutes_before=minutes_before,
                        language=getattr(user, "language", "ar") or "ar",
                    )
                    if email_ok:
                        log_row.email_sent = True
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent reminder to {user.username} for call {call.id}'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending reminder for call {call.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending reminder for call {call.id}: {e}')
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
