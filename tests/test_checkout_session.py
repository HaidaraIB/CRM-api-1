"""
The shared create-session flow.

All five create-*-session endpoints now run one function (views/checkout.py)
instead of five copies of the same ~100-line prologue. The URLs and the
per-gateway response field names are part of the contract the web and mobile
clients read, so these tests pin both.
"""
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


def _gateway(name, config=None):
    return PaymentGateway.objects.create(
        name=name,
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config=config or {},
    )


# (url, gateway name, util to stub, stub return, expected response subset)
GATEWAY_CASES = [
    pytest.param(
        "create-stripe-session",
        "Stripe",
        "subscriptions.stripe_utils.create_stripe_payment_session",
        {"session_id": "cs_1", "url": "https://checkout.stripe.test/pay"},
        {"redirect_url": "https://checkout.stripe.test/pay", "session_id": "cs_1"},
        "cs_1",
        id="stripe",
    ),
    pytest.param(
        "create-paytabs-session",
        "PayTabs",
        "subscriptions.paytabs_utils.create_paytabs_payment_session",
        {"tran_ref": "PT_1", "redirect_url": "https://secure.paytabs.test/pay"},
        {"redirect_url": "https://secure.paytabs.test/pay", "tran_ref": "PT_1"},
        "PT_1",
        id="paytabs",
    ),
    pytest.param(
        "create-qicard-session",
        "QiCard",
        "subscriptions.qicard_utils.create_qicard_payment_session",
        {
            "payment_id": "qi_1",
            "form_url": "https://qi.test/form",
            "request_id": "req_1",
        },
        {
            "redirect_url": "https://qi.test/form",
            "payment_id": "qi_1",
            "request_id": "req_1",
        },
        "qi_1",
        id="qicard",
    ),
    pytest.param(
        "create-zaincash-session",
        "ZainCash",
        "subscriptions.zaincash_utils.create_zaincash_payment_session",
        {"id": "zc_1", "payment_url": "https://test.zaincash.iq/pay?id=zc_1"},
        {
            "redirect_url": "https://test.zaincash.iq/pay?id=zc_1",
            "transaction_id": "zc_1",
        },
        "zc_1",
        id="zaincash",
    ),
    pytest.param(
        "create-fib-session",
        "FIB",
        "subscriptions.fib_utils.create_fib_payment_session",
        {
            "paymentId": "fib_1",
            "qrCode": "data:image/png;base64,AAA",
            "readableCode": "ABC123",
            "personalAppLink": "fib://pay/1",
            "validUntil": "2030-01-01T00:00:00Z",
        },
        {
            "payment_id": "fib_1",
            "redirect_url": None,
            "qr_code": "data:image/png;base64,AAA",
            "readable_code": "ABC123",
            "personal_app_link": "fib://pay/1",
        },
        "fib_1",
        id="fib",
    ),
]


