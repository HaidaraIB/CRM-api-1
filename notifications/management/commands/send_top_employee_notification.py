"""
Management command to send top employee notifications
Usage:
    python manage.py send_top_employee_notification
    python manage.py send_top_employee_notification --company-id 1
    python manage.py send_top_employee_notification --days 7
    python manage.py send_top_employee_notification --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from companies.models import Company
from accounts.models import User
from crm.models import Deal
from notifications.services import NotificationService
from notifications.models import NotificationType
from django.db.models import Count, Q
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send top employee notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-id',
            type=int,
            help='Send notification for specific company only',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days to calculate top employee (default: 7)',
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
            if not company.owner:
                continue

            # Get top employee by deals count
            top_employee = User.objects.filter(
                company=company,
                role='employee',
                is_active=True
            ).annotate(
                deals_count=Count(
                    'deals',
                    filter=Q(
                        deals__created_at__gte=week_start,
                        deals__stage='won',
                        deals__company=company
                    )
                )
            ).order_by('-deals_count').first()

            if not top_employee or top_employee.deals_count == 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'No top employee found for company {company.id} ({company.name})'
                    )
                )
                continue

            # Notify company owner
            if company.owner.fcm_token:
                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'[DRY RUN] Would send top employee notification to {company.owner.username} '
                            f'for company {company.id} ({company.name}) - '
                            f'Top employee: {top_employee.username} with {top_employee.deals_count} deals'
                        )
                    )
                else:
                    try:
                        NotificationService.send_notification(
                            user=company.owner,
                            notification_type=NotificationType.TOP_EMPLOYEE,
                            data={
                                'employee_id': top_employee.id,
                                'employee_name': top_employee.get_full_name() or top_employee.username,
                                'deals_count': top_employee.deals_count,
                            },
                            skip_settings_check=False,  # Respect user settings
                        )
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Error sending top employee notification to owner for company {company.id}: {e}")
                        skipped_count += 1

            # Notify top employee
            if top_employee.fcm_token:
                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'[DRY RUN] Would send top employee notification to {top_employee.username}'
                        )
                    )
                else:
                    try:
                        NotificationService.send_notification(
                            user=top_employee,
                            notification_type=NotificationType.TOP_EMPLOYEE,
                            data={
                                'employee_id': top_employee.id,
                                'employee_name': top_employee.get_full_name() or top_employee.username,
                                'deals_count': top_employee.deals_count,
                            },
                            skip_settings_check=False,  # Respect user settings
                        )
                        sent_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Sent top employee notification to {top_employee.username} '
                                f'({top_employee.deals_count} deals)'
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error sending top employee notification to employee {top_employee.id}: {e}")
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
