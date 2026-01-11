"""
Management command to check for WhatsApp messages waiting for response
Usage:
    python manage.py check_whatsapp_waiting_response
    python manage.py check_whatsapp_waiting_response --hours 24
    python manage.py check_whatsapp_waiting_response --dry-run
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
    help = 'Check for WhatsApp messages waiting for response and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Number of hours without response to trigger notification (default: 24)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        hours = options.get('hours', 24)
        dry_run = options.get('dry_run', False)

        threshold = timezone.now() - timedelta(hours=hours)

        # Find leads with last WhatsApp message sent before threshold
        # Note: This requires tracking last_message_sent_at in Client model
        # For now, we'll use last_contacted_at as a proxy
        leads = Client.objects.filter(
            last_contacted_at__lt=threshold,
            assigned_to__isnull=False,
            communication_way__name__icontains='whatsapp',  # Assuming WhatsApp channel exists
        ).select_related('assigned_to', 'communication_way')

        if not leads.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No leads found waiting for WhatsApp response for {hours} hours.')
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
                        f'for lead {lead.id} ({lead.name}) - Waiting for response for {hours} hours'
                    )
                )
            else:
                try:
                    NotificationService.send_notification(
                        user=lead.assigned_to,
                        notification_type=NotificationType.WHATSAPP_WAITING_RESPONSE,
                        data={
                            'lead_id': lead.id,
                            'lead_name': lead.name,
                            'hours': hours,
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
