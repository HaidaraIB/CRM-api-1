"""
Management command to end subscriptions that have reached their end_date.

This command should be run periodically (e.g., via cron) to automatically
deactivate subscriptions that have expired.

Usage:
    python manage.py end_expired_subscriptions

For cron, add to crontab:
    # Run every hour
    0 * * * * cd /path/to/project && python manage.py end_expired_subscriptions

    # Or run every 15 minutes for more frequent checks
    */15 * * * * cd /path/to/project && python manage.py end_expired_subscriptions
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from subscriptions.models import Subscription
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'End subscriptions that have reached their end_date'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually updating subscriptions',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output for each subscription',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        
        now = timezone.now()
        
        # Find all active subscriptions that have passed their end_date
        expired_subscriptions = Subscription.objects.filter(
            is_active=True,
            end_date__lte=now
        )
        
        count = expired_subscriptions.count()
        
        if count == 0:
            self.stdout.write(
                self.style.SUCCESS('No expired subscriptions found.')
            )
            return
        
        self.stdout.write(
            f'Found {count} expired subscription(s) to deactivate.'
        )
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made')
            )
        
        deactivated_count = 0
        
        for subscription in expired_subscriptions:
            company_name = subscription.company.name
            plan_name = subscription.plan.name
            end_date = subscription.end_date
            
            if verbose:
                self.stdout.write(
                    f'  - {company_name} ({plan_name}): '
                    f'End date: {end_date.strftime("%Y-%m-%d %H:%M:%S")}'
                )
            
            if not dry_run:
                subscription.is_active = False
                subscription.save(update_fields=['is_active', 'updated_at'])
                logger.info(
                    f'Deactivated subscription for company {company_name} '
                    f'(ID: {subscription.id}) - End date: {end_date}'
                )
            
            deactivated_count += 1
        
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Would deactivate {deactivated_count} subscription(s).'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully deactivated {deactivated_count} subscription(s).'
                )
            )

