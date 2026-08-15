"""
The gateway registry and adapter contract.

These pin the thing the old code got wrong in four separate places: turning an
operator-editable gateway name into the right code, and turning that gateway's
private status vocabulary into one shared notion of paid/failed/pending.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

from subscriptions.gateways import (
    CARD_GROUP,
    CheckoutContext,
    CheckoutSession,
    GatewayAdapter,
    GatewayResult,
    adapter_for_gateway,
    adapter_for_name,
    all_adapters,
    get_adapter,
    resolve_gateway,
)
from subscriptions.models import PaymentGateway, PaymentGatewayStatus

ALL_SLUGS = {"stripe", "paytabs", "qicard", "fib", "zaincash", "alqaseh"}
#: The interchangeable card processors - only one may be enabled at a time.
CARD_SLUGS = {"stripe", "paytabs", "alqaseh"}


class TestRegistry:
    def test_every_gateway_has_an_adapter(self):
        assert {a.slug for a in all_adapters()} == ALL_SLUGS

    def test_card_gateways_share_an_exclusive_group(self):
        assert {a.slug for a in all_adapters() if a.exclusive_group == CARD_GROUP} == (
            CARD_SLUGS
        )

    def test_wallet_gateways_are_not_exclusive(self):
        for adapter in all_adapters():
            if adapter.slug not in CARD_SLUGS:
                assert adapter.exclusive_group == "", adapter.slug

    def test_adapters_satisfy_the_protocol(self):
        for adapter in all_adapters():
            assert isinstance(adapter, GatewayAdapter), adapter.slug

    @pytest.mark.parametrize(
        "name,slug",
        [
            ("Stripe", "stripe"),
            ("stripe (live)", "stripe"),
            ("PayTabs", "paytabs"),
            ("Pay Tabs Iraq", "paytabs"),
            ("QiCard", "qicard"),
            ("Qi Card", "qicard"),
            ("QI-CARD", "qicard"),
            ("FIB", "fib"),
            ("First Iraqi Bank", "fib"),
            ("ZainCash", "zaincash"),
            ("Zain Cash", "zaincash"),
            ("زين كاش zain", "zaincash"),
            ("Al Qaseh", "alqaseh"),
            ("AlQaseh", "alqaseh"),
            ("Qaseh", "alqaseh"),
        ],
    )
    def test_operator_entered_names_resolve(self, name, slug):
        adapter = adapter_for_name(name)
        assert adapter is not None, name
        assert adapter.slug == slug

    def test_unknown_name_resolves_to_nothing(self):
        assert adapter_for_name("Bank Transfer") is None
        assert adapter_for_name("") is None

    def test_get_adapter_by_slug(self):
        assert get_adapter("stripe").slug == "stripe"
        assert get_adapter("nope") is None


@pytest.mark.django_db
class TestGatewayRowResolution:
    def test_resolve_finds_active_enabled_row(self):
        row = PaymentGateway.objects.create(
            name="Stripe", status=PaymentGatewayStatus.ACTIVE.value, enabled=True
        )
        found, adapter = resolve_gateway("stripe")
        assert found == row
        assert adapter.slug == "stripe"

    def test_resolve_skips_disabled_row(self):
        PaymentGateway.objects.create(
            name="Stripe", status=PaymentGatewayStatus.ACTIVE.value, enabled=False
        )
        found, adapter = resolve_gateway("stripe")
        assert found is None
        assert adapter.slug == "stripe"

    def test_resolve_skips_inactive_row(self):
        PaymentGateway.objects.create(
            name="Stripe", status=PaymentGatewayStatus.DISABLED.value, enabled=True
        )
        assert resolve_gateway("stripe")[0] is None

    def test_adapter_for_gateway_row(self):
        row = PaymentGateway.objects.create(
            name="Qi Card", status=PaymentGatewayStatus.ACTIVE.value, enabled=True
        )
        assert adapter_for_gateway(row).slug == "qicard"

    def test_adapter_for_unknown_row_is_none(self):
        row = PaymentGateway.objects.create(
            name="Cash on delivery",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        )
        assert adapter_for_gateway(row) is None

    def test_adapter_for_none_is_none(self):
        assert adapter_for_gateway(None) is None


class TestStateMapping:
    """Each adapter owns its gateway's vocabulary; callers see one alphabet."""

    @pytest.mark.parametrize(
        "slug,util,raw,expected",
        [
            # Stripe
            ("stripe", "subscriptions.stripe_utils.verify_stripe_payment",
             {"stripe_payment_status": "paid"}, "paid"),
            ("stripe", "subscriptions.stripe_utils.verify_stripe_payment",
             {"payment_status": "completed"}, "paid"),
            ("stripe", "subscriptions.stripe_utils.verify_stripe_payment",
             {"stripe_payment_status": "unpaid"}, "pending"),
            # PayTabs
            ("paytabs", "subscriptions.paytabs_utils.verify_paytabs_payment",
             {"payment_result": {"response_status": "A"}}, "paid"),
            ("paytabs", "subscriptions.paytabs_utils.verify_paytabs_payment",
             {"payment_result": {"response_status": "D"}}, "failed"),
            ("paytabs", "subscriptions.paytabs_utils.verify_paytabs_payment",
             {"payment_result": {"response_status": "P"}}, "pending"),
            # QiCard
            ("qicard", "subscriptions.qicard_utils.verify_qicard_payment",
             {"status": "SUCCESS"}, "paid"),
            ("qicard", "subscriptions.qicard_utils.verify_qicard_payment",
             {"status": "AUTHENTICATION_FAILED"}, "failed"),
            ("qicard", "subscriptions.qicard_utils.verify_qicard_payment",
             {"status": "CREATED"}, "pending"),
            # FIB
            ("fib", "subscriptions.fib_utils.check_fib_payment_status",
             {"status": "PAID"}, "paid"),
            ("fib", "subscriptions.fib_utils.check_fib_payment_status",
             {"status": "DECLINED"}, "failed"),
            ("fib", "subscriptions.fib_utils.check_fib_payment_status",
             {"status": "UNPAID"}, "pending"),
            # ZainCash
            ("zaincash", "subscriptions.zaincash_utils.check_zaincash_payment_status",
             {"status": "success"}, "paid"),
            ("zaincash", "subscriptions.zaincash_utils.check_zaincash_payment_status",
             {"status": "failed"}, "failed"),
            ("zaincash", "subscriptions.zaincash_utils.check_zaincash_payment_status",
             {"status": "pending"}, "pending"),
        ],
    )
    def test_status_normalizes(self, slug, util, raw, expected):
        with patch(util, return_value=raw):
            result = get_adapter(slug).verify("ref-1")
        assert result.state == expected
        assert result.raw == raw

    def test_missing_status_is_pending_not_paid(self):
        """An empty gateway response must never read as paid."""
        for slug, util in [
            ("stripe", "subscriptions.stripe_utils.verify_stripe_payment"),
            ("paytabs", "subscriptions.paytabs_utils.verify_paytabs_payment"),
            ("qicard", "subscriptions.qicard_utils.verify_qicard_payment"),
            ("fib", "subscriptions.fib_utils.check_fib_payment_status"),
            ("zaincash", "subscriptions.zaincash_utils.check_zaincash_payment_status"),
        ]:
            with patch(util, return_value={}):
                assert get_adapter(slug).verify("ref").state == "pending", slug


