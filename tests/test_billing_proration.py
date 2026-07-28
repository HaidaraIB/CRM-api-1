"""Tests for subscription billing / proration helpers."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from subscriptions.services.billing import (
    apply_successful_payment,
    compute_upgrade_proration_usd,
    normalize_subscription_billing_window,
    resolve_checkout_pricing,
)


@pytest.mark.django_db
class TestUpgradeProration:
    def test_proration_keeps_period_end(self, company):
        silver = Plan.objects.create(
            name="Silver",
            description="s",
            price_monthly=Decimal("30.00"),
            price_yearly=Decimal("300.00"),
            tier=1,
        )
        gold = Plan.objects.create(
            name="Gold",
            description="g",
            price_monthly=Decimal("50.00"),
            price_yearly=Decimal("500.00"),
            tier=2,
        )
        now = timezone.now()
        period_end = now + timedelta(days=10)
        sub = Subscription.objects.create(
            company=company,
            plan=silver,
            is_active=True,
            start_date=now - timedelta(days=20),
            end_date=period_end,
            current_period_start=now - timedelta(days=20),
            billing_cycle=BillingCycle.MONTHLY,
        )
        gw = PaymentGateway.objects.create(
            name="GW Proration",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("30.00"),
            currency="USD",
            amount_usd=Decimal("30.00"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="old_pay",
        )
        amt, rem, pdays = compute_upgrade_proration_usd(
            sub, silver, gold, BillingCycle.MONTHLY, as_of=now
        )
        assert amt > 0
        assert rem <= 10
        pay = Payment.objects.create(
            subscription=sub,
            amount=float(amt),
            currency="USD",
            amount_usd=amt,
            payment_method=gw,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="up",
            target_plan=gold,
            billing_cycle=BillingCycle.MONTHLY,
        )
        apply_successful_payment(
            sub,
            amount_usd=float(amt),
            target_plan=gold,
            billing_cycle=BillingCycle.MONTHLY,
            exclude_payment_id=pay.id,
        )
        sub.refresh_from_db()
        assert sub.plan_id == gold.id
        assert sub.end_date == period_end


@pytest.mark.django_db
class TestRenewalExtendsPeriod:
    def test_renewal_extends_from_end_date(self, company):
        plan = Plan.objects.create(
            name="Paid",
            description="p",
            price_monthly=Decimal("29.00"),
            price_yearly=Decimal("290.00"),
            tier=1,
        )
        now = timezone.now()
        period_end = now + timedelta(days=5)
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            start_date=now - timedelta(days=25),
            end_date=period_end,
            current_period_start=now - timedelta(days=25),
            billing_cycle=BillingCycle.MONTHLY,
            subscription_status=SubscriptionStatus.ACTIVE,
        )
        gw = PaymentGateway.objects.create(
            name="GW Renew",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="prior",
        )
        _, _, amount, intent = resolve_checkout_pricing(
            sub, target_plan_id=plan.id, billing_cycle_param="monthly"
        )
        assert intent == "renewal"
        assert amount == Decimal("29.00")
        apply_successful_payment(
            sub,
            amount_usd=29.0,
            target_plan=plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        sub.refresh_from_db()
        assert sub.current_period_start == period_end
        assert sub.end_date == period_end + timedelta(days=30)


@pytest.mark.django_db
class TestResolveCheckout:
    def test_downgrade_raises(self, company, plan):
        low = Plan.objects.create(
            name="Low",
            description="l",
            price_monthly=Decimal("10.00"),
            price_yearly=Decimal("100.00"),
            tier=0,
        )
        high = Plan.objects.create(
            name="High",
            description="h",
            price_monthly=Decimal("49.99"),
            price_yearly=Decimal("499.99"),
            tier=5,
        )
        sub = Subscription.objects.create(
            company=company,
            plan=high,
            is_active=True,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=15),
            billing_cycle=BillingCycle.MONTHLY,
        )
        with pytest.raises(ValueError):
            resolve_checkout_pricing(sub, target_plan_id=low.id, billing_cycle_param="monthly")

    def test_same_tier_lateral_raises(self, company):
        a = Plan.objects.create(
            name="A",
            description="a",
            price_monthly=Decimal("20.00"),
            price_yearly=Decimal("200.00"),
            tier=2,
        )
        b = Plan.objects.create(
            name="B",
            description="b",
            price_monthly=Decimal("22.00"),
            price_yearly=Decimal("220.00"),
            tier=2,
        )
        sub = Subscription.objects.create(
            company=company,
            plan=a,
            is_active=True,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=15),
            billing_cycle=BillingCycle.MONTHLY,
        )
        with pytest.raises(ValueError):
            resolve_checkout_pricing(sub, target_plan_id=b.id, billing_cycle_param="monthly")


@pytest.mark.django_db
class TestPendingDowngradeAtPeriodEnd:
    def test_end_expired_applies_pending_plan(self, company):
        high = Plan.objects.create(
            name="High",
            description="h",
            price_monthly=Decimal("50.00"),
            price_yearly=Decimal("500.00"),
            tier=3,
        )
        low = Plan.objects.create(
            name="Low",
            description="l",
            price_monthly=Decimal("20.00"),
            price_yearly=Decimal("200.00"),
            tier=1,
        )
        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=high,
            pending_plan=low,
            pending_billing_cycle=BillingCycle.MONTHLY,
            is_active=True,
            start_date=now - timedelta(days=40),
            end_date=now - timedelta(minutes=1),
            current_period_start=now - timedelta(days=40),
            billing_cycle=BillingCycle.MONTHLY,
            subscription_status=SubscriptionStatus.ACTIVE,
        )
        call_command("end_expired_subscriptions")
        sub.refresh_from_db()
        assert sub.plan_id == low.id
        assert sub.pending_plan_id is None
        assert sub.is_active is True


@pytest.mark.django_db
class TestNormalizeBillingWindow:
    def test_fills_missing_current_period_start(self, company):
        now = timezone.now()
        end = now + timedelta(days=10)
        sub = Subscription.objects.create(
            company=company,
            plan=Plan.objects.create(
                name="P",
                description="d",
                price_monthly=Decimal("10"),
                price_yearly=Decimal("100"),
                tier=1,
            ),
            is_active=True,
            start_date=now - timedelta(days=5),
            end_date=end,
            current_period_start=None,
            billing_cycle=BillingCycle.MONTHLY,
        )
        fields = normalize_subscription_billing_window(sub)
        assert "current_period_start" in fields
        assert sub.current_period_start < sub.end_date
