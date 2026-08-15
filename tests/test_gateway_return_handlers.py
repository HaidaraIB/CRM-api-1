"""
Characterization tests for the gateway *_return redirect handlers.

These four handlers (paytabs_return, stripe_return, qicard_return, zaincash_return)
carry ~1,090 lines with no prior coverage, and each hand-rolls its own
find-payment -> mark COMPLETED -> finalize sequence instead of going through
subscriptions.services.payment_completion.confirm_and_finalize_payment.

The tests below pin the observable contract that must survive the refactor:
    approved -> period applied exactly once, redirect carries status=success
    declined -> nothing applied, redirect carries status=failed
    replayed -> period applied exactly once (idempotency via Payment.applied_at)
    amount mismatch -> nothing applied, redirect carries status=failed

Tests marked xfail(strict=True) document defects that the Phase 1 fixes must
close; remove the marker together with the fix.
"""
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


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
def lapsed_subscription(company, paid_plan, owner_user, db):
    """Inactive subscription whose period has already ended.

    Using a lapsed period makes the "initial purchase" branch of
    apply_successful_payment move end_date forward visibly, so a second
    application would be detectable.
    """
    owner_user.phone_verified = True
    owner_user.save(update_fields=["phone_verified"])
    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=paid_plan,
        is_active=False,
        start_date=now - timedelta(days=60),
        end_date=now - timedelta(days=2),
        current_period_start=now - timedelta(days=32),
        billing_cycle=BillingCycle.MONTHLY,
    )


def _gateway(name, config=None):
    return PaymentGateway.objects.create(
        name=name,
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config=config or {},
    )


@pytest.fixture
def paytabs_gateway(db):
    return _gateway("PayTabs", {"profileId": "p", "serverKey": "s"})


@pytest.fixture
def stripe_gateway_row(db):
    return _gateway("Stripe", {"secretKey": "sk_test", "publishableKey": "pk_test"})


@pytest.fixture
def qicard_gateway(db):
    return _gateway("QiCard", {"terminalId": "t", "username": "u", "password": "p"})


@pytest.fixture
def zaincash_gateway(db):
    return _gateway("ZainCash", {"merchantId": "m", "merchantSecret": "s"})


def _pending_payment(subscription, gateway, plan, tran_ref, amount="29.00"):
    return Payment.objects.create(
        subscription=subscription,
        amount=Decimal(amount),
        currency="USD",
        exchange_rate=Decimal("1"),
        amount_usd=Decimal(amount),
        payment_method=gateway,
        payment_status=PaymentStatus.PENDING.value,
        tran_ref=tran_ref,
        target_plan=plan,
        billing_cycle=BillingCycle.MONTHLY,
    )


def location(response):
    return response.get("Location", "")


# --------------------------------------------------------------------------
# gateway stubs
#
# Every path that asks "is this paid?" - return handlers, webhooks, and the
# polling endpoint - now goes through the gateway adapter, which imports from
# the *_utils module at call time. So one patch target per gateway covers them
# all, and these tests assert behavior rather than call path.
# --------------------------------------------------------------------------


@contextmanager
def _patched(*pairs):
    with ExitStack() as stack:
        for target, value in pairs:
            stack.enter_context(patch(target, return_value=value))
        yield


def paytabs_reports(result):
    return _patched(("subscriptions.paytabs_utils.verify_paytabs_payment", result))


def stripe_reports(result):
    return _patched(("subscriptions.stripe_utils.verify_stripe_payment", result))


def qicard_reports(result):
    return _patched(("subscriptions.qicard_utils.verify_qicard_payment", result))


def zaincash_reports(jwt_result, api_status=None):
    """
    Zain Cash decodes a signed JWT on the way in (to recover the transaction id)
    and confirms against the transaction/get API, so both are stubbed.
    """
    if api_status is None:
        api_status = jwt_result.get("status")
    return _patched(
        ("subscriptions.zaincash_utils.verify_zaincash_payment", jwt_result),
        (
            "subscriptions.zaincash_utils.check_zaincash_payment_status",
            {"status": api_status},
        ),
    )


# --------------------------------------------------------------------------
# PayTabs
# --------------------------------------------------------------------------


def _paytabs_result(subscription_id, response_status="A", amount=29.0, currency="USD"):
    return {
        "cart_id": f"SUB-{subscription_id}",
        "cart_amount": amount,
        "cart_currency": currency,
        "tran_ref": "PT_OK",
        "payment_result": {"response_status": response_status},
    }


