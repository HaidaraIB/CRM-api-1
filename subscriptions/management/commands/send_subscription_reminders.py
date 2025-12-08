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
from subscriptions.utils import get_smtp_connection
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from accounts.models import User, Role
from settings.models import SMTPSettings
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

    def send_renewal_reminder(self, subscription, language='en', dry_run=False):
        """
        Send renewal reminder email to subscription owner
        """
        try:
            smtp_settings = SMTPSettings.get_settings()
            
            if not smtp_settings.is_active:
                logger.warning("SMTP is not active. Cannot send reminder.")
                return {
                    "success": False,
                    "error": "SMTP is not active",
                }
            
            # Get company owner email
            company = subscription.company
            owner = company.owner
            
            if not owner or not owner.email:
                logger.warning(f"No owner email found for subscription {subscription.id}")
                return {
                    "success": False,
                    "error": "No owner email found",
                }
            
            # Prepare email content
            plan_name = subscription.plan.name
            if language == 'ar' and subscription.plan.name_ar:
                plan_name = subscription.plan.name_ar
            
            end_date_str = subscription.end_date.strftime('%Y-%m-%d %H:%M:%S')
            
            # Create email subject and content
            if language == 'ar':
                subject = f"تذكير: انتهاء الاشتراك في {plan_name}"
                message = f"""
                عزيزي/عزيزتي {owner.first_name or owner.username},
                
                نود أن نذكرك بأن اشتراكك في خطة {plan_name} سينتهي في {end_date_str}.
                
                يرجى تجديد اشتراكك لتجنب انقطاع الخدمة.
                
                يمكنك تجديد اشتراكك من صفحة الملف الشخصي.
                
                شكراً لاستخدامك خدماتنا.
                """
            else:
                subject = f"Reminder: Your {plan_name} subscription is ending soon"
                message = f"""
                Dear {owner.first_name or owner.username},
                
                We would like to remind you that your {plan_name} subscription will end on {end_date_str}.
                
                Please renew your subscription to avoid service interruption.
                
                You can renew your subscription from your profile page.
                
                Thank you for using our services.
                """
            
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'  [DRY RUN] Would send reminder to {owner.email} '
                        f'for subscription {subscription.id}'
                    )
                )
                return {"success": True, "dry_run": True}
            
            # Get SMTP connection
            connection = get_smtp_connection()
            
            # Prepare email
            from_email = (
                f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
                if smtp_settings.from_name
                else smtp_settings.from_email
            )
            
            # Create email message
            email = EmailMultiAlternatives(
                subject=subject,
                body=message,
                from_email=from_email,
                to=[owner.email],
                connection=connection,
            )
            
            # Send email
            email.send()
            
            logger.info(
                f"Renewal reminder sent to {owner.email} for subscription {subscription.id} "
                f"(Company: {company.name}, Plan: {plan_name})"
            )
            
            return {
                "success": True,
                "recipient": owner.email,
            }
            
        except Exception as e:
            logger.error(f"Error sending reminder for subscription {subscription.id}: {str(e)}")
            return {
                "success": False,
                "error": str(e),
            }

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
                    f'  - {company_name} ({plan_name}): '
                    f'Ends in {days_until_end} day(s) on {end_date.strftime("%Y-%m-%d %H:%M:%S")}'
                )
            
            # Determine language (default to 'en', can be extended if User model has language field)
            language = 'en'
            
            result = self.send_renewal_reminder(subscription, language, dry_run)
            
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

