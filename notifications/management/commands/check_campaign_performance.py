"""
Management command to check campaign performance and send notifications
Usage:
    python manage.py check_campaign_performance
    python manage.py check_campaign_performance --check-low-performance
    python manage.py check_campaign_performance --check-budget-alert
    python manage.py check_campaign_performance --dry-run

Both alerts are deduped so a campaign that sits below threshold does not re-alert forever:
low-performance fires at most once per campaign per local day, and the budget alert fires
once per crossed threshold bucket (20% / 10% / 5%) per campaign.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crm.models import Campaign, Client
from notifications.dispatch import claim_dispatch, due_local_slot, mark_dispatched
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
            '--hour',
            type=int,
            default=10,
            help='Local hour (0-23) at which each company is checked (default: 10)',
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
        target_hour = options.get('hour', 10)
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

            # Evaluate at the company's own local hour, once per local day.
            slot = due_local_slot(campaign.company, target_hour)
            if slot is None:
                skipped_count += 1
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
                        log_row = claim_dispatch(
                            user=campaign.company.owner,
                            notification_type=NotificationType.CAMPAIGN_LOW_PERFORMANCE,
                            obj=campaign,
                            scheduled_for=slot,
                            dedupe_key="campaign_low",
                        )
                        if log_row is None:
                            # Already alerted today. Note: no `continue` here — the budget
                            # check below is independent and must still run.
                            skipped_count += 1
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
                                mark_dispatched(log_row, push_sent=True)
                                sent_count += 1
                            except Exception as e:
                                logger.error(f"Error sending low performance notification for campaign {campaign.id}: {e}")
                                mark_dispatched(log_row, error=str(e))
                                skipped_count += 1

            # Check budget alert
            if check_budget and campaign.budget and hasattr(campaign, 'spent'):
                remaining = campaign.budget - (campaign.spent or 0)
                remaining_percent = (remaining / campaign.budget) * 100 if campaign.budget > 0 else 0

                if remaining_percent < budget_threshold:
                    # Alert once per crossed bucket so a depleting campaign escalates
                    # (20% -> 10% -> 5%) instead of repeating the same alert daily.
                    bucket = next(
                        (b for b in (5, 10, 20) if remaining_percent < b),
                        budget_threshold,
                    )
                    if dry_run:
                        self.stdout.write(
                            self.style.WARNING(
                                f'[DRY RUN] Would send budget alert for campaign {campaign.id} ({campaign.name}) - {remaining_percent:.1f}% remaining'
                            )
                        )
                    else:
                        log_row = claim_dispatch(
                            user=campaign.company.owner,
                            notification_type=NotificationType.CAMPAIGN_BUDGET_ALERT,
                            obj=campaign,
                            # Bucket-only key: the same bucket never re-alerts, at any time.
                            scheduled_for=campaign.created_at,
                            dedupe_key=f"budget_{bucket}",
                        )
                        if log_row is None:
                            # This bucket was already alerted for this campaign.
                            skipped_count += 1
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
                                mark_dispatched(log_row, push_sent=True)
                                sent_count += 1
                            except Exception as e:
                                logger.error(f"Error sending budget alert for campaign {campaign.id}: {e}")
                                mark_dispatched(log_row, error=str(e))
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
