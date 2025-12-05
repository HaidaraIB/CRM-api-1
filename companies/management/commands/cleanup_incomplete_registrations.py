"""
Management command to cleanup incomplete registrations.

This command deletes companies, users, and subscriptions that were created
but never completed payment within the specified time period (default: 48 hours).

Usage:
    python manage.py cleanup_incomplete_registrations
    python manage.py cleanup_incomplete_registrations --hours 24
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from companies.models import Company
from accounts.models import User
from subscriptions.models import Subscription, Payment
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Cleanup incomplete registrations that never completed payment'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=48,
            help='Number of hours after which incomplete registrations should be deleted (default: 48)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        
        cutoff_time = timezone.now() - timedelta(hours=hours)
        
        self.stdout.write(
            self.style.WARNING(
                f'Looking for incomplete registrations older than {hours} hours (before {cutoff_time})'
            )
        )
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be deleted'))
        
        # Find companies with inactive subscriptions that have no completed payments
        incomplete_companies = Company.objects.filter(
            created_at__lt=cutoff_time,
            subscriptions__is_active=False,
        ).distinct()
        
        deleted_count = 0
        companies_to_delete = []
        
        for company in incomplete_companies:
            # Check if company has any completed payments
            has_completed_payment = Payment.objects.filter(
                subscription__company=company,
                payment_status='completed'
            ).exists()
            
            # Check if company has any active subscriptions
            has_active_subscription = company.subscriptions.filter(is_active=True).exists()
            
            # Only delete if no completed payments and no active subscriptions
            if not has_completed_payment and not has_active_subscription:
                companies_to_delete.append(company)
        
        if not companies_to_delete:
            self.stdout.write(self.style.SUCCESS('No incomplete registrations found to cleanup.'))
            return
        
        self.stdout.write(
            self.style.WARNING(f'Found {len(companies_to_delete)} incomplete registration(s) to cleanup:')
        )
        
        for company in companies_to_delete:
            owner = company.owner
            subscriptions_count = company.subscriptions.count()
            payments_count = Payment.objects.filter(subscription__company=company).count()
            
            self.stdout.write(
                f'  - Company: {company.name} (domain: {company.domain})'
            )
            self.stdout.write(
                f'    Owner: {owner.username} ({owner.email})'
            )
            self.stdout.write(
                f'    Created: {company.created_at}'
            )
            self.stdout.write(
                f'    Subscriptions: {subscriptions_count}, Payments: {payments_count}'
            )
            
            if not dry_run:
                # Delete subscriptions first (they have foreign keys)
                company.subscriptions.all().delete()
                
                # Delete payments related to this company
                Payment.objects.filter(subscription__company=company).delete()
                
                # Delete the owner user
                owner.delete()
                
                # Delete the company
                company.delete()
                
                deleted_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'    ✓ Deleted company {company.name} and related data')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'    [DRY RUN] Would delete company {company.name}')
                )
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'\n[DRY RUN] Would delete {len(companies_to_delete)} incomplete registration(s)'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSuccessfully deleted {deleted_count} incomplete registration(s)'
                )
            )

