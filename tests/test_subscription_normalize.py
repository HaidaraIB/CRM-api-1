"""
Idempotent payment apply / reconcile (Payment.applied_at).
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from conftest import api_body


@pytest.mark.django_db
class TestReconcileUnappliedCompletedPayment:
    def _gw(self):
        from subscriptions.models import PaymentGateway, PaymentGatewayStatus

        return PaymentGateway.objects.create(
            name="Stripe Test Reconcile",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )

    def test_first_unapplied_payment_extends_stale_trial(self, company, plan):
        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            reconcile_unapplied_completed_payment,
        )

        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=3),
            billing_cycle=BillingCycle.MONTHLY,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=self._gw(),
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=None,
            tran_ref="cs_first_unapplied",
        )

        assert reconcile_unapplied_completed_payment(sub) is True
        sub.refresh_from_db()
        assert (sub.end_date - now).days >= 28
        payment = sub.payments.get(tran_ref="cs_first_unapplied")
        assert payment.applied_at is not None

    def test_renewal_unapplied_extends_from_current_end_once(self, company, plan):
        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            reconcile_unapplied_completed_payment,
        )

        now = timezone.now()
        period_end = now + timedelta(days=20)
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=period_end,
            current_period_start=now - timedelta(days=10),
            billing_cycle=BillingCycle.MONTHLY,
        )
        gw = self._gw()
        # Prior payment already applied (backfill semantics)
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=now - timedelta(days=10),
            tran_ref="cs_prior_applied",
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=None,
            tran_ref="cs_renewal_unapplied",
        )

        assert reconcile_unapplied_completed_payment(sub) is True
        sub.refresh_from_db()
        expected = period_end + timedelta(days=30)
        assert abs((sub.end_date - expected).total_seconds()) < 120

        # Second call: no double extend
        assert reconcile_unapplied_completed_payment(sub) is False
        sub.refresh_from_db()
        assert abs((sub.end_date - expected).total_seconds()) < 120

    def test_already_applied_payment_is_noop_even_if_end_looks_short(self, company, plan):
        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            reconcile_unapplied_completed_payment,
        )

        now = timezone.now()
        short_end = now + timedelta(days=3)
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=short_end,
            billing_cycle=BillingCycle.MONTHLY,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=self._gw(),
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=now - timedelta(days=1),
            tran_ref="cs_backfilled",
        )

        assert reconcile_unapplied_completed_payment(sub) is False
        sub.refresh_from_db()
        assert sub.end_date == short_end

    def test_finalize_second_call_does_not_double_extend(self, company, plan):
        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.billing import finalize_completed_payment

        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=3),
            billing_cycle=BillingCycle.MONTHLY,
        )
        payment = Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=self._gw(),
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=None,
            tran_ref="cs_finalize_once",
        )

        finalize_completed_payment(sub, payment, 49.99)
        sub.refresh_from_db()
        payment.refresh_from_db()
        end_after_first = sub.end_date
        assert payment.applied_at is not None
        assert (end_after_first - now).days >= 28

        finalize_completed_payment(sub, payment, 49.99)
        sub.refresh_from_db()
        assert sub.end_date == end_after_first

    def test_normalize_alias_calls_reconcile(self, company, plan):
        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentStatus,
            Subscription,
        )
        from subscriptions.services.subscription_helpers import (
            normalize_paid_subscription_end_date,
        )

        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=3),
            billing_cycle=BillingCycle.MONTHLY,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=self._gw(),
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=None,
            tran_ref="cs_alias",
        )

        assert normalize_paid_subscription_end_date(sub) is True
        sub.refresh_from_db()
        assert (sub.end_date - now).days >= 28

    def test_check_payment_status_reconciles_unapplied(self, company, plan, api_client, owner_user):
        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentStatus,
            Subscription,
        )

        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=3),
            billing_cycle=BillingCycle.MONTHLY,
        )
        Payment.objects.create(
            subscription=sub,
            amount=Decimal("49.99"),
            currency="USD",
            amount_usd=Decimal("49.99"),
            payment_method=self._gw(),
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.MONTHLY,
            target_plan=plan,
            applied_at=None,
            tran_ref="cs_check_reconcile",
        )

        # payment-status requires auth; any company member may poll it.
        api_client.force_authenticate(user=owner_user)
        response = api_client.get(f"/api/v1/payment-status/{sub.id}/")
        assert response.status_code == 200
        data = api_body(response)
        assert data.get("days_until_expiry", 0) >= 28
        sub.refresh_from_db()
        assert (sub.end_date - now).days >= 28
        assert sub.payments.get(tran_ref="cs_check_reconcile").applied_at is not None

    def test_check_payment_status_allows_company_employee(
        self, company, plan, api_client, employee_user
    ):
        from subscriptions.models import BillingCycle, Subscription

        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=30),
            billing_cycle=BillingCycle.MONTHLY,
        )
        api_client.force_authenticate(user=employee_user)
        response = api_client.get(f"/api/v1/payment-status/{sub.id}/")
        assert response.status_code == 200
        data = api_body(response)
        assert data.get("is_truly_active") is True
        assert data.get("subscription_id") == sub.id


@pytest.mark.django_db
class TestRepairSubscriptionPeriodWindows:
    def test_dry_run_and_repair_sliding_end_date(self, company, plan):
        from io import StringIO

        from django.core.management import call_command

        from subscriptions.models import (
            BillingCycle,
            Payment,
            PaymentGateway,
            PaymentGatewayStatus,
            PaymentStatus,
            Subscription,
        )

        now = timezone.now()
        paid_at = now - timedelta(days=90)
        gw = PaymentGateway.objects.create(
            name="Stripe Repair",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=365),
            billing_cycle=BillingCycle.YEARLY,
        )
        Subscription.objects.filter(pk=sub.pk).update(
            start_date=paid_at,
            current_period_start=paid_at,
        )
        payment = Payment.objects.create(
            subscription=sub,
            amount=Decimal("499.99"),
            currency="USD",
            amount_usd=Decimal("499.99"),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            billing_cycle=BillingCycle.YEARLY,
            target_plan=plan,
            applied_at=paid_at,
            tran_ref="cs_repair",
        )
        Payment.objects.filter(pk=payment.pk).update(created_at=paid_at)

        out = StringIO()
        call_command("repair_subscription_period_windows", "--dry-run", stdout=out)
        assert "would repair" in out.getvalue() or "dry-run" in out.getvalue()
        sub.refresh_from_db()
        assert abs((sub.end_date - (now + timedelta(days=365))).total_seconds()) < 120

        call_command("repair_subscription_period_windows", stdout=StringIO())
        sub.refresh_from_db()
        expected = paid_at + timedelta(days=365)
        assert abs((sub.end_date - expected).total_seconds()) < 120
