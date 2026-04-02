"""
Management command to end subscriptions that have reached their end_date.

Also applies pending_plan (scheduled downgrade / paid→free) at period boundary.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from subscriptions.cache_utils import invalidate_company_subscription_cache
from subscriptions.models import BillingCycle, Subscription, SubscriptionStatus
from subscriptions.services.billing import is_plan_free, period_days_for_cycle
from subscriptions.services.trial_eligibility import is_free_trial_plan, mark_company_free_trial_consumed

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "End subscriptions that have reached their end_date and apply scheduled plan changes"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without actually updating subscriptions",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output for each subscription",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        now = timezone.now()

        expired = Subscription.objects.filter(is_active=True, end_date__lte=now).select_related(
            "plan", "pending_plan", "company"
        )

        count = expired.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No expired subscriptions found."))
            return

        self.stdout.write(f"Found {count} expired subscription(s) to process.")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))

        processed = 0

        for subscription in expired:
            company_name = subscription.company.name
            plan_name = subscription.plan.name
            end_date = subscription.end_date

            if verbose:
                self.stdout.write(
                    f"  - {company_name} ({plan_name}): End date: {end_date}"
                )

            if dry_run:
                processed += 1
                continue

            if subscription.pending_plan_id:
                new_plan = subscription.pending_plan
                subscription.plan = new_plan
                subscription.pending_plan = None
                subscription.pending_billing_cycle = None
                if is_plan_free(new_plan):
                    subscription.current_period_start = now
                    td = int(getattr(new_plan, "trial_days", 0) or 0)
                    consumed = subscription.company.free_trial_consumed
                    if td > 0 and not consumed and is_free_trial_plan(new_plan):
                        subscription.end_date = now + timedelta(days=td)
                        subscription.subscription_status = SubscriptionStatus.TRIALING
                    else:
                        subscription.end_date = now + timedelta(days=365 * 100)
                        subscription.subscription_status = SubscriptionStatus.ACTIVE
                    subscription.is_active = True
                else:
                    # Paid downgrade: new period at lower tier from boundary (next invoice at new rate).
                    bc = (
                        subscription.pending_billing_cycle
                        or subscription.billing_cycle
                        or BillingCycle.MONTHLY
                    )
                    subscription.billing_cycle = bc
                    subscription.current_period_start = now
                    subscription.end_date = now + timedelta(
                        days=period_days_for_cycle(bc)
                    )
                    subscription.subscription_status = SubscriptionStatus.ACTIVE
                    subscription.is_active = True
                subscription.save(
                    update_fields=[
                        "plan",
                        "pending_plan",
                        "pending_billing_cycle",
                        "billing_cycle",
                        "current_period_start",
                        "end_date",
                        "subscription_status",
                        "is_active",
                        "updated_at",
                    ]
                )
                invalidate_company_subscription_cache(subscription.company_id)
                logger.info(
                    "Applied pending plan for subscription %s -> plan %s",
                    subscription.id,
                    new_plan.id,
                )
            else:
                was_trialing = subscription.subscription_status == SubscriptionStatus.TRIALING
                subscription.is_active = False
                subscription.save(update_fields=["is_active", "updated_at"])
                if was_trialing:
                    mark_company_free_trial_consumed(subscription.company_id)
                invalidate_company_subscription_cache(subscription.company_id)
                logger.info(
                    "Deactivated subscription for company %s (ID: %s)",
                    company_name,
                    subscription.id,
                )

            processed += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"Would process {processed} subscription(s).")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Successfully processed {processed} subscription(s).")
            )
