"""
Subscription end_date normalization after trial -> paid (completed payment, stale end_date).
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from conftest import api_body


@pytest.mark.django_db
class TestNormalizePaidSubscriptionEndDate:
    def test_first_completed_payment_extends_stale_trial_end_date(self, company, plan):
        from subscriptions.models import (
            Payment,
            PaymentGateway,
            PaymentGatewayStatus,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            normalize_paid_subscription_end_date,
        )

        gw = PaymentGateway.objects.create(
            name="Stripe Test Normalize",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=3),
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="cs_test_normalize",
        )

        assert normalize_paid_subscription_end_date(sub) is True
        sub.refresh_from_db()
        assert (sub.end_date - now).days >= 28

    def test_does_not_shift_future_period_when_prior_payments_exist(self, company, plan):
        from subscriptions.models import (
            Payment,
            PaymentGateway,
            PaymentGatewayStatus,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            normalize_paid_subscription_end_date,
        )

        gw = PaymentGateway.objects.create(
            name="Stripe Test Prior",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        now = timezone.now()
        period_end = now + timedelta(days=20)
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            start_date=now - timedelta(days=10),
            end_date=period_end,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="cs_old",
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="cs_new",
        )

        old_end = sub.end_date
        assert normalize_paid_subscription_end_date(sub) is False
        sub.refresh_from_db()
        assert sub.end_date == old_end

    def test_first_payment_does_not_reset_end_date_to_today_plus_period(self, company, plan):
        """Regression: polling check_payment must not push end_date forward every day."""
        from subscriptions.models import (
            Payment,
            PaymentGateway,
            PaymentGatewayStatus,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            normalize_paid_subscription_end_date,
        )

        gw = PaymentGateway.objects.create(
            name="Stripe Test Sticky End",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        now = timezone.now()
        paid_at = now - timedelta(days=90)
        correct_end = paid_at + timedelta(days=365)
        # Simulate the old bug: end_date was rewritten to "today + 365".
        corrupted_end = now + timedelta(days=365)
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=corrupted_end,
        )
        # auto_now_add ignores create() kwargs; set period start via UPDATE.
        Subscription.objects.filter(pk=sub.pk).update(
            start_date=paid_at,
            current_period_start=paid_at,
        )
        sub.refresh_from_db()
        payment = Payment.objects.create(
            subscription=sub,
            amount=Decimal("499.99"),
            currency="USD",
            amount_usd=Decimal("499.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="cs_sticky_end",
        )
        Payment.objects.filter(pk=payment.pk).update(created_at=paid_at)
        payment.refresh_from_db()
        assert abs((payment.created_at - paid_at).total_seconds()) < 2

        assert normalize_paid_subscription_end_date(sub) is True
        sub.refresh_from_db()
        assert abs((sub.end_date - correct_end).total_seconds()) < 120
        # Second call must be a no-op (idempotent; does not keep sliding forward).
        assert normalize_paid_subscription_end_date(sub) is False
        sub.refresh_from_db()
        assert abs((sub.end_date - correct_end).total_seconds()) < 120

    def test_check_payment_status_updates_end_date_for_paid_plan(self, company, plan, api_client):
        from subscriptions.models import (
            Payment,
            PaymentGateway,
            PaymentGatewayStatus,
            PaymentStatus,
            Subscription,
        )

        gw = PaymentGateway.objects.create(
            name="Stripe Test CheckPayment",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=3),
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="cs_check_payment",
        )

        response = api_client.get(f"/api/v1/payment-status/{sub.id}/")
        assert response.status_code == 200
        data = api_body(response)
        assert data.get("days_until_expiry", 0) >= 28
        sub.refresh_from_db()
        assert (sub.end_date - now).days >= 28