@pytest.mark.django_db
class TestPaytabsReturn:
    def test_approved_applies_period_once(
        self, api_client, lapsed_subscription, paytabs_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, paytabs_gateway, paid_plan, "PT_OK"
        )
        end_before = lapsed_subscription.end_date

        with paytabs_reports(_paytabs_result(lapsed_subscription.id)):
            res = api_client.get(
                f"/api/payments/paytabs-return/?subscription_id={lapsed_subscription.id}"
            )

        assert res.status_code == 302
        assert "status=success" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.payment_status == PaymentStatus.COMPLETED.value
        assert payment.applied_at is not None
        assert lapsed_subscription.is_active is True
        assert lapsed_subscription.end_date > end_before

    def test_declined_does_not_apply(
        self, api_client, lapsed_subscription, paytabs_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, paytabs_gateway, paid_plan, "PT_DECLINED"
        )
        end_before = lapsed_subscription.end_date

        with paytabs_reports(
            _paytabs_result(lapsed_subscription.id, response_status="D")
        ):
            res = api_client.get(
                f"/api/payments/paytabs-return/?subscription_id={lapsed_subscription.id}"
            )

        assert res.status_code == 302
        assert "status=failed" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is None
        assert lapsed_subscription.is_active is False
        assert lapsed_subscription.end_date == end_before

    def test_replay_is_idempotent(
        self, api_client, lapsed_subscription, paytabs_gateway, paid_plan
    ):
        _pending_payment(lapsed_subscription, paytabs_gateway, paid_plan, "PT_OK")

        with paytabs_reports(_paytabs_result(lapsed_subscription.id)):
            url = (
                f"/api/payments/paytabs-return/"
                f"?subscription_id={lapsed_subscription.id}"
            )
            api_client.get(url)
            lapsed_subscription.refresh_from_db()
            end_after_first = lapsed_subscription.end_date
            api_client.get(url)

        lapsed_subscription.refresh_from_db()
        assert lapsed_subscription.end_date == end_after_first

    def test_amount_mismatch_does_not_apply(
        self, api_client, lapsed_subscription, paytabs_gateway, paid_plan
    ):
        """Gateway approves an amount that does not match the plan price."""
        payment = _pending_payment(
            lapsed_subscription, paytabs_gateway, paid_plan, "PT_OK", amount="1.00"
        )
        end_before = lapsed_subscription.end_date

        with paytabs_reports(_paytabs_result(lapsed_subscription.id, amount=1.0)):
            res = api_client.get(
                f"/api/payments/paytabs-return/?subscription_id={lapsed_subscription.id}"
            )

        assert res.status_code == 302
        assert "status=failed" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is None
        assert lapsed_subscription.end_date == end_before

    def test_completes_the_row_matching_tran_ref(
        self, api_client, lapsed_subscription, paytabs_gateway, paid_plan
    ):
        paid = _pending_payment(
            lapsed_subscription, paytabs_gateway, paid_plan, "PT_PAID"
        )
        abandoned_later = _pending_payment(
            lapsed_subscription, paytabs_gateway, paid_plan, "PT_NEWER"
        )

        result = _paytabs_result(lapsed_subscription.id)
        result["tran_ref"] = "PT_PAID"
        with paytabs_reports(result):
            api_client.get(
                f"/api/payments/paytabs-return/"
                f"?subscription_id={lapsed_subscription.id}&tran_ref=PT_PAID"
            )

        paid.refresh_from_db()
        abandoned_later.refresh_from_db()
        assert paid.payment_status == PaymentStatus.COMPLETED.value
        assert abandoned_later.payment_status == PaymentStatus.PENDING.value
        assert abandoned_later.tran_ref == "PT_NEWER"


# --------------------------------------------------------------------------
# Stripe
# --------------------------------------------------------------------------


def _stripe_result(subscription_id, paid=True, amount=29.0):
    return {
        "subscription_id": str(subscription_id),
        "payment_status": "completed" if paid else "pending",
        "stripe_payment_status": "paid" if paid else "unpaid",
        "amount_total": amount,
    }


