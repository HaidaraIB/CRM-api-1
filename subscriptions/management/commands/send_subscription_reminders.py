"""
Management command to send email reminders to users 3 days before their subscription ends.

This command should be run daily (e.g., via cron) to send renewal reminders.

Usage:
    python manage.py send_subscription_reminders

For cron, add to crontab:
    # Run daily at 9 AM
    0 9 * * * cd /path/to/project && python manage.py send_subscription_reminders
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from subscriptions.models import Subscription
from accounts.event_emails import send_subscription_expiring_email
from accounts.utils import get_email_language_for_user
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send email reminders to users 3 days before subscription ends'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually sending emails',
        )
        parser.add_argument(
            '--days-before',
            type=int,
            default=3,
            help='Number of days before end_date to send reminder (default: 3)',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output for each subscription',
        )

    def send_renewal_reminder(self, subscription, days_remaining, dry_run=False):
        """
        Send renewal reminder email to subscription owner in their preferred language.
        """
        try:
            company = subscription.company
            owner = company.owner

            if not owner or not owner.email:
                logger.warning("No owner email found for subscription %s", subscription.id)
                return {"success": False, "error": "No owner email found"}

            language = get_email_language_for_user(owner, request=None, default="en")

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        "  [DRY RUN] Would send reminder to %s for subscription %s (language=%s)"
                        % (owner.email, subscription.id, language)
                    )
                )
                return {"success": True, "dry_run": True}

            send_subscription_expiring_email(
                owner, subscription, days_remaining, language=language
            )
            plan_name = getattr(subscription.plan, "name", "")
            logger.info(
                "Renewal reminder sent to %s for subscription %s (Company: %s, Plan: %s)",
                owner.email, subscription.id, company.name, plan_name,
            )
            return {"success": True, "recipient": owner.email}
        except Exception as e:
            logger.error("Error sending reminder for subscription %s: %s", subscription.id, e)
            return {"success": False, "error": str(e)}

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days_before = options['days_before']
        verbose = options['verbose']
        
        now = timezone.now()
        target_date = now + timedelta(days=days_before)
        
        # Find all active subscriptions ending in 'days_before' days
        # We'll check subscriptions where end_date is between now and target_date
        # This ensures we catch subscriptions ending exactly in 'days_before' days
        subscriptions_to_remind = Subscription.objects.filter(
            is_active=True,
            end_date__gte=now,
            end_date__lte=target_date + timedelta(days=1)  # Include the target day
        ).select_related('plan', 'company', 'company__owner')
        
        count = subscriptions_to_remind.count()
        
        if count == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'No subscriptions ending in {days_before} day(s) found.'
                )
            )
            return
        
        self.stdout.write(
            f'Found {count} subscription(s) ending in {days_before} day(s).'
        )
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No emails will be sent')
            )
        
        sent_count = 0
        failed_count = 0
        
        for subscription in subscriptions_to_remind:
            company_name = subscription.company.name
            plan_name = subscription.plan.name
            end_date = subscription.end_date
            days_until_end = (end_date - now).days

            if verbose:
                self.stdout.write(
                    "  - %s (%s): Ends in %s day(s) on %s"
                    % (company_name, plan_name, days_until_end, end_date.strftime("%Y-%m-%d %H:%M:%S"))
                )

            result = self.send_renewal_reminder(subscription, days_until_end, dry_run)
            
            if result.get('success'):
                sent_count += 1
            else:
                failed_count += 1
                if verbose:
                    self.stdout.write(
                        self.style.ERROR(
                            f'    Failed to send: {result.get("error", "Unknown error")}'
                        )
                    )
        
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Would send {sent_count} reminder email(s).'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully sent {sent_count} reminder email(s).'
                )
            )
            if failed_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'Failed to send {failed_count} reminder email(s).'
                    )
                )