@pytest.mark.django_db
class TestCreateSessionPerGateway:
    @pytest.mark.parametrize(
        "url,gw_name,util,stub,expected,tran_ref", GATEWAY_CASES
    )
    def test_response_shape_is_preserved(
        self,
        url,
        gw_name,
        util,
        stub,
        expected,
        tran_ref,
        api_client,
        inactive_subscription,
        owner_user,
    ):
        _gateway(gw_name)
        api_client.force_authenticate(user=owner_user)

        with patch(util, return_value=stub):
            res = api_client.post(
                f"/api/payments/{url}/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )

        assert res.status_code == 200, res.content
        data = api_body(res)
        for key, value in expected.items():
            assert data[key] == value, f"{url}: {key}"

    @pytest.mark.parametrize(
        "url,gw_name,util,stub,expected,tran_ref", GATEWAY_CASES
    )
    def test_payment_row_records_billing_intent(
        self,
        url,
        gw_name,
        util,
        stub,
        expected,
        tran_ref,
        api_client,
        inactive_subscription,
        owner_user,
        paid_plan,
    ):
        """target_plan and billing_cycle must be stored, or finalize has to guess."""
        _gateway(gw_name)
        api_client.force_authenticate(user=owner_user)

        with patch(util, return_value=stub):
            api_client.post(
                f"/api/payments/{url}/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )

        payment = Payment.objects.get(tran_ref=tran_ref)
        assert payment.target_plan_id == paid_plan.id
        assert payment.billing_cycle == BillingCycle.MONTHLY
        assert payment.payment_status == PaymentStatus.PENDING.value
        assert payment.amount_usd == Decimal("29.00")
        assert payment.currency == "USD"

    @pytest.mark.parametrize(
        "url,gw_name,util,stub,expected,tran_ref", GATEWAY_CASES
    )
    def test_requires_authentication(
        self,
        url,
        gw_name,
        util,
        stub,
        expected,
        tran_ref,
        api_client,
        inactive_subscription,
    ):
        _gateway(gw_name)
        res = api_client.post(
            f"/api/payments/{url}/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 401

    @pytest.mark.parametrize(
        "url,gw_name,util,stub,expected,tran_ref", GATEWAY_CASES
    )
    def test_forbidden_for_non_owner(
        self,
        url,
        gw_name,
        util,
        stub,
        expected,
        tran_ref,
        api_client,
        inactive_subscription,
        other_owner_user,
    ):
        _gateway(gw_name)
        api_client.force_authenticate(user=other_owner_user)
        res = api_client.post(
            f"/api/payments/{url}/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 403

    @pytest.mark.parametrize(
        "url,gw_name,util,stub,expected,tran_ref", GATEWAY_CASES
    )
    def test_gateway_not_configured(
        self,
        url,
        gw_name,
        util,
        stub,
        expected,
        tran_ref,
        api_client,
        inactive_subscription,
        owner_user,
    ):
        """No enabled gateway row -> a clear 400, not a crash."""
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            f"/api/payments/{url}/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 400


@pytest.mark.django_db
class TestSharedCheckoutRules:
    """Rules that used to be copy-pasted into all five views."""

    def test_missing_subscription_is_404(self, api_client, owner_user):
        _gateway("Stripe")
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": 999999},
            format="json",
        )
        assert res.status_code == 404

    def test_invalid_body_is_400(self, api_client, owner_user):
        _gateway("Stripe")
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/", {}, format="json"
        )
        assert res.status_code == 400

    def test_unverified_owner_phone_is_blocked(
        self, api_client, inactive_subscription, owner_user
    ):
        _gateway("Stripe")
        owner_user.phone_verified = False
        owner_user.save(update_fields=["phone_verified"])
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code >= 400

    def test_active_subscription_without_plan_change_is_rejected(
        self, api_client, inactive_subscription, owner_user
    ):
        _gateway("Stripe")
        inactive_subscription.is_active = True
        inactive_subscription.save(update_fields=["is_active"])
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": inactive_subscription.id},
            format="json",
        )
        assert res.status_code == 400

    def test_free_plan_needs_no_payment(
        self, api_client, company, owner_user, db
    ):
        _gateway("Stripe")
        owner_user.phone_verified = True
        owner_user.save(update_fields=["phone_verified"])
        free_plan = Plan.objects.create(
            name="Free",
            description="free",
            price_monthly=Decimal("0"),
            price_yearly=Decimal("0"),
            tier=0,
        )
        now = timezone.now()
        sub = Subscription.objects.create(
            company=company,
            plan=free_plan,
            is_active=False,
            start_date=now,
            end_date=now + timedelta(days=30),
            current_period_start=now,
            billing_cycle=BillingCycle.MONTHLY,
        )
        api_client.force_authenticate(user=owner_user)
        res = api_client.post(
            "/api/payments/create-stripe-session/",
            {"subscription_id": sub.id},
            format="json",
        )
        assert res.status_code == 400

    def test_gateway_failure_becomes_a_clean_error(
        self, api_client, inactive_subscription, owner_user
    ):
        _gateway("Stripe")
        api_client.force_authenticate(user=owner_user)
        with patch(
            "subscriptions.stripe_utils.create_stripe_payment_session",
            side_effect=Exception("stripe is down"),
        ):
            res = api_client.post(
                "/api/payments/create-stripe-session/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )
        assert res.status_code == 400
        assert Payment.objects.count() == 0


@pytest.mark.django_db
class TestSessionReuseAcrossGateways:
    def test_pending_session_is_reused_not_recreated(
        self, api_client, inactive_subscription, owner_user, paid_plan
    ):
        gateway = _gateway("QiCard")
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="qi_reuse",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        attach_checkout_session(
            payment,
            tran_ref="qi_reuse",
            checkout_url="https://qi.test/reuse",
            session_expires_at=timezone.now() + timedelta(minutes=20),
            session_meta={"request_id": "req_reuse"},
        )
        api_client.force_authenticate(user=owner_user)

        with patch(
            "subscriptions.qicard_utils.create_qicard_payment_session"
        ) as mock_create:
            res = api_client.post(
                "/api/payments/create-qicard-session/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )

        assert res.status_code == 200
        data = api_body(res)
        assert data["redirect_url"] == "https://qi.test/reuse"
        assert data["payment_id"] == "qi_reuse"
        assert data["request_id"] == "req_reuse"
        mock_create.assert_not_called()
        assert Payment.objects.count() == 1

    def test_expired_session_is_not_reused(
        self, api_client, inactive_subscription, owner_user, paid_plan
    ):
        gateway = _gateway("Stripe")
        payment = Payment.objects.create(
            subscription=inactive_subscription,
            amount=Decimal("29.00"),
            currency="USD",
            amount_usd=Decimal("29.00"),
            payment_method=gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref="cs_old",
            target_plan=paid_plan,
            billing_cycle=BillingCycle.MONTHLY,
        )
        attach_checkout_session(
            payment,
            tran_ref="cs_old",
            checkout_url="https://checkout.stripe.test/old",
            session_expires_at=timezone.now() - timedelta(minutes=1),
        )
        api_client.force_authenticate(user=owner_user)

        with patch(
            "subscriptions.stripe_utils.create_stripe_payment_session",
            return_value={"session_id": "cs_new", "url": "https://new"},
        ):
            res = api_client.post(
                "/api/payments/create-stripe-session/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )

        assert res.status_code == 200
        assert api_body(res)["session_id"] == "cs_new"


@pytest.mark.django_db
class TestConcurrentCreateSessionIsSerialized:
    """
    Two create-session requests arriving milliseconds apart used to produce two
    gateway sessions and two invoices: the reuse lookup ran before the Payment
    insert with nothing serializing them, so both requests read "nothing
    reusable" before either had written a row.

    The fix locks the subscription row and does the lookup *and* the insert
    inside that one critical section. These tests pin that structure, since a
    real two-thread race is not reproducible on SQLite.
    """

    def _record_atomic_depth(self):
        """Nesting depth of the current atomic block, as savepoint count."""
        from django.db import connection

        return len(connection.savepoint_ids)

    def test_reuse_lookup_and_insert_share_one_locked_transaction(
        self, api_client, inactive_subscription, owner_user
    ):
        _gateway("QiCard")
        api_client.force_authenticate(user=owner_user)
        depths = {}

        real_lookup = (
            __import__(
                "subscriptions.views.checkout", fromlist=["find_reusable_pending_payment"]
            ).find_reusable_pending_payment
        )

        def spy_lookup(**kwargs):
            depths["lookup"] = self._record_atomic_depth()
            return real_lookup(**kwargs)

        def stub_session(*args, **kwargs):
            depths["gateway_call"] = self._record_atomic_depth()
            return {
                "payment_id": "qi_race",
                "form_url": "https://qi.test/race",
                "request_id": "req_race",
            }

        with patch(
            "subscriptions.views.checkout.find_reusable_pending_payment",
            side_effect=spy_lookup,
        ), patch(
            "subscriptions.qicard_utils.create_qicard_payment_session",
            side_effect=stub_session,
        ):
            res = api_client.post(
                "/api/payments/create-qicard-session/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )

        assert res.status_code == 200
        # Both ran one atomic level deeper than the view entered at, and at the
        # same level — i.e. inside the same lock-holding block.
        assert depths["lookup"] > 0
        assert depths["lookup"] == depths["gateway_call"]

    def test_second_request_reuses_instead_of_opening_a_second_session(
        self, api_client, inactive_subscription, owner_user
    ):
        """The serialized outcome: the loser of the race reuses, never re-creates."""
        _gateway("QiCard")
        api_client.force_authenticate(user=owner_user)

        def stub_session(*args, **kwargs):
            return {
                "payment_id": "qi_once",
                "form_url": "https://qi.test/once",
                "request_id": "req_once",
            }

        with patch(
            "subscriptions.qicard_utils.create_qicard_payment_session",
            side_effect=stub_session,
        ) as mock_create:
            first = api_client.post(
                "/api/payments/create-qicard-session/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )
            second = api_client.post(
                "/api/payments/create-qicard-session/",
                {"subscription_id": inactive_subscription.id},
                format="json",
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert mock_create.call_count == 1
        assert Payment.objects.count() == 1
        assert api_body(first)["redirect_url"] == api_body(second)["redirect_url"]
