"""
One-shot repair for subscription end_dates slid by the old now+period normalize bug.

Detects active paid subscriptions where end_date ≈ today + period while the latest
completed payment (or current_period_start) is older than the sliding tolerance.
Rewrites end_date to a stable anchor + period (never timezone.now()).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from subscriptions.models import Payment, PaymentStatus, Subscription
from subscriptions.services.billing import PERIOD_DAYS_MONTHLY, PERIOD_DAYS_YEARLY, is_plan_free
from subscriptions.services.subscription_helpers import (
    _payment_amount_usd,
    infer_billing_cycle_from_amount_usd,
)

# end_date within this of (now + period) counts as "slid to today"
_SLIDE_MATCH = timedelta(days=2)
# payment / period start must be older than this vs now to count as corruption
_STALE_ANCHOR = timedelta(days=3)


class Command(BaseCommand):
    help = (
        "Repair subscription period windows corrupted by now+period sliding. "
        "Use --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without saving.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        repaired = 0
        scanned = 0

        qs = (
            Subscription.objects.filter(is_active=True)
            .select_related("plan", "company")
            .order_by("id")
        )

        for sub in qs.iterator():
            scanned += 1
            plan = sub.plan
            if is_plan_free(plan):
                continue

            latest = (
                Payment.objects.filter(
                    subscription=sub,
                    payment_status=PaymentStatus.COMPLETED.value,
                )
                .order_by("-created_at")
                .first()
            )
            if not latest:
                continue

            amount = _payment_amount_usd(latest)
            if amount <= 0:
                continue

            billing_cycle = latest.billing_cycle or infer_billing_cycle_from_amount_usd(
                plan, amount
            )
            period_days = (
                PERIOD_DAYS_YEARLY
                if billing_cycle == "yearly"
                else PERIOD_DAYS_MONTHLY
            )
            today_plus_period = now + timedelta(days=period_days)

            if sub.end_date is None:
                continue
            if abs(sub.end_date - today_plus_period) > _SLIDE_MATCH:
                continue

            anchor = sub.current_period_start or latest.created_at
            if anchor is None:
                continue
            if now - anchor < _STALE_ANCHOR:
                # Recently paid; end_date ≈ now+period is expected, not corruption.
                continue

            expected_end = anchor + timedelta(days=period_days)
            if abs(sub.end_date - expected_end) <= _SLIDE_MATCH:
                continue

            company_name = getattr(sub.company, "name", sub.company_id)
            self.stdout.write(
                f"{'[dry-run] ' if dry_run else ''}"
                f"sub={sub.id} company={company_name}: "
                f"{sub.end_date.isoformat()} -> {expected_end.isoformat()} "
                f"(anchor={anchor.isoformat()}, cycle={billing_cycle})"
            )

            if not dry_run:
                update_fields = ["end_date", "updated_at"]
                sub.end_date = expected_end
                if sub.current_period_start is None:
                    sub.current_period_start = anchor
                    update_fields.append("current_period_start")
                sub.save(update_fields=update_fields)
            repaired += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {scanned} active subscriptions; "
                f"{'would repair' if dry_run else 'repaired'} {repaired}."
            )
        )
