"""
Management command to check for expired subscriptions and send push + email notifications.
Usage:
    python manage.py check_subscription_expired
    python manage.py check_subscription_expired --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from subscriptions.models import Subscription
from notifications.services import NotificationService
from notifications.models import NotificationType
from accounts.event_emails import send_subscription_expired_email
from accounts.utils import get_email_language_for_user
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for expired subscriptions and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )
        parser.add_argument(
            '--deactivate',
            action='store_true',
            help='Deactivate expired subscriptions',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        deactivate = options.get('deactivate', False)

        today = timezone.now().date()

        subscriptions = Subscription.objects.filter(
            is_active=True,
            end_date__lt=today,
            company__owner__isnull=False,
        ).select_related('company', 'company__owner')

        if not subscriptions.exists():
            self.stdout.write(
                self.style.SUCCESS('No expired subscriptions found.')
            )
            return

        sent_count = 0
        skipped_count = 0
        deactivated_count = 0

        for subscription in subscriptions:
            owner = subscription.company.owner
            if not owner:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        "[DRY RUN] Would send push + email to %s for expired subscription %s - Expired on %s"
                        % (owner.username, subscription.id, subscription.end_date)
                    )
                )
                if deactivate:
                    self.stdout.write(
                        self.style.WARNING("[DRY RUN] Would deactivate subscription %s" % subscription.id)
                    )
                sent_count += 1
                continue

            try:
                if owner.fcm_token:
                    NotificationService.send_notification(
                        user=owner,
                        notification_type=NotificationType.SUBSCRIPTION_EXPIRED,
                        data={"expiry_date": subscription.end_date.isoformat()},
                        skip_settings_check=False,
                    )
                if owner.email:
                    language = get_email_language_for_user(owner, request=None, default="en")
                    send_subscription_expired_email(owner, subscription, language=language)
                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        "Sent notification to %s for expired subscription %s" % (owner.username, subscription.id)
                    )
                )

                if deactivate:
                    subscription.is_active = False
                    subscription.save(update_fields=["is_active"])
                    deactivated_count += 1
                    self.stdout.write(self.style.SUCCESS("Deactivated subscription %s" % subscription.id))
            except Exception as e:
                logger.error("Error sending notification for subscription %s: %s", subscription.id, e)
                self.stdout.write(
                    self.style.ERROR("Error sending notification for subscription %s: %s" % (subscription.id, e))
                )
                skipped_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n[DRY RUN] Would send {sent_count} notification(s), skipped {skipped_count}'
                )
            )
            if deactivate:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would deactivate {len(subscriptions)} subscription(s)'
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSent {sent_count} notification(s), skipped {skipped_count}'
                )
            )
            if deactivate:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Deactivated {deactivated_count} subscription(s)'
                    )
                )
