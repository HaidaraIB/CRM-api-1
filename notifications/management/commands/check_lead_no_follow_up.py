"""
Management command to check for leads without follow-up and send notifications.
This command runs 4 times a day and checks for leads that haven't been contacted
since the last check. Once any action is taken on a lead (status change, task, call, etc.),
the last_contacted_at field is updated and the lead will no longer receive this notification.
At most 3 "no follow-up" notifications are sent in a row per client; after that, no further
notification is sent until the lead is contacted again (last_contacted_at is updated).

Usage:
    python manage.py check_lead_no_follow_up
    python manage.py check_lead_no_follow_up --hours 6
    python manage.py check_lead_no_follow_up --dry-run
"""
import math
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import models
from datetime import timedelta
from crm.models import Client
from notifications.services import NotificationService
from notifications.models import NotificationType, Notification
import logging

# Maximum number of "no follow-up" notifications sent in a row per client before we stop until next contact
MAX_NO_FOLLOW_UP_IN_ROW = 3

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for leads without follow-up and send notifications (runs 4 times a day)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=6,
            help='Number of hours without follow-up to trigger notification (default: 6, for 4 times daily)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        hours = options.get('hours', 6)
        dry_run = options.get('dry_run', False)

        threshold = timezone.now() - timedelta(hours=hours)

        # Find leads without follow-up
        # Only leads where last_contacted_at is older than threshold OR is None
        leads = Client.objects.filter(
            assigned_to__isnull=False,
            company__isnull=False,
        ).filter(
            # Either never contacted or last contact was before threshold
            models.Q(last_contacted_at__lt=threshold) | models.Q(last_contacted_at__isnull=True)
        ).select_related('assigned_to', 'company')

        if not leads.exists():
            self.stdout.write(
                self.style.SUCCESS(f'No leads found without follow-up for {hours} hours.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for lead in leads:
            if not lead.assigned_to or not lead.assigned_to.has_any_fcm_token():
                skipped_count += 1
                continue

            # Reference time: last contact, or assignment, or creation (real value for display)
            reference = (
                lead.last_contacted_at
                or lead.assigned_at
                or lead.created_at
            )
            # Only notify when the lead has had no action for at least `hours` (e.g. 6h)
            if reference > threshold:
                skipped_count += 1
                continue
            seconds = max(0, (timezone.now() - reference).total_seconds())
            # ceil so e.g. 30 min → 1 hour (real calculated value); never show 0
            hours_display = max(1, math.ceil(seconds / 3600))

            # Cap at MAX_NO_FOLLOW_UP_IN_ROW notifications in a row per client (since last contact/assignment/creation)
            no_follow_up_count = Notification.objects.filter(
                user=lead.assigned_to,
                type=NotificationType.LEAD_NO_FOLLOW_UP,
                data__lead_id=lead.id,
                created_at__gt=reference,
            ).count()
            if no_follow_up_count >= MAX_NO_FOLLOW_UP_IN_ROW:
                skipped_count += 1
                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f'[DRY RUN] Would skip lead {lead.id} ({lead.name}): '
                            f'already {no_follow_up_count} no-follow-up notification(s) in row (max {MAX_NO_FOLLOW_UP_IN_ROW})'
                        )
                    )
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send notification to {lead.assigned_to.username} '
                        f'for lead {lead.id} ({lead.name}) - No follow-up for {hours_display} hours'
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
                            'hours': hours_display,
                        },
                        lead_source=getattr(lead, 'source', None),
                    )
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent notification to {lead.assigned_to.username} for lead {lead.id} ({lead.name}) - {hours_display} hours without follow-up'
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