@pytest.mark.django_db
class TestStripeReturn:
    def test_paid_applies_period_once(
        self, api_client, lapsed_subscription, stripe_gateway_row, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, stripe_gateway_row, paid_plan, "cs_ok"
        )
        end_before = lapsed_subscription.end_date

        with stripe_reports(_stripe_result(lapsed_subscription.id)):
            res = api_client.get("/api/payments/stripe-return/?session_id=cs_ok")

        assert res.status_code == 302
        assert "status=success" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is not None
        assert lapsed_subscription.is_active is True
        assert lapsed_subscription.end_date > end_before

    def test_unpaid_does_not_apply(
        self, api_client, lapsed_subscription, stripe_gateway_row, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, stripe_gateway_row, paid_plan, "cs_unpaid"
        )
        end_before = lapsed_subscription.end_date

        with stripe_reports(_stripe_result(lapsed_subscription.id, paid=False)):
            res = api_client.get("/api/payments/stripe-return/?session_id=cs_unpaid")

        assert res.status_code == 302
        assert "status=failed" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is None
        assert lapsed_subscription.is_active is False
        assert lapsed_subscription.end_date == end_before

    def test_replay_is_idempotent(
        self, api_client, lapsed_subscription, stripe_gateway_row, paid_plan
    ):
        _pending_payment(lapsed_subscription, stripe_gateway_row, paid_plan, "cs_ok")

        with stripe_reports(_stripe_result(lapsed_subscription.id)):
            api_client.get("/api/payments/stripe-return/?session_id=cs_ok")
            lapsed_subscription.refresh_from_db()
            end_after_first = lapsed_subscription.end_date
            api_client.get("/api/payments/stripe-return/?session_id=cs_ok")

        lapsed_subscription.refresh_from_db()
        assert lapsed_subscription.end_date == end_after_first

    def test_missing_session_id_redirects_failed(self, api_client):
        res = api_client.get("/api/payments/stripe-return/")
        assert res.status_code == 302
        assert "status=failed" in location(res)


# --------------------------------------------------------------------------
# QiCard
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestQicardReturn:
    def test_success_applies_period_once(
        self, api_client, lapsed_subscription, qicard_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, qicard_gateway, paid_plan, "qi_ok"
        )
        end_before = lapsed_subscription.end_date

        with qicard_reports({"status": "SUCCESS", "amount": 37700}):
            res = api_client.get(
                f"/api/payments/qicard-return/"
                f"?paymentId=qi_ok&subscription_id={lapsed_subscription.id}"
            )

        assert res.status_code == 302
        assert "status=success" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is not None
        assert lapsed_subscription.is_active is True
        assert lapsed_subscription.end_date > end_before

    def test_failed_marks_payment_failed(
        self, api_client, lapsed_subscription, qicard_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, qicard_gateway, paid_plan, "qi_bad"
        )

        with qicard_reports({"status": "FAILED"}):
            res = api_client.get(
                f"/api/payments/qicard-return/"
                f"?paymentId=qi_bad&subscription_id={lapsed_subscription.id}"
            )

        assert res.status_code == 302
        assert "status=failed" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.payment_status == PaymentStatus.FAILED.value
        assert payment.applied_at is None
        assert lapsed_subscription.is_active is False

    def test_replay_is_idempotent(
        self, api_client, lapsed_subscription, qicard_gateway, paid_plan
    ):
        _pending_payment(lapsed_subscription, qicard_gateway, paid_plan, "qi_ok")
        url = (
            f"/api/payments/qicard-return/"
            f"?paymentId=qi_ok&subscription_id={lapsed_subscription.id}"
        )

        with qicard_reports({"status": "SUCCESS", "amount": 37700}):
            api_client.get(url)
            lapsed_subscription.refresh_from_db()
            end_after_first = lapsed_subscription.end_date
            api_client.get(url)

        lapsed_subscription.refresh_from_db()
        assert lapsed_subscription.end_date == end_after_first

    def test_missing_payment_id_redirects_failed(self, api_client):
        res = api_client.get("/api/payments/qicard-return/")
        assert res.status_code == 302
        assert "status=failed" in location(res)

    def test_unknown_reference_does_not_fabricate_payment(
        self, api_client, lapsed_subscription, qicard_gateway, paid_plan
    ):
        with qicard_reports({"status": "SUCCESS", "amount": 37700}):
            res = api_client.get(
                f"/api/payments/qicard-return/"
                f"?paymentId=qi_unknown&subscription_id={lapsed_subscription.id}"
            )

        assert res.status_code == 302
        assert "status=success" not in location(res)
        assert Payment.objects.filter(subscription=lapsed_subscription).count() == 0


# --------------------------------------------------------------------------
# ZainCash
# --------------------------------------------------------------------------


