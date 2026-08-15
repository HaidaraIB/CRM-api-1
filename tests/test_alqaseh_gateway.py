"""
Al Qaseh gateway.

Endpoints and field names follow Al Qaseh's OpenAPI 3.0.1 document; the HTTP
layer is stubbed so these run offline. What they pin: the nine-state mapping,
the required create-payment body, IQD conversion, the three-identifier dance
(token for the hosted page, payment_id for the status API, order_id for the
webhook), and that neither a forged redirect nor a forged webhook can apply a
period without the server-side re-query agreeing.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from conftest import api_body
from subscriptions.gateways import get_adapter, adapter_for_name
from subscriptions.gateways.base import CheckoutContext, GatewayError
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
)

ADAPTER_REQUEST = "subscriptions.gateways.alqaseh.AlqasehAdapter._request"


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
def alqaseh_gateway(db):
    return PaymentGateway.objects.create(
        name="Al Qaseh",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config={
            "clientId": "public_test",
            "clientSecret": "test_secret_value",
            "terminalId": "T-1",
            "environment": "test",
        },
    )


@pytest.fixture
def lapsed_subscription(company, paid_plan, owner_user, db):
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


def _pending_payment(subscription, gateway, plan, tran_ref="aq_pay_1", order_id="SUB-1-abc"):
    payment = Payment.objects.create(
        subscription=subscription,
        amount=Decimal("29.00"),
        currency="USD",
        exchange_rate=Decimal("1"),
        amount_usd=Decimal("29.00"),
        payment_method=gateway,
        payment_status=PaymentStatus.PENDING.value,
        tran_ref=tran_ref,
        target_plan=plan,
        billing_cycle=BillingCycle.MONTHLY,
    )
    payment.session_meta = {"order_id": order_id, "token": "tok_1"}
    payment.save(update_fields=["session_meta"])
    return payment


class TestRegistration:
    def test_adapter_is_registered(self):
        assert get_adapter("alqaseh") is not None

    @pytest.mark.parametrize(
        "name", ["Al Qaseh", "AlQaseh", "alqaseh", "al-qaseh", "Qaseh", "QASEH IQ"]
    )
    def test_operator_names_resolve(self, name):
        assert adapter_for_name(name).slug == "alqaseh"

    def test_does_not_steal_other_gateways(self):
        for name, slug in [
            ("Stripe", "stripe"),
            ("QiCard", "qicard"),
            ("ZainCash", "zaincash"),
            ("FIB", "fib"),
            ("PayTabs", "paytabs"),
        ]:
            assert adapter_for_name(name).slug == slug


class TestStatusMapping:
    """The nine documented Al Qaseh states, mapped to our shared vocabulary."""

    @pytest.mark.parametrize(
        "api_status,expected",
        [
            ("succeeded", "paid"),
            ("failed", "failed"),
            ("declined", "failed"),
            ("revoked", "failed"),
            ("expired", "failed"),
            ("prepared", "pending"),
            ("retried", "pending"),
            # Indeterminate: never acted on, so the payment stays PENDING.
            ("unknown", "unknown"),
            ("duplicated", "unknown"),
        ],
    )
    def test_documented_states(self, api_status, expected, alqaseh_gateway):
        with patch(ADAPTER_REQUEST, return_value={"payment_status": api_status}):
            assert get_adapter("alqaseh").verify("aq_1").state == expected

    def test_status_is_case_insensitive(self, alqaseh_gateway):
        """The redirect spells it 'Success'; the API spells it 'succeeded'."""
        with patch(ADAPTER_REQUEST, return_value={"payment_status": "SUCCEEDED"}):
            assert get_adapter("alqaseh").verify("aq_1").state == "paid"

    def test_missing_status_is_never_paid(self, alqaseh_gateway):
        with patch(ADAPTER_REQUEST, return_value={}):
            assert get_adapter("alqaseh").verify("aq_1").state == "unknown"

    def test_unrecognised_status_is_never_paid(self, alqaseh_gateway):
        with patch(ADAPTER_REQUEST, return_value={"payment_status": "brand_new"}):
            assert get_adapter("alqaseh").verify("aq_1").state == "unknown"


@pytest.mark.django_db
class TestCreateSession:
    def _ctx(self, subscription, plan):
        return CheckoutContext(
            subscription=subscription,
            target_plan=plan,
            billing_cycle=BillingCycle.MONTHLY,
            amount_usd=Decimal("29.00"),
            customer_email="a@b.c",
            customer_name="A B",
            customer_phone="+9647700000000",
            return_url="https://api.test/api/payments/alqaseh-return/",
            callback_url="https://api.test/api/payments/alqaseh-webhook/",
        )

    def test_amount_is_converted_to_iqd(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """Plans are priced in USD; Al Qaseh settles in IQD."""
        with patch(
            ADAPTER_REQUEST,
            return_value={"token": "tok_1", "payment_id": "aq_1"},
        ) as mock_request:
            get_adapter("alqaseh").create_session(
                self._ctx(lapsed_subscription, paid_plan)
            )

        body = mock_request.call_args.kwargs["json"]
        assert body["currency"] == "IQD"
        # 29 USD at the default 1300 rate, as a JSON number in whole dinars
        assert body["amount"] == 37700
        assert isinstance(body["amount"], int)

    def test_request_carries_every_required_field(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """CreatePaymentContextParams marks these six as required."""
        with patch(
            ADAPTER_REQUEST, return_value={"token": "t", "payment_id": "p"}
        ) as mock_request:
            get_adapter("alqaseh").create_session(
                self._ctx(lapsed_subscription, paid_plan)
            )

        args, kwargs = mock_request.call_args
        assert args[0] == "POST"
        assert args[1] == "/egw/payments/create"
        body = kwargs["json"]
        for field in (
            "amount", "currency", "description",
            "order_id", "redirect_url", "transaction_type",
        ):
            assert body.get(field), f"missing required field {field}"
        assert body["transaction_type"] == "Retail"
        assert body["custom_data"]["subscription_id"] == str(lapsed_subscription.id)

    def test_verify_calls_the_payment_id_endpoint(
        self, alqaseh_gateway
    ):
        with patch(
            ADAPTER_REQUEST, return_value={"payment_status": "succeeded"}
        ) as mock_request:
            get_adapter("alqaseh").verify("aq_xyz")

        args, _ = mock_request.call_args
        assert args[0] == "GET"
        assert args[1] == "/egw/payments/aq_xyz"

    def test_builds_hosted_page_url_from_token(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        with patch(
            ADAPTER_REQUEST,
            return_value={"token": "tok_abc", "payment_id": "aq_1"},
        ):
            session = get_adapter("alqaseh").create_session(
                self._ctx(lapsed_subscription, paid_plan)
            )

        assert session.checkout_url == "https://pay-test.alqaseh.com/pay/tok_abc"
        assert session.tran_ref == "aq_1"
        assert session.meta["token"] == "tok_abc"

    def test_order_id_is_unique_per_attempt(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """A repeated order id comes back as the `duplicated` status."""
        ctx = self._ctx(lapsed_subscription, paid_plan)
        with patch(
            ADAPTER_REQUEST,
            return_value={"token": "t", "payment_id": "p"},
        ):
            first = get_adapter("alqaseh").create_session(ctx)
            second = get_adapter("alqaseh").create_session(ctx)

        assert first.meta["order_id"] != second.meta["order_id"]
        assert first.meta["order_id"].startswith(f"SUB-{lapsed_subscription.id}-")

    def test_live_environment_uses_live_hosts(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        alqaseh_gateway.config = dict(alqaseh_gateway.config, environment="live")
        alqaseh_gateway.save(update_fields=["config"])
        with patch(
            ADAPTER_REQUEST, return_value={"token": "t", "payment_id": "p"}
        ):
            session = get_adapter("alqaseh").create_session(
                self._ctx(lapsed_subscription, paid_plan)
            )
        assert session.checkout_url.startswith("https://pay.alqaseh.com/pay/")

    def test_base_urls_are_overridable_from_config(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """So the unconfirmed live hosts can be corrected without a deploy."""
        alqaseh_gateway.config = dict(
            alqaseh_gateway.config, payBaseUrl="https://pay.example.test/checkout"
        )
        alqaseh_gateway.save(update_fields=["config"])
        with patch(
            ADAPTER_REQUEST, return_value={"token": "t", "payment_id": "p"}
        ):
            session = get_adapter("alqaseh").create_session(
                self._ctx(lapsed_subscription, paid_plan)
            )
        assert session.checkout_url == "https://pay.example.test/checkout/t"

    def test_missing_token_raises_rather_than_half_creating(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        with patch(ADAPTER_REQUEST, return_value={"nope": 1}):
            with pytest.raises(GatewayError):
                get_adapter("alqaseh").create_session(
                    self._ctx(lapsed_subscription, paid_plan)
                )

    def test_missing_credentials_raise(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        alqaseh_gateway.config = {"environment": "test"}
        alqaseh_gateway.save(update_fields=["config"])
        with pytest.raises(GatewayError):
            get_adapter("alqaseh").create_session(
                self._ctx(lapsed_subscription, paid_plan)
            )


@pytest.mark.django_db
class TestReferenceExtraction:
    class _Req:
        def __init__(self, params):
            self.GET = params
            self.data = {}

    def test_redirect_uses_payment_id(self):
        req = self._Req({"payment_id": "aq_9", "order_id": "SUB-1-abc"})
        assert get_adapter("alqaseh").extract_tran_ref(req) == "aq_9"

    def test_webhook_resolves_order_id_to_the_stored_payment(
        self, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """The webhook carries no payment_id, only our own order_id."""
        _pending_payment(
            lapsed_subscription, alqaseh_gateway, paid_plan,
            tran_ref="aq_stored", order_id="SUB-7-xyz",
        )
        req = self._Req({"order_id": "SUB-7-xyz"})
        assert get_adapter("alqaseh").extract_tran_ref(req) == "aq_stored"

    def test_unknown_order_id_yields_nothing(self, db):
        req = self._Req({"order_id": "SUB-nope"})
        assert get_adapter("alqaseh").extract_tran_ref(req) is None

    def test_no_reference_yields_nothing(self, db):
        assert get_adapter("alqaseh").extract_tran_ref(self._Req({})) is None


@pytest.mark.django_db
class TestReturnAndWebhook:
    def test_forged_success_is_not_applied(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """status=Success in the URL means nothing without the re-query."""
        payment = _pending_payment(lapsed_subscription, alqaseh_gateway, paid_plan)
        end_before = lapsed_subscription.end_date

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "prepared"}):
            res = api_client.get(
                "/api/payments/alqaseh-return/?payment_id=aq_pay_1&status=Success"
            )

        assert res.status_code == 302
        assert "status=success" not in res["Location"]
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.applied_at is None
        assert lapsed_subscription.is_active is False
        assert lapsed_subscription.end_date == end_before

    def test_confirmed_success_applies_period_once(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        payment = _pending_payment(lapsed_subscription, alqaseh_gateway, paid_plan)
        end_before = lapsed_subscription.end_date

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "succeeded"}):
            res = api_client.get(
                "/api/payments/alqaseh-return/?payment_id=aq_pay_1&status=Success"
            )

        assert res.status_code == 302
        assert "status=success" in res["Location"]
        payment.refresh_from_db()
        lapsed_subscription.refresh_from_db()
        assert payment.payment_status == PaymentStatus.COMPLETED.value
        assert payment.applied_at is not None
        assert lapsed_subscription.is_active is True
        assert lapsed_subscription.end_date > end_before

    def test_replay_is_idempotent(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        _pending_payment(lapsed_subscription, alqaseh_gateway, paid_plan)
        url = "/api/payments/alqaseh-return/?payment_id=aq_pay_1"

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "succeeded"}):
            api_client.get(url)
            lapsed_subscription.refresh_from_db()
            end_after_first = lapsed_subscription.end_date
            api_client.get(url)

        lapsed_subscription.refresh_from_db()
        assert lapsed_subscription.end_date == end_after_first

    def test_declined_marks_payment_failed(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        payment = _pending_payment(lapsed_subscription, alqaseh_gateway, paid_plan)

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "declined"}):
            res = api_client.get(
                "/api/payments/alqaseh-return/?payment_id=aq_pay_1"
            )

        assert "status=failed" in res["Location"]
        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.FAILED.value
        assert payment.applied_at is None

    def test_indeterminate_leaves_payment_pending(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        """`unknown` must not be mistaken for either success or failure."""
        payment = _pending_payment(lapsed_subscription, alqaseh_gateway, paid_plan)

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "unknown"}):
            api_client.get("/api/payments/alqaseh-return/?payment_id=aq_pay_1")

        payment.refresh_from_db()
        assert payment.payment_status == PaymentStatus.PENDING.value
        assert payment.applied_at is None

    def test_webhook_finalizes_via_order_id(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, alqaseh_gateway, paid_plan, order_id="SUB-9-zzz"
        )

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "succeeded"}):
            res = api_client.post(
                "/api/payments/alqaseh-webhook/",
                {"order_id": "SUB-9-zzz", "payment_status": "Success"},
                format="json",
            )

        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.applied_at is not None

    def test_webhook_forged_success_is_not_applied(
        self, api_client, alqaseh_gateway, lapsed_subscription, paid_plan
    ):
        payment = _pending_payment(
            lapsed_subscription, alqaseh_gateway, paid_plan, order_id="SUB-9-zzz"
        )

        with patch(ADAPTER_REQUEST, return_value={"payment_status": "prepared"}):
            res = api_client.post(
                "/api/payments/alqaseh-webhook/",
                {"order_id": "SUB-9-zzz", "payment_status": "Success"},
                format="json",
            )

        assert res.status_code == 200
        payment.refresh_from_db()
        assert payment.applied_at is None

    def test_webhook_unknown_order_is_404(self, api_client, alqaseh_gateway, db):
        res = api_client.post(
            "/api/payments/alqaseh-webhook/",
            {"order_id": "SUB-nope"},
            format="json",
        )
        assert res.status_code == 404


@pytest.mark.django_db
class TestCreateSessionEndpoint:
    def test_endpoint_creates_pending_payment(
        self, api_client, alqaseh_gateway, lapsed_subscription, owner_user, paid_plan
    ):
        api_client.force_authenticate(user=owner_user)
        with patch(
            ADAPTER_REQUEST,
            return_value={"token": "tok_e2e", "payment_id": "aq_e2e"},
        ):
            res = api_client.post(
                "/api/payments/create-alqaseh-session/",
                {"subscription_id": lapsed_subscription.id},
                format="json",
            )

        assert res.status_code == 200, res.content
        data = api_body(res)
        assert data["redirect_url"] == "https://pay-test.alqaseh.com/pay/tok_e2e"
        assert data["tran_ref"] == "aq_e2e"

        payment = Payment.objects.get(tran_ref="aq_e2e")
        assert payment.payment_status == PaymentStatus.PENDING.value
        assert payment.target_plan_id == paid_plan.id
        assert payment.billing_cycle == BillingCycle.MONTHLY
        # The Payment row stays in USD; only the charge is converted.
        assert payment.amount_usd == Decimal("29.00")
        assert payment.currency == "USD"
        assert payment.session_meta["order_id"] == data["order_id"]

    def test_requires_authentication(
        self, api_client, alqaseh_gateway, lapsed_subscription
    ):
        res = api_client.post(
            "/api/payments/create-alqaseh-session/",
            {"subscription_id": lapsed_subscription.id},
            format="json",
        )
        assert res.status_code == 401

    def test_forbidden_for_non_owner(
        self, api_client, alqaseh_gateway, lapsed_subscription, other_owner_user
    ):
        api_client.force_authenticate(user=other_owner_user)
        res = api_client.post(
            "/api/payments/create-alqaseh-session/",
            {"subscription_id": lapsed_subscription.id},
            format="json",
        )
        assert res.status_code == 403


@pytest.mark.django_db
class TestCredentialTest:
    def test_valid_credentials_report_success(self, alqaseh_gateway):
        with patch(ADAPTER_REQUEST, return_value=[]) as mock_request:
            result = get_adapter("alqaseh").test_credentials(alqaseh_gateway.config)
        assert result["success"] is True
        args, kwargs = mock_request.call_args
        assert args[1] == "/egw/payments/history"
        assert kwargs["params"] == {"limit": 1}

    def test_rejected_credentials_report_failure(self, alqaseh_gateway):
        with patch(ADAPTER_REQUEST, side_effect=GatewayError("Al Qaseh API error: 401")):
            result = get_adapter("alqaseh").test_credentials(alqaseh_gateway.config)
        assert result["success"] is False
        assert "401" in result["message"]

    def test_missing_credentials_short_circuit(self, alqaseh_gateway):
        with patch(ADAPTER_REQUEST) as mock_request:
            result = get_adapter("alqaseh").test_credentials({"environment": "test"})
        assert result["success"] is False
        mock_request.assert_not_called()


@pytest.mark.django_db
class TestSecretsAreMasked:
    def test_client_secret_is_not_readable(self, alqaseh_gateway):
        from subscriptions.serializers import PaymentGatewaySerializer

        data = PaymentGatewaySerializer(alqaseh_gateway).data
        assert "test_secret_value" not in str(data["config"])
        # clientId and terminalId are identifiers, not credentials
        assert data["config"]["clientId"] == "public_test"
        assert data["config"]["terminalId"] == "T-1"
