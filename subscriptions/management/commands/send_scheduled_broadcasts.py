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
from django.utils import timezone
from subscriptions.models import Broadcast, BroadcastStatus
from subscriptions.utils import send_broadcast_email
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
        
        scheduled_broadcasts = Broadcast.objects.filter(
            status=BroadcastStatus.PENDING.value,
            scheduled_at__lte=now,
            scheduled_at__gte=time_threshold
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
            scheduled_time = broadcast.scheduled_at.strftime('%Y-%m-%d %H:%M:%S')
            
            if verbose:
                self.stdout.write(
                    f'  - Broadcast ID {broadcast.id}: "{broadcast.subject}" '
                    f'(scheduled for {scheduled_time}, target: {broadcast.target})'
                )
            
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'  [DRY RUN] Would send broadcast {broadcast.id} now'
                    )
                )
                sent_count += 1
                continue
            
            # Send the broadcast email
            result = send_broadcast_email(broadcast)
            
            if result.get('success'):
                # Update broadcast status
                broadcast.status = BroadcastStatus.SENT.value
                broadcast.sent_at = timezone.now()
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
                # Update broadcast status to failed
                broadcast.status = BroadcastStatus.FAILED.value
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
