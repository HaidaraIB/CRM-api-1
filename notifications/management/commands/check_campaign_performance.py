"""
Management command to check campaign performance and send notifications
Usage:
    python manage.py check_campaign_performance
    python manage.py check_campaign_performance --check-low-performance
    python manage.py check_campaign_performance --check-budget-alert
    python manage.py check_campaign_performance --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import Campaign, Client
from notifications.services import NotificationService
from notifications.models import NotificationType
from django.db.models import Count, Q
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check campaign performance and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--check-low-performance',
            action='store_true',
            help='Check for low performing campaigns',
        )
        parser.add_argument(
            '--check-budget-alert',
            action='store_true',
            help='Check for campaigns with low budget',
        )
        parser.add_argument(
            '--budget-threshold',
            type=int,
            default=20,
            help='Budget percentage threshold for alert (default: 20)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def handle(self, *args, **options):
        check_low = options.get('check_low_performance', False)
        check_budget = options.get('check_budget_alert', False)
        budget_threshold = options.get('budget_threshold', 20)
        dry_run = options.get('dry_run', False)

        if not check_low and not check_budget:
            # Check both by default
            check_low = True
            check_budget = True

        sent_count = 0
        skipped_count = 0

        campaigns = Campaign.objects.filter(is_active=True).select_related('company', 'company__owner')

        for campaign in campaigns:
            if not campaign.company or not campaign.company.owner:
                continue

            # Check low performance
            if check_low:
                today = timezone.now().date()
                today_leads = Client.objects.filter(
                    campaign=campaign,
                    created_at__date=today
                ).count()

                # Calculate average daily leads (last 7 days)
                week_ago = today - timedelta(days=7)
                week_leads = Client.objects.filter(
                    campaign=campaign,
                    created_at__date__gte=week_ago
                ).count()
                avg_daily = week_leads / 7 if week_leads > 0 else 0

                if avg_daily > 0 and today_leads < avg_daily * 0.5:  # Less than 50% of average
                    if dry_run:
                        self.stdout.write(
                            self.style.WARNING(
                                f'[DRY RUN] Would send low performance alert for campaign {campaign.id} ({campaign.name})'
                            )
                        )
                    else:
                        try:
                            NotificationService.send_notification(
                                user=campaign.company.owner,
                                notification_type=NotificationType.CAMPAIGN_LOW_PERFORMANCE,
                                data={
                                    'campaign_id': campaign.id,
                                    'campaign_name': campaign.name,
                                    'today_leads': today_leads,
                                },
                                skip_settings_check=False,  # Respect user settings
                            )
                            sent_count += 1
                        except Exception as e:
                            logger.error(f"Error sending low performance notification for campaign {campaign.id}: {e}")
                            skipped_count += 1

            # Check budget alert
            if check_budget and campaign.budget and hasattr(campaign, 'spent'):
                remaining = campaign.budget - (campaign.spent or 0)
                remaining_percent = (remaining / campaign.budget) * 100 if campaign.budget > 0 else 0

                if remaining_percent < budget_threshold:
                    if dry_run:
                        self.stdout.write(
                            self.style.WARNING(
                                f'[DRY RUN] Would send budget alert for campaign {campaign.id} ({campaign.name}) - {remaining_percent:.1f}% remaining'
                            )
                        )
                    else:
                        try:
                            NotificationService.send_notification(
                                user=campaign.company.owner,
                                notification_type=NotificationType.CAMPAIGN_BUDGET_ALERT,
                                data={
                                    'campaign_id': campaign.id,
                                    'campaign_name': campaign.name,
                                    'remaining_percent': round(remaining_percent, 1),
                                },
                                skip_settings_check=False,  # Respect user settings
                            )
                            sent_count += 1
                        except Exception as e:
                            logger.error(f"Error sending budget alert for campaign {campaign.id}: {e}")
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
