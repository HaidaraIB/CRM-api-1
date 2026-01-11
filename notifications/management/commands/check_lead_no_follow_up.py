"""
Management command to check for leads without follow-up and send notifications
Usage:
    python manage.py check_lead_no_follow_up
    python manage.py check_lead_no_follow_up --minutes 30
    python manage.py check_lead_no_follow_up --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import Client
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for leads without follow-up and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--minutes',
            type=int,
            default=30,
            help='Number of minutes without follow-up to trigger notification (default: 30)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        minutes = options.get('minutes', 30)
        dry_run = options.get('dry_run', False)

        threshold = timezone.now() - timedelta(minutes=minutes)

        # Find leads without follow-up
        leads = Client.objects.filter(
            last_contacted_at__lt=threshold,
            assigned_to__isnull=False,
            company__isnull=False,
        ).select_related('assigned_to', 'company')

        if not leads.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No leads found without follow-up for {minutes} minutes.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for lead in leads:
            if not lead.assigned_to or not lead.assigned_to.fcm_token:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send notification to {lead.assigned_to.username} '
                        f'for lead {lead.id} ({lead.name}) - No follow-up for {minutes} minutes'
                    )
                )
            else:
                try:
                    NotificationService.send_notification(
                        user=lead.assigned_to,
                        notification_type=NotificationType.LEAD_NO_FOLLOW_UP,
                        data={
                            'lead_id': lead.id,
                            'lead_name': lead.name,
                            'minutes': minutes,
                        },
                        lead_source=getattr(lead, 'source', None),
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent notification to {lead.assigned_to.username} for lead {lead.id} ({lead.name})'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending notification for lead {lead.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending notification for lead {lead.id}: {e}')
                    )
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
