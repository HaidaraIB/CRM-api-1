"""
Delete old ReminderDispatchLog rows.

The dispatch log is an append-only ledger written by every scheduled notification job, so
it grows without bound. Rows only need to outlive the window in which a job could
re-evaluate the same due instant; anything older is dead weight.

Keep --days comfortably larger than the longest escalation window (currently 3x the
no-follow-up SLA) so pruning can never resurrect an already-sent notification.

Usage:
    python manage.py prune_dispatch_logs
    python manage.py prune_dispatch_logs --days 30
    python manage.py prune_dispatch_logs --dry-run
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from notifications.models import ReminderDispatchLog

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


class Command(BaseCommand):
    help = 'Delete ReminderDispatchLog rows older than --days (default: 90)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Delete rows whose scheduled_for is older than this many days (default: 90)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report how many rows would be deleted without deleting them',
        )

    def handle(self, *args, **options):
        days = options.get('days', 90)
        dry_run = options.get('dry_run', False)

        if days < 1:
            self.stdout.write(self.style.ERROR('--days must be at least 1.'))
            return

        cutoff = timezone.now() - timedelta(days=days)
        stale = ReminderDispatchLog.objects.filter(scheduled_for__lt=cutoff)

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY RUN] Would delete {stale.count()} dispatch log row(s) older than {cutoff.isoformat()}'
                )
            )
            return

        deleted_total = 0
        while True:
            batch_ids = list(stale.values_list('id', flat=True)[:BATCH_SIZE])
            if not batch_ids:
                break
            deleted, _ = ReminderDispatchLog.objects.filter(id__in=batch_ids).delete()
            deleted_total += deleted

        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted {deleted_total} dispatch log row(s) older than {cutoff.isoformat()}'
            )
        )
