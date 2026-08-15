"""
Only one card gateway may be live at a time.

Stripe, PayTabs and Al Qaseh are interchangeable ways to take the same card
payment. The rule used to live in the admin panel as two hardcoded name checks
plus a second HTTP request, so it knew nothing about Al Qaseh, could not be
atomic, and was skipped entirely by any other client. These tests pin the rule
where it now lives: on the server, on every write path that can enable a row.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from conftest import api_body
from subscriptions.gateways import conflicting_gateway_rows
from subscriptions.models import PaymentGateway, PaymentGatewayStatus
from subscriptions.services import apply_exclusive_activation

User = get_user_model()

ACTIVE = PaymentGatewayStatus.ACTIVE.value
DISABLED = PaymentGatewayStatus.DISABLED.value


def make_gateway(name, *, enabled=True, status=ACTIVE, config=None):
    return PaymentGateway.objects.create(
        name=name, status=status, enabled=enabled, config=config or {}
    )


@pytest.fixture
def super_admin(db):
    return User.objects.create_superuser(
        username="gateway_admin",
        email="gateway_admin@test.com",
        password="securepassword123",
    )


@pytest.fixture
def admin_client(api_client, super_admin):
    api_client.force_authenticate(user=super_admin)
    return api_client


def toggle_url(gateway):
    return reverse("paymentgateway-toggle-enabled", args=[gateway.id])


def detail_url(gateway):
    return reverse("paymentgateway-detail", args=[gateway.id])


@pytest.mark.django_db
class TestConflictResolution:
    """Which rows the registry considers rivals, before anything is written."""

    def test_card_gateways_are_rivals_of_each_other(self):
        stripe = make_gateway("Stripe")
        paytabs = make_gateway("PayTabs")
        alqaseh = make_gateway("Al Qaseh")

        rivals = conflicting_gateway_rows(alqaseh)
        assert {row.pk for row in rivals} == {stripe.pk, paytabs.pk}

    def test_operator_spellings_still_match(self):
        make_gateway("Stripe Payments (Live)")
        alqaseh = make_gateway("AlQaseh IQ")
        assert [row.name for row in conflicting_gateway_rows(alqaseh)] == [
            "Stripe Payments (Live)"
        ]

    def test_wallet_gateways_have_no_rivals(self):
        make_gateway("Stripe")
        for name in ("ZainCash", "Qi Card", "FIB"):
            wallet = make_gateway(name)
            assert conflicting_gateway_rows(wallet) == []

    def test_card_gateway_does_not_rival_wallets(self):
        make_gateway("ZainCash")
        make_gateway("Qi Card")
        assert conflicting_gateway_rows(make_gateway("Stripe")) == []

    def test_unknown_gateway_has_no_rivals(self):
        make_gateway("Stripe")
        assert conflicting_gateway_rows(make_gateway("Cash on delivery")) == []

    def test_already_disabled_rows_are_not_rivals(self):
        make_gateway("Stripe", enabled=False)
        make_gateway("PayTabs", status=DISABLED)
        assert conflicting_gateway_rows(make_gateway("Al Qaseh")) == []


@pytest.mark.django_db
class TestActivationService:
    def test_enabling_disables_every_other_card_gateway(self):
        stripe = make_gateway("Stripe")
        paytabs = make_gateway("PayTabs")
        alqaseh = make_gateway("Al Qaseh")

        disabled = apply_exclusive_activation(alqaseh)

        assert sorted(disabled) == ["PayTabs", "Stripe"]
        for row in (stripe, paytabs):
            row.refresh_from_db()
            assert row.enabled is False
            # Not just `enabled` - the old client-side path left status="active"
            # behind, which made the row's two flags disagree.
            assert row.status == DISABLED
        alqaseh.refresh_from_db()
        assert alqaseh.enabled is True and alqaseh.status == ACTIVE

    def test_wallets_survive_a_card_activation(self):
        zaincash = make_gateway("ZainCash")
        qicard = make_gateway("Qi Card")

        apply_exclusive_activation(make_gateway("Stripe"))

        for row in (zaincash, qicard):
            row.refresh_from_db()
            assert row.enabled is True

    def test_disabled_gateway_does_not_disable_anything(self):
        stripe = make_gateway("Stripe")
        alqaseh = make_gateway("Al Qaseh", enabled=False, status=DISABLED)

        assert apply_exclusive_activation(alqaseh) == []
        stripe.refresh_from_db()
        assert stripe.enabled is True

    def test_enabled_but_not_active_does_not_disable_anything(self):
        """`enabled=True, status=setup_required` is not live, so it wins nothing."""
        stripe = make_gateway("Stripe")
        pending = make_gateway(
            "Al Qaseh", status=PaymentGatewayStatus.SETUP_REQUIRED.value
        )

        assert apply_exclusive_activation(pending) == []
        stripe.refresh_from_db()
        assert stripe.enabled is True

    def test_is_idempotent(self):
        make_gateway("Stripe")
        alqaseh = make_gateway("Al Qaseh")

        assert apply_exclusive_activation(alqaseh) == ["Stripe"]
        assert apply_exclusive_activation(alqaseh) == []


@pytest.mark.django_db
class TestToggleEndpoint:
    def test_toggling_a_card_gateway_on_disables_the_others(self, admin_client):
        stripe = make_gateway("Stripe")
        paytabs = make_gateway("PayTabs")
        alqaseh = make_gateway("Al Qaseh", enabled=False, status=DISABLED)

        response = admin_client.post(toggle_url(alqaseh))

        assert response.status_code == 200
        body = api_body(response)
        assert body["enabled"] is True
        assert sorted(body["disabled_gateways"]) == ["PayTabs", "Stripe"]
        for row in (stripe, paytabs):
            row.refresh_from_db()
            assert row.enabled is False

    def test_toggling_a_card_gateway_off_touches_nothing_else(self, admin_client):
        zaincash = make_gateway("ZainCash")
        stripe = make_gateway("Stripe")

        response = admin_client.post(toggle_url(stripe))

        body = api_body(response)
        assert body["enabled"] is False
        assert body["disabled_gateways"] == []
        zaincash.refresh_from_db()
        assert zaincash.enabled is True

    def test_toggling_a_wallet_on_keeps_the_card_gateway(self, admin_client):
        stripe = make_gateway("Stripe")
        zaincash = make_gateway("ZainCash", enabled=False, status=DISABLED)

        body = api_body(admin_client.post(toggle_url(zaincash)))

        assert body["disabled_gateways"] == []
        stripe.refresh_from_db()
        assert stripe.enabled is True


@pytest.mark.django_db
class TestPatchEndpoint:
    """The path the admin panel's own checks never covered."""

    def test_patch_enabled_true_disables_rivals(self, admin_client):
        stripe = make_gateway("Stripe")
        alqaseh = make_gateway("Al Qaseh", enabled=False, status=DISABLED)

        response = admin_client.patch(
            detail_url(alqaseh),
            {"enabled": True, "status": ACTIVE},
            format="json",
        )

        assert response.status_code == 200
        assert api_body(response)["disabled_gateways"] == ["Stripe"]
        stripe.refresh_from_db()
        assert stripe.enabled is False
        assert stripe.status == DISABLED

    def test_patching_config_only_disables_nothing(self, admin_client):
        stripe = make_gateway("Stripe")
        alqaseh = make_gateway("Al Qaseh", enabled=False, status=DISABLED)

        response = admin_client.patch(
            detail_url(alqaseh),
            {"config": {"clientId": "abc", "clientSecret": "shhh"}},
            format="json",
        )

        assert response.status_code == 200
        assert api_body(response)["disabled_gateways"] == []
        stripe.refresh_from_db()
        assert stripe.enabled is True

    def test_creating_an_enabled_card_gateway_disables_rivals(self, admin_client):
        stripe = make_gateway("Stripe")

        response = admin_client.post(
            reverse("paymentgateway-list"),
            {"name": "Al Qaseh", "status": ACTIVE, "enabled": True},
            format="json",
        )

        assert response.status_code == 201
        assert api_body(response)["disabled_gateways"] == ["Stripe"]
        stripe.refresh_from_db()
        assert stripe.enabled is False


@pytest.mark.django_db
class TestPublicList:
    """What tenants can see: never two card gateways to choose between."""

    def test_only_one_card_gateway_is_offered(self, api_client, admin_client):
        make_gateway("Stripe")
        make_gateway("PayTabs")
        make_gateway("ZainCash")
        alqaseh = make_gateway("Al Qaseh", enabled=False, status=DISABLED)
        admin_client.post(toggle_url(alqaseh))

        body = api_body(api_client.get(reverse("public_payment_gateway_list")))
        names = sorted(row["name"] for row in body)

        assert names == ["Al Qaseh", "ZainCash"]
