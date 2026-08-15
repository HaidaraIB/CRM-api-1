"""
Management command to send weekly reports to company owners.

Runs hourly and delivers each company's report on `--weekday` at `--hour` in that
company's *own* timezone, deduped per company per local day. Previously a single fixed
server-time cron entry meant the report landed at the wrong local time for many tenants.

Usage:
    python manage.py send_weekly_report
    python manage.py send_weekly_report --hour 9 --weekday 0
    python manage.py send_weekly_report --company-id 1
    python manage.py send_weekly_report --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from companies.models import Company
from crm.models import Client, Deal
from notifications.dispatch import claim_dispatch, due_local_slot, local_now, mark_dispatched
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send weekly reports to company owners'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-id',
            type=int,
            help='Send report for specific company only',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days to include in report (default: 7)',
        )
        parser.add_argument(
            '--hour',
            type=int,
            default=9,
            help='Local hour (0-23) at which each company receives its report (default: 9)',
        )
        parser.add_argument(
            '--weekday',
            type=int,
            default=0,
            help='Local weekday to send on (0=Monday .. 6=Sunday, default: 0)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        company_id = options.get('company_id')
        days = options.get('days', 7)
        target_hour = options.get('hour', 9)
        target_weekday = options.get('weekday', 0)
        dry_run = options.get('dry_run', False)

        week_start = timezone.now() - timedelta(days=days)

        companies = Company.objects.filter(is_active=True).select_related('owner')
        if company_id:
            companies = companies.filter(id=company_id)

        if not companies.exists():
            self.stdout.write(
                self.style.SUCCESS('No active companies found.')
            )
            return

        sent_count = 0
        skipped_count = 0

        for company in companies:
            if not company.owner:
                skipped_count += 1
                continue

            # Deliver on the company's local report weekday, at its local hour, once per day.
            if local_now(company).weekday() != target_weekday:
                skipped_count += 1
                continue
            slot = due_local_slot(company, target_hour)
            if slot is None:
                skipped_count += 1
                continue

            # Count leads created in the last week
            leads_count = Client.objects.filter(
                company=company,
                created_at__gte=week_start
            ).count()

            # Count deals won in the last week
            deals_count = Deal.objects.filter(
                company=company,
                created_at__gte=week_start,
                stage='won'
            ).count()

            week_str = week_start.strftime('%Y-W%W')

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send weekly report to {company.owner.username} '
                        f'for company {company.id} ({company.name}) - '
                        f'{leads_count} leads, {deals_count} deals in last {days} days'
                    )
                )
            else:
                log_row = claim_dispatch(
                    user=company.owner,
                    notification_type=NotificationType.WEEKLY_REPORT,
                    obj=company,
                    scheduled_for=slot,
                    dedupe_key="weekly_report",
                )
                if log_row is None:
                    skipped_count += 1
                    continue
                try:
                    NotificationService.send_notification(
                        user=company.owner,
                        notification_type=NotificationType.WEEKLY_REPORT,
                        data={
                            'week': week_str,
                            'leads_count': leads_count,
                            'deals_count': deals_count,
                        },
                        skip_settings_check=False,  # Respect user settings
                    )
                    mark_dispatched(log_row, push_sent=True)
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent weekly report to {company.owner.username} for company {company.id} ({company.name})'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending weekly report for company {company.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending weekly report for company {company.id}: {e}')
                    )
                    mark_dispatched(log_row, error=str(e))
                    skipped_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n[DRY RUN] Would send {sent_count} report(s), skipped {skipped_count}'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSent {sent_count} report(s), skipped {skipped_count}'
                )
            )