class TestGatewayResult:
    def test_is_paid_and_is_failed(self):
        assert GatewayResult("paid").is_paid
        assert not GatewayResult("paid").is_failed
        assert GatewayResult("failed").is_failed
        assert not GatewayResult("pending").is_paid
        assert not GatewayResult("unknown").is_paid
        assert not GatewayResult("unknown").is_failed


class TestTranRefExtraction:
    class _FakeRequest:
        def __init__(self, params):
            self.GET = params
            self.data = {}

    def test_stripe_reads_session_id(self):
        req = self._FakeRequest({"session_id": "cs_123"})
        assert get_adapter("stripe").extract_tran_ref(req) == "cs_123"

    def test_paytabs_reads_either_spelling(self):
        assert get_adapter("paytabs").extract_tran_ref(
            self._FakeRequest({"tranRef": "PT_1"})
        ) == "PT_1"
        assert get_adapter("paytabs").extract_tran_ref(
            self._FakeRequest({"tran_ref": "PT_2"})
        ) == "PT_2"

    def test_qicard_reads_payment_id(self):
        req = self._FakeRequest({"paymentId": "qi_9"})
        assert get_adapter("qicard").extract_tran_ref(req) == "qi_9"

    def test_missing_ref_is_none(self):
        assert get_adapter("fib").extract_tran_ref(self._FakeRequest({})) is None

    def test_zaincash_decodes_jwt_to_transaction_id(self):
        """tran_ref must stay the transaction id, never the token."""
        req = self._FakeRequest({"token": "header.payload.signature"})
        with patch(
            "subscriptions.zaincash_utils.verify_zaincash_payment",
            return_value={"id": "zc_txn_42", "status": "success"},
        ):
            assert get_adapter("zaincash").extract_tran_ref(req) == "zc_txn_42"

    def test_zaincash_passes_through_a_plain_transaction_id(self):
        req = self._FakeRequest({"id": "zc_txn_42"})
        assert get_adapter("zaincash").extract_tran_ref(req) == "zc_txn_42"

    def test_zaincash_unverifiable_token_yields_nothing(self):
        req = self._FakeRequest({"token": "bad.jwt.sig"})
        with patch(
            "subscriptions.zaincash_utils.verify_zaincash_payment",
            side_effect=Exception("bad signature"),
        ):
            assert get_adapter("zaincash").extract_tran_ref(req) is None


class TestCheckoutDataclasses:
    def test_checkout_session_defaults(self):
        session = CheckoutSession(tran_ref="abc")
        assert session.checkout_url == ""
        assert session.expires_at is None
        assert session.meta == {}

    def test_checkout_context_exposes_subscription_id(self):
        class _Sub:
            id = 7

        ctx = CheckoutContext(
            subscription=_Sub(),
            target_plan=object(),
            billing_cycle="monthly",
            amount_usd=Decimal("29.00"),
            customer_email="a@b.c",
            customer_name="A B",
        )
        assert ctx.subscription_id == 7
