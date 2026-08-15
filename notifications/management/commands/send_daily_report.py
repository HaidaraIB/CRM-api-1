"""
Management command to send daily reports to company owners.

Runs hourly and delivers each company's report at `--hour` in that company's *own*
timezone, deduped per company per local day. Previously it ran once at a fixed server
hour, so a "9am" report landed in the middle of the night for tenants in other zones.

Usage:
    python manage.py send_daily_report
    python manage.py send_daily_report --hour 9
    python manage.py send_daily_report --company-id 1
    python manage.py send_daily_report --dry-run
"""
from django.core.management.base import BaseCommand
from datetime import date
from companies.models import Company
from crm.models import Client, Deal
from notifications.dispatch import claim_dispatch, due_local_slot, local_now, mark_dispatched
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send daily reports to company owners'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-id',
            type=int,
            help='Send report for specific company only',
        )
        parser.add_argument(
            '--date',
            type=str,
            help='Date to generate report for (YYYY-MM-DD), default: each company\'s local today',
        )
        parser.add_argument(
            '--hour',
            type=int,
            default=9,
            help='Local hour (0-23) at which each company receives its report (default: 9)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        company_id = options.get('company_id')
        date_str = options.get('date')
        target_hour = options.get('hour', 9)
        dry_run = options.get('dry_run', False)

        forced_date = None
        if date_str:
            try:
                forced_date = date.fromisoformat(date_str)
            except ValueError:
                self.stdout.write(
                    self.style.ERROR('Invalid date format. Use YYYY-MM-DD')
                )
                return

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

            # Deliver at the company's own local hour, once per local day.
            slot = due_local_slot(company, target_hour)
            if slot is None:
                skipped_count += 1
                continue

            report_date = forced_date or local_now(company).date()

            # Count leads created today
            leads_count = Client.objects.filter(
                company=company,
                created_at__date=report_date
            ).count()

            # Count deals won today
            deals_count = Deal.objects.filter(
                company=company,
                created_at__date=report_date,
                stage='won'
            ).count()

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would send daily report to {company.owner.username} '
                        f'for company {company.id} ({company.name}) - '
                        f'{leads_count} leads, {deals_count} deals on {report_date}'
                    )
                )
            else:
                log_row = claim_dispatch(
                    user=company.owner,
                    notification_type=NotificationType.DAILY_REPORT,
                    obj=company,
                    scheduled_for=slot,
                    dedupe_key="daily_report",
                )
                if log_row is None:
                    skipped_count += 1
                    continue
                try:
                    NotificationService.send_notification(
                        user=company.owner,
                        notification_type=NotificationType.DAILY_REPORT,
                        data={
                            'date': report_date.isoformat(),
                            'leads_count': leads_count,
                            'deals_count': deals_count,
                        },
                        skip_settings_check=False,  # Respect user settings
                    )
                    mark_dispatched(log_row, push_sent=True)
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Sent daily report to {company.owner.username} for company {company.id} ({company.name})'
                        )
                    )
                except Exception as e:
                    logger.error(f"Error sending daily report for company {company.id}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f'Error sending daily report for company {company.id}: {e}')
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
