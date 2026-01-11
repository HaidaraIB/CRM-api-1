"""
Management command to check for expired subscriptions and send notifications
Usage:
    python manage.py check_subscription_expired
    python manage.py check_subscription_expired --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from subscriptions.models import Subscription
from notifications.services import NotificationService
from notifications.models import NotificationType
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
            status='active',
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
            if not owner or not owner.fcm_token:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'[DRY RUN] Would send notification to {owner.username} '
                        f'for expired subscription {subscription.id} - Expired on {subscription.end_date}'
                    )
                )
                if deactivate:
                    self.stdout.write(
                        self.style.WARNING(
                            f'[DRY RUN] Would deactivate subscription {subscription.id}'
                        )
                    )
            else:
                try:
                    NotificationService.send_notification(
                        user=owner,
                        notification_type=NotificationType.SUBSCRIPTION_EXPIRED,
                        data={
                            'expiry_date': subscription.end_date.isoformat(),
                        },
                        skip_settings_check=False,  # Respect user settings (but subscription notifications are critical)
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent notification to {owner.username} for expired subscription {subscription.id}'
                        )
                    )

                    if deactivate:
                        subscription.status = 'expired'
                        subscription.is_active = False
                        subscription.save(update_fields=['status', 'is_active'])
                        deactivated_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Deactivated subscription {subscription.id}'
                            )
                        )
                except Exception as e:
                    logger.error(f"Error sending notification for subscription {subscription.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending notification for subscription {subscription.id}: {e}')
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
