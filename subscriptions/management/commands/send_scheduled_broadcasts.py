"""
Management command to send scheduled broadcast emails.

This command should be run periodically (e.g., every minute via cron) to check
for scheduled broadcasts and send them when their scheduled time arrives.

Usage:
    python manage.py send_scheduled_broadcasts

For cron, add to crontab:
    # Run every minute
    * * * * * cd /path/to/project && python manage.py send_scheduled_broadcasts
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from subscriptions.models import (
    SENDING_CLAIM_STALE_AFTER,
    Broadcast,
    BroadcastStatus,
    BroadcastType,
)
from subscriptions.utils import send_broadcast_email, send_broadcast_push_notification
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send scheduled broadcast emails that are due'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually sending emails',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output for each broadcast',
        )
        parser.add_argument(
            '--check-minutes',
            type=int,
            default=1,
            help='Check for broadcasts scheduled within the last N minutes (default: 1)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        check_minutes = options['check_minutes']
        
        now = timezone.now()
        
        # Find all pending broadcasts that are scheduled to be sent
        # We check broadcasts scheduled in the past (up to check_minutes ago)
        # to account for any delays in running the command
        from datetime import timedelta
        time_threshold = now - timedelta(minutes=check_minutes)
        
        # Due broadcasts, plus any whose send died partway.
        #
        # The second arm is the recovery path and deliberately ignores the
        # check-minutes window: a broadcast whose sender was killed is stuck in
        # SENDING, and the window would have moved past it long before anyone
        # noticed. It is safe to pick up because claim_for_sending only hands over
        # a stale claim, and delivered_user_ids means the resumed send skips
        # everyone who already received it.
        scheduled_broadcasts = Broadcast.objects.filter(
            Q(
                status=BroadcastStatus.PENDING.value,
                scheduled_at__lte=now,
                scheduled_at__gte=time_threshold,
            )
            | Q(
                status=BroadcastStatus.SENDING.value,
                sending_started_at__lt=now - SENDING_CLAIM_STALE_AFTER,
            )
        ).order_by('scheduled_at')
        
        count = scheduled_broadcasts.count()
        
        if count == 0:
            if verbose:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'No scheduled broadcasts found to send (checked up to {check_minutes} minute(s) ago).'
                    )
                )
            return
        
        self.stdout.write(
            f'Found {count} scheduled broadcast(s) ready to send.'
        )
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No emails will be sent')
            )
        
        sent_count = 0
        failed_count = 0
        
        for broadcast in scheduled_broadcasts:
            # None for a broadcast that was sent immediately from the admin panel
            # and stalled — those reach us through the stale-claim arm above.
            scheduled_time = (
                broadcast.scheduled_at.strftime('%Y-%m-%d %H:%M:%S')
                if broadcast.scheduled_at
                else 'immediate'
            )
            
            if verbose:
                from subscriptions.utils import get_broadcast_targets_list
                targets_list = get_broadcast_targets_list(broadcast)
                self.stdout.write(
                    f'  - Broadcast ID {broadcast.id}: "{broadcast.subject}" '
                    f'(scheduled for {scheduled_time}, targets: {targets_list})'
                )
            
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'  [DRY RUN] Would send broadcast {broadcast.id} now'
                    )
                )
                sent_count += 1
                continue

            # Take the row before sending anything. Losing the race is the normal,
            # expected outcome whenever this run overlaps another sender — an
            # earlier run still working through a long recipient list, or an admin
            # pressing "send now" — so it is skipped quietly rather than logged as
            # a failure. Without this both senders pass the status check above and
            # every recipient gets the broadcast twice.
            if not broadcast.claim_for_sending():
                if verbose:
                    self.stdout.write(
                        f'    - Skipped: broadcast {broadcast.id} is already '
                        f'being sent by another process'
                    )
                continue

            # Send by broadcast type (email or push)
            broadcast_type = broadcast.broadcast_type or BroadcastType.EMAIL.value
            if broadcast_type == BroadcastType.PUSH.value:
                result = send_broadcast_push_notification(broadcast)
            else:
                result = send_broadcast_email(broadcast)
            
            if result.get('success'):
                # Update broadcast status. Clearing the claim timestamp keeps
                # "when did the in-flight send start" meaningful only while a send
                # is actually in flight.
                broadcast.status = BroadcastStatus.SENT.value
                broadcast.sent_at = timezone.now()
                broadcast.sending_started_at = None
                broadcast.save()
                
                sent_count += 1
                recipients_count = result.get('recipients_count', 0)
                
                if verbose:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'    ✓ Sent successfully to {recipients_count} recipient(s)'
                        )
                    )
                
                logger.info(
                    f"Broadcast {broadcast.id} sent successfully to {recipients_count} recipients"
                )
            else:
                # Update broadcast status to failed, releasing the claim so it is
                # not left looking like a send that is still running.
                broadcast.status = BroadcastStatus.FAILED.value
                broadcast.sending_started_at = None
                broadcast.save()
                
                failed_count += 1
                error_message = result.get('error', 'Unknown error')
                
                if verbose:
                    self.stdout.write(
                        self.style.ERROR(
                            f'    ✗ Failed to send: {error_message}'
                        )
                    )
                
                logger.error(
                    f"Failed to send broadcast {broadcast.id}: {error_message}"
                )
        
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Would send {sent_count} broadcast(s).'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully sent {sent_count} broadcast(s).'
                )
            )
            if failed_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'Failed to send {failed_count} broadcast(s).'
                    )
                )