def _zaincash_result(subscription_id, status_value="success", amount=37700):
    """ZainCash returns a JWT the view decodes; amount is IQD (rate default 1300)."""
    return {
        "status": status_value,
        "orderid": f"SUB-{subscription_id}",
        "amount": amount,
        "id": "zc_txn_1",
    }


@pytest.mark.django_db
class TestZaincashReturn:
    def test_success_applies_period_once(
        self, api_client, lapsed_subscription, zaincash_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, zaincash_gateway, paid_plan, "zc_txn_1"
        )
        end_before = lapsed_subscription.end_date

        with zaincash_reports(_zaincash_result(lapsed_subscription.id)):
            res = api_client.get("/api/payments/zaincash-return/?token=a.b.c")

        assert res.status_code == 302
        assert "status=success" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is not None
        assert lapsed_subscription.is_active is True
        assert lapsed_subscription.end_date > end_before

    def test_failed_does_not_apply(
        self, api_client, lapsed_subscription, zaincash_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, zaincash_gateway, paid_plan, "zc_txn_1"
        )
        end_before = lapsed_subscription.end_date

        with zaincash_reports(
            _zaincash_result(lapsed_subscription.id, status_value="failed")
        ):
            res = api_client.get("/api/payments/zaincash-return/?token=a.b.c")

        assert res.status_code == 302
        assert "status=failed" in location(res)
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is None
        assert lapsed_subscription.is_active is False
        assert lapsed_subscription.end_date == end_before

    def test_replay_is_idempotent(
        self, api_client, lapsed_subscription, zaincash_gateway, paid_plan
    ):
        _pending_payment(lapsed_subscription, zaincash_gateway, paid_plan, "zc_txn_1")

        with zaincash_reports(_zaincash_result(lapsed_subscription.id)):
            api_client.get("/api/payments/zaincash-return/?token=a.b.c")
            lapsed_subscription.refresh_from_db()
            end_after_first = lapsed_subscription.end_date
            api_client.get("/api/payments/zaincash-return/?token=a.b.c")

        lapsed_subscription.refresh_from_db()
        assert lapsed_subscription.end_date == end_after_first

    def test_missing_token_redirects_failed(self, api_client):
        res = api_client.get("/api/payments/zaincash-return/")
        assert res.status_code == 302
        assert "status=failed" in location(res)

    def test_tran_ref_stays_a_transaction_id(
        self, api_client, lapsed_subscription, zaincash_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, zaincash_gateway, paid_plan, "zc_txn_1"
        )

        with zaincash_reports(_zaincash_result(lapsed_subscription.id)):
            api_client.get("/api/payments/zaincash-return/?token=a.b.c")

        payment.refresh_from_db()
        assert payment.tran_ref == "zc_txn_1"

    def test_pending_status_does_not_apply(
        self, api_client, lapsed_subscription, zaincash_gateway, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, zaincash_gateway, paid_plan, "zc_txn_1"
        )

        with zaincash_reports(
            _zaincash_result(lapsed_subscription.id, status_value="pending")
        ):
            api_client.get("/api/payments/zaincash-return/?token=a.b.c")

        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is None
        assert lapsed_subscription.is_active is False


# --------------------------------------------------------------------------
# Polling endpoint (check_payment_status) — gateway coverage
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckPaymentStatusGatewayCoverage:
    def test_stripe_paid_is_reconciled_by_polling(
        self, api_client, lapsed_subscription, stripe_gateway_row, paid_plan, owner_user
    ):
        payment = _pending_payment(
            lapsed_subscription, stripe_gateway_row, paid_plan, "cs_poll"
        )
        api_client.force_authenticate(user=owner_user)

        with stripe_reports(
            {"stripe_payment_status": "paid", "payment_status": "completed"}
        ):
            res = api_client.get(f"/api/payment-status/{lapsed_subscription.id}/")

        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.applied_at is not None

    def test_qicard_paid_is_reconciled_by_polling(
        self, api_client, lapsed_subscription, qicard_gateway, paid_plan, owner_user
    ):
        payment = _pending_payment(
            lapsed_subscription, qicard_gateway, paid_plan, "qi_poll"
        )
        api_client.force_authenticate(user=owner_user)

        with qicard_reports({"status": "SUCCESS"}):
            res = api_client.get(f"/api/payment-status/{lapsed_subscription.id}/")

        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.applied_at is not None
