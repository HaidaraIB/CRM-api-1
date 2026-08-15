"""
Management command to check for expiring subscriptions and send push + email notifications.
Usage:
    python manage.py check_subscription_expiring
    python manage.py check_subscription_expiring --days-before 3
    python manage.py check_subscription_expiring --hour 9
    python manage.py check_subscription_expiring --dry-run

Runs hourly and notifies each owner at --hour in their company's own timezone, deduped per
subscription per local day, instead of a single fixed server-time daily cron entry.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from subscriptions.models import Subscription
from notifications.dispatch import claim_dispatch, due_local_slot, mark_dispatched
from notifications.services import NotificationService
from notifications.models import NotificationType
from accounts.event_emails import send_subscription_expiring_email
from accounts.utils import get_email_language_for_user
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for expiring subscriptions and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days-before',
            type=int,
            default=3,
            help='Number of days before expiry to send notification (default: 3)',
        )
        parser.add_argument(
            '--hour',
            type=int,
            default=9,
            help="Local hour (0-23) at which each owner is notified (default: 9)",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        days_before = options.get('days_before', 3)
        target_hour = options.get('hour', 9)
        dry_run = options.get('dry_run', False)

        expiry_date = timezone.now().date() + timedelta(days=days_before)

        subscriptions = Subscription.objects.filter(
            is_active=True,
            end_date=expiry_date,
            company__owner__isnull=False,
        ).select_related('company', 'company__owner')

        if not subscriptions.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No subscriptions found expiring in {days_before} days.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for subscription in subscriptions:
            owner = subscription.company.owner
            if not owner:
                skipped_count += 1
                continue

            # Notify at the company's own local hour, once per local day.
            slot = due_local_slot(subscription.company, target_hour)
            if slot is None:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        "[DRY RUN] Would send push + email to %s for subscription %s - Expires in %s days"
                        % (owner.username, subscription.id, days_before)
                    )
                )
                sent_count += 1
                continue

            log_row = claim_dispatch(
                user=owner,
                notification_type=NotificationType.SUBSCRIPTION_EXPIRING,
                obj=subscription,
                scheduled_for=slot,
                dedupe_key=f"expiring_{days_before}",
            )
            if log_row is None:
                skipped_count += 1
                continue

            try:
                NotificationService.send_notification(
                    user=owner,
                    notification_type=NotificationType.SUBSCRIPTION_EXPIRING,
                    data={
                        "days_remaining": days_before,
                        "expiry_date": subscription.end_date.isoformat(),
                    },
                    skip_settings_check=False,
                )
                # Email in owner's language
                if owner.email:
                    language = get_email_language_for_user(owner, request=None, default="en")
                    send_subscription_expiring_email(
                        owner, subscription, days_before, language=language
                    )
                mark_dispatched(log_row, push_sent=True)
                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        "Sent notification to %s for subscription %s" % (owner.username, subscription.id)
                    )
                )
            except Exception as e:
                logger.error("Error sending notification for subscription %s: %s", subscription.id, e)
                self.stdout.write(
                    self.style.ERROR("Error sending notification for subscription %s: %s" % (subscription.id, e))
                )
                mark_dispatched(log_row, error=str(e))
                skipped_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n[DRY RUN] Would send {sent_count} notification(s), skipped {skipped_count}'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSent {sent_count} notification(s), skipped {skipped_count}'
                )
            )
