"""
Management command to send weekly reports to company owners
Usage:
    python manage.py send_weekly_report
    python manage.py send_weekly_report --company-id 1
    python manage.py send_weekly_report --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from companies.models import Company
from crm.models import Client, Deal
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
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        company_id = options.get('company_id')
        days = options.get('days', 7)
        dry_run = options.get('dry_run', False)

        week_start = timezone.now() - timedelta(days=days)

        companies = Company.objects.filter(is_active=True)
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
            if not company.owner or not company.owner.fcm_token:
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
