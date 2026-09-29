"""QiCard refund/cancel payment viewset actions."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from accounts.models import User

from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
)


@pytest.fixture
def super_admin(db):
    return User.objects.create_user(
        username="platform_admin",
        email="admin@loop.test",
        password="testpass123",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def qicard_gateway(db):
    return PaymentGateway.objects.create(
        name="QiCard",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config={"terminalId": "t", "username": "u", "password": "p"},
    )


@pytest.fixture
def stripe_gateway(db):
    return PaymentGateway.objects.create(
        name="Stripe",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config={"secretKey": "sk_test", "publishableKey": "pk_test"},
    )


@pytest.fixture
def paid_plan(db):
    return Plan.objects.create(
        name="Paid",
        description="paid",
        price_monthly=Decimal("29.00"),
        price_yearly=Decimal("290.00"),
        tier=1,
    )


@pytest.fixture
def subscription(company, paid_plan, db):
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=paid_plan,
        is_active=True,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )


def _payment(subscription, gateway, status, tran_ref="qi-pay-1"):
    return Payment.objects.create(
        subscription=subscription,
        amount=Decimal("37700.00"),
        currency="IQD",
        payment_method=gateway,
        payment_status=status,
        tran_ref=tran_ref,
    )


@pytest.mark.django_db
class TestPaymentQicardRefundCancel:
    def test_refund_success(self, api_client, super_admin, subscription, qicard_gateway):
        payment = _payment(
            subscription, qicard_gateway, PaymentStatus.COMPLETED.value
        )
        api_client.force_authenticate(user=super_admin)
        with patch(
            "subscriptions.gateways.qicard.QicardAdapter.refund",
            return_value={
                "status": "SUCCESS",
                "refundId": "ref-1",
                "paymentId": payment.tran_ref,
                "amount": 37700.0,
            },
        ):
            res = api_client.post(f"/api/payments/{payment.id}/refund/", {}, format="json")
        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.REFUNDED.value
        assert payment.session_meta.get("qicard_refund", {}).get("refundId") == "ref-1"

    def test_cancel_success(self, api_client, super_admin, subscription, qicard_gateway):
        payment = _payment(
            subscription, qicard_gateway, PaymentStatus.COMPLETED.value, "qi-pay-2"
        )
        api_client.force_authenticate(user=super_admin)
        with patch(
            "subscriptions.gateways.qicard.QicardAdapter.cancel",
            return_value={
                "status": "SUCCESS",
                "paymentId": payment.tran_ref,
                "canceled": True,
                "amount": 37700.0,
            },
        ):
            res = api_client.post(f"/api/payments/{payment.id}/cancel/", {}, format="json")
        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.CANCELED.value

    def test_refund_gateway_error_unchanged(
        self, api_client, super_admin, subscription, qicard_gateway
    ):
        payment = _payment(
            subscription, qicard_gateway, PaymentStatus.COMPLETED.value, "qi-pay-3"
        )
        api_client.force_authenticate(user=super_admin)
        with patch(
            "subscriptions.gateways.qicard.QicardAdapter.refund",
            return_value={"status": "FAILED"},
        ):
            res = api_client.post(f"/api/payments/{payment.id}/refund/", {}, format="json")
        assert res.status_code == 400
        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.COMPLETED.value

    def test_refund_rejects_pending(
        self, api_client, super_admin, subscription, qicard_gateway
    ):
        payment = _payment(
            subscription, qicard_gateway, PaymentStatus.PENDING.value, "qi-pay-4"
        )
        api_client.force_authenticate(user=super_admin)
        res = api_client.post(f"/api/payments/{payment.id}/refund/", {}, format="json")
        assert res.status_code == 400

    def test_refund_rejects_non_qicard(
        self, api_client, super_admin, subscription, stripe_gateway
    ):
        payment = _payment(
            subscription, stripe_gateway, PaymentStatus.COMPLETED.value, "cs_test"
        )
        api_client.force_authenticate(user=super_admin)
        res = api_client.post(f"/api/payments/{payment.id}/refund/", {}, format="json")
        assert res.status_code == 400
