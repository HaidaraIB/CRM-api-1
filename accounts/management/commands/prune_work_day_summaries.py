"""
Delete old WorkDaySummary rows (measured CRM usage time).

Unlike the dispatch log, this table is small by construction — one row per user per
worked day, so roughly 250 rows/user/year. Pruning is therefore a data-minimisation
control rather than a capacity one: measured working hours are employee activity data,
and some tenants will want a retention ceiling on it.

Deleting rows only removes *history*; it never affects live tracking, which reads the
cursor from ``User.work_last_ping_at``. Keep --days well beyond any range managers
still report on, since pruned days silently drop out of the Employees Report.

Usage:
    python manage.py prune_work_day_summaries
    python manage.py prune_work_day_summaries --days 365
    python manage.py prune_work_day_summaries --dry-run
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import WorkDaySummary

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


class Command(BaseCommand):
    help = 'Delete WorkDaySummary rows older than --days (default: 730)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=730,
            help='Delete rows whose work_date is older than this many days (default: 730)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report how many rows would be deleted without deleting them',
        )

    def handle(self, *args, **options):
        days = options.get('days', 730)
        dry_run = options.get('dry_run', False)

        if days < 1:
            self.stdout.write(self.style.ERROR('--days must be at least 1.'))
            return

        # work_date is a company-local calendar date, so compare against a date, not
        # an aware datetime. A day's worth of imprecision across timezones is
        # irrelevant at a two-year cutoff.
        cutoff = (timezone.now() - timedelta(days=days)).date()
        stale = WorkDaySummary.objects.filter(work_date__lt=cutoff)

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY RUN] Would delete {stale.count()} work day row(s) older than {cutoff.isoformat()}'
                )
            )
            return

        deleted_total = 0
        while True:
            batch_ids = list(stale.values_list('id', flat=True)[:BATCH_SIZE])
            if not batch_ids:
                break
            deleted, _ = WorkDaySummary.objects.filter(id__in=batch_ids).delete()
            deleted_total += deleted

        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted {deleted_total} work day row(s) older than {cutoff.isoformat()}'
            )
        )
