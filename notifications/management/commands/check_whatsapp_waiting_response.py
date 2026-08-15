"""
Alert assignees about leads whose WhatsApp conversation has gone unanswered.

Like check_lead_no_follow_up, each lead is evaluated against its own clock: the alert fires
once, at exactly `last contact + --hours`, claimed in ReminderDispatchLog. Previously this
ran as an hourly sweep with no dedupe at all, so the same lead was re-notified every single
hour indefinitely.

Run every 15 minutes so leads are alerted close to their actual due time.

Usage:
    python manage.py check_whatsapp_waiting_response
    python manage.py check_whatsapp_waiting_response --hours 24
    python manage.py check_whatsapp_waiting_response --dry-run
"""
import logging
import math

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from crm.models import Client
from notifications.dispatch import claim_dispatch, mark_dispatched
from notifications.models import NotificationType
from notifications.services import NotificationService

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
        now = timezone.now()

        threshold = now - timedelta(hours=hours)

        # NOTE: last_contacted_at is used as a proxy for "last WhatsApp exchange" —
        # the Client model does not track a dedicated last-inbound-message timestamp.
        leads = Client.objects.filter(
            last_contacted_at__lt=threshold,
            assigned_to__isnull=False,
            communication_way__name__icontains='whatsapp',
        ).select_related('assigned_to', 'communication_way')

        sent_count = 0
        skipped_count = 0

        for lead in leads.iterator(chunk_size=500):
            if not lead.assigned_to:
                skipped_count += 1
                continue

            reference = lead.last_contacted_at
            due_at = reference + timedelta(hours=hours)
            elapsed_hours = max(1, math.ceil((now - reference).total_seconds() / 3600))

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would notify {lead.assigned_to.username} for lead '
                        f'{lead.id} ({lead.name}) - waiting {elapsed_hours}h, due at {due_at.isoformat()}'
                    )
                )
                sent_count += 1
                continue

            log_row = claim_dispatch(
                user=lead.assigned_to,
                notification_type=NotificationType.WHATSAPP_WAITING_RESPONSE,
                obj=lead,
                scheduled_for=due_at,
            )
            if log_row is None:
                skipped_count += 1
                continue

            try:
                NotificationService.send_notification(
                    user=lead.assigned_to,
                    notification_type=NotificationType.WHATSAPP_WAITING_RESPONSE,
                    data={
                        'lead_id': lead.id,
                        'lead_name': lead.name,
                        'hours': elapsed_hours,
                    },
                    lead_source=getattr(lead, 'source', None),
                )
                mark_dispatched(log_row, push_sent=True)
                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Sent notification to {lead.assigned_to.username} for lead {lead.id} ({lead.name})'
                    )
                )
            except Exception as e:
                logger.error("Error sending notification for lead %s: %s", lead.id, e)
                self.stdout.write(
                    self.style.ERROR(f'Error sending notification for lead {lead.id}: {e}')
                )
                mark_dispatched(log_row, error=str(e))
                skipped_count += 1

        prefix = '[DRY RUN] Would send' if dry_run else 'Sent'
        self.stdout.write(
            self.style.SUCCESS(f'\n{prefix} {sent_count} notification(s), skipped {skipped_count}')
        )
