"""Payment engineering: auth, webhook re-query, session reuse, finalize lock."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from conftest import api_body
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
)
from subscriptions.services.billing import finalize_completed_payment
from subscriptions.services.payment_completion import attach_checkout_session


@pytest.fixture
def paid_plan(db):
    return Plan.objects.create(
        name="Paid Plan",
        description="paid",
        price_monthly=Decimal("29.00"),
        price_yearly=Decimal("290.00"),
        tier=1,
    )


@pytest.fixture
def inactive_subscription(company, paid_plan, owner_user, db):
    owner_user.phone_verified = True
    owner_user.save(update_fields=["phone_verified"])
    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=paid_plan,
        is_active=False,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )


@pytest.fixture
def stripe_gateway(db):
    return PaymentGateway.objects.create(
        name="Stripe",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config={
            "secretKey": "sk_test_dummy",
            "publishableKey": "pk_test_dummy",
            "webhookSecret": "whsec_test_secret",
        },
    )


@pytest.fixture
def qicard_gateway(db):
    return PaymentGateway.objects.create(
        name="QiCard",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config={"terminalId": "t", "username": "u", "password": "p"},
    )


@pytest.mark.django_db
class TestCreateSessionAuth:
    def test_create_stripe_requires_auth(
        self, api_client, inactive_subscription, stripe_gateway
    ):
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 401

    def test_create_stripe_forbidden_for_other_owner(
        self,
        api_client,
        inactive_subscription,
        stripe_gateway,
        other_owner_user,
    ):
        api_client.force_authenticate(user=other_owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 403

    @patch("subscriptions.stripe_utils.create_stripe_payment_session")
    def test_create_stripe_ok_for_owner(
        self,
        mock_create,
        api_client,
        inactive_subscription,
        stripe_gateway,
        owner_user,
    ):
        mock_create.return_value = {
            "session_id": "cs_test_1",
            "url": "https://checkout.stripe.test/pay",
        }
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 200
        data = api_body(res)
        assert data["redirect_url"] == "https://checkout.stripe.test/pay"
        assert data["session_id"] == "cs_test_1"
        payment = Payment.objects.get(tran_ref="cs_test_1")
        assert payment.checkout_url == "https://checkout.stripe.test/pay"
        assert payment.session_expires_at is not None


@pytest.mark.django_db
class TestSessionReuse:
    @patch("subscriptions.stripe_utils.create_stripe_payment_session")
    def test_reuse_pending_stripe_session(
        self,
        mock_create,
        api_client,
        inactive_subscription,
        stripe_gateway,
        owner_user,
        paid_plan,
    ):
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=stripe_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="cs_reuse",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        attach_checkout_session(
            payment,
            tran_ref="cs_reuse",
            checkout_url="https://checkout.stripe.test/reuse",
            session_expires_at=timezone.now() + timedelta(minutes=20),
        )
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 200
        data = api_body(res)
        assert data["session_id"] == "cs_reuse"
        assert data["redirect_url"] == "https://checkout.stripe.test/reuse"
        mock_create.assert_not_called()


@pytest.mark.django_db
class TestWebhookRequery:
    def test_qicard_forged_success_not_applied(
        self, api_client, inactive_subscription, qicard_gateway, paid_plan
    ):
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=qicard_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="qi_pay_1",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        with patch(
            "subscriptions.qicard_utils.verify_qicard_payment",
            return_value={"status": "CREATED"},
        ):
            res = api_client.post(
                "/api/payments/qicard-webhook/",
                {"paymentId": "qi_pay_1", "status": "SUCCESS"},
                format="json",
            )
        assert res.status_code == 200
        payment.refresh_from_db()
        inactive_subscription.refresh_from_db()
        assert payment.payment_status == PaymentStatus.PENDING.value
        assert payment.applied_at is None
        assert inactive_subscription.is_active is False

    def test_qicard_confirmed_success_applies_once(
        self, api_client, inactive_subscription, qicard_gateway, paid_plan
    ):
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=qicard_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="qi_pay_2",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        with patch(
            "subscriptions.qicard_utils.verify_qicard_payment",
            return_value={"status": "SUCCESS"},
        ):
            res1 = api_client.post(
                "/api/payments/qicard-webhook/",
                {"paymentId": "qi_pay_2", "status": "SUCCESS"},
                format="json",
            )
            res2 = api_client.post(
                "/api/payments/qicard-webhook/",
                {"paymentId": "qi_pay_2", "status": "SUCCESS"},
                format="json",
            )
        assert res1.status_code == 200
        assert res2.status_code == 200
        payment.refresh_from_db()
        inactive_subscription.refresh_from_db()
        assert payment.payment_status == PaymentStatus.COMPLETED.value
        assert payment.applied_at is not None
        assert inactive_subscription.is_active is True

    def test_fib_forged_paid_not_applied(
        self, api_client, inactive_subscription, paid_plan, db
    ):
        fib = PaymentGateway.objects.create(
            name="FIB",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
            config={},
        )
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=fib,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="fib_1",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        with patch(
            "subscriptions.fib_utils.check_fib_payment_status",
            return_value={"status": "UNPAID"},
        ):
            res = api_client.post(
                "/api/payments/fib-callback/",
                {"id": "fib_1", "status": "PAID"},
                format="json",
            )
        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.PENDING.value
        assert payment.applied_at is None

    def test_paytabs_callback_requires_query_approval(
        self, api_client, inactive_subscription, paid_plan, db
    ):
        pt = PaymentGateway.objects.create(
            name="PayTabs",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
            config={},
        )
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=pt,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="PT_1",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        with patch(
            "subscriptions.paytabs_utils.verify_paytabs_payment",
            return_value={"payment_result": {"response_status": "D"}},
        ):
            res = api_client.post(
                "/api/payments/paytabs-callback/",
                {"tran_ref": "PT_1"},
                format="json",
            )
        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.applied_at is None

        with patch(
            "subscriptions.paytabs_utils.verify_paytabs_payment",
            return_value={"payment_result": {"response_status": "A"}},
        ):
            res2 = api_client.post(
                "/api/payments/paytabs-callback/",
                {"tran_ref": "PT_1"},
                format="json",
            )
        assert res2.status_code == 200
        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.COMPLETED.value
        assert payment.applied_at is not None


@pytest.mark.django_db
class TestStripeWebhook:
    def test_invalid_signature_rejected(self, api_client, stripe_gateway):
        import stripe

        with patch(
            "stripe.Webhook.construct_event",
            side_effect=stripe.error.SignatureVerificationError("bad", "sig_header"),
        ):
            res = api_client.post(
                "/api/payments/stripe-webhook/",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=x",
            )
        assert res.status_code == 400

    def test_checkout_session_completed_finalizes(
        self,
        api_client,
        inactive_subscription,
        stripe_gateway,
        paid_plan,
    ):
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=stripe_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="cs_wh_1",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_wh_1"}},
        }
        with patch("stripe.Webhook.construct_event", return_value=event), patch(
            "subscriptions.stripe_utils.verify_stripe_payment",
            return_value={
                "stripe_payment_status": "paid",
                "payment_status": "completed",
            },
        ):
            res = api_client.post(
                "/api/payments/stripe-webhook/",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
            )
        assert res.status_code == 200
        payment.refresh_from_db()
        inactive_subscription.refresh_from_db()
        assert payment.applied_at is not None
        assert inactive_subscription.is_active is True


@pytest.mark.django_db
class TestFinalizeLock:
    def test_double_finalize_idempotent(
        self, inactive_subscription, stripe_gateway, paid_plan
    ):
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=stripe_gateway,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="fin_1",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        end_before = inactive_subscription.end_date
        finalize_completed_payment(inactive_subscription, payment, 29.0)
        payment.refresh_from_db()
        inactive_subscription.refresh_from_db()
        assert payment.applied_at is not None
        end_after_first = inactive_subscription.end_date
        finalize_completed_payment(inactive_subscription, payment, 29.0)
        inactive_subscription.refresh_from_db()
        assert inactive_subscription.end_date == end_after_first
        assert end_after_first is not None
        assert end_after_first >= end_before
