"""
Billing-scoped tokens: the way out of the expired-subscription deadlock.

An owner whose subscription lapsed cannot log in (login withholds JWTs without
an active subscription) but checkout requires the owner to be authenticated.
Login therefore hands back a checkout-only token, and BillingScopeMiddleware
keeps that token away from everything except paying.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from accounts.billing_access import (
    BILLING_SCOPE,
    SCOPE_CLAIM,
    issue_billing_access_token,
)
from subscriptions.models import (
    BillingCycle,
    PaymentGateway,
    PaymentGatewayStatus,
    Plan,
    Subscription,
)


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
def expired_subscription(company, paid_plan, owner_user, db):
    """The state a lapsed tenant is actually in: is_active False, end date past."""
    owner_user.phone_verified = True
    owner_user.save(update_fields=["phone_verified"])
    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=paid_plan,
        is_active=False,
        start_date=now - timedelta(days=60),
        end_date=now - timedelta(days=1),
        current_period_start=now - timedelta(days=31),
        billing_cycle=BillingCycle.MONTHLY,
    )


def _login(api_client, username="owner_user", password="testpass123"):
    return api_client.post(
        reverse("token_obtain_pair"),
        {"username": username, "password": password},
        format="json",
    )


def _error(response):
    return response.data.get("error", {})


@pytest.mark.django_db
def test_login_refuses_session_but_returns_payment_token(api_client, expired_subscription):
    """The deadlock breaker: no access/refresh, but a token to pay with."""
    r = _login(api_client)

    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert "access" not in r.data
    assert "refresh" not in r.data

    details = _error(r).get("details", {})
    assert details.get("subscriptionId") == expired_subscription.id
    assert details.get("paymentToken")


@pytest.mark.django_db
def test_payment_token_is_billing_scoped(api_client, expired_subscription):
    from rest_framework_simplejwt.tokens import AccessToken

    r = _login(api_client)
    token = AccessToken(_error(r)["details"]["paymentToken"])

    assert token[SCOPE_CLAIM] == BILLING_SCOPE
    assert token["subscription_id"] == expired_subscription.id
    assert str(token["user_id"]) == str(expired_subscription.company.owner_id)


@pytest.mark.django_db
def test_payment_token_opens_checkout(api_client, expired_subscription):
    """End to end: the token the login error handed back can actually pay."""
    PaymentGateway.objects.create(
        name="ZainCash",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
        config={"msisdn": "1", "merchant_id": "m", "secret": "s"},
    )
    token = _error(_login(api_client))["details"]["paymentToken"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    with patch(
        "subscriptions.zaincash_utils.create_zaincash_payment_session",
        return_value={"id": "zc_1", "payment_url": "https://test.zaincash.iq/pay?id=zc_1"},
    ):
        r = api_client.post(
            reverse("create_zaincash_payment"),
            {"subscription_id": expired_subscription.id, "billing_cycle": "monthly"},
            format="json",
        )

    assert r.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_payment_token_may_poll_payment_status(api_client, expired_subscription):
    token = issue_billing_access_token(
        expired_subscription.company.owner, expired_subscription
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    r = api_client.get(
        reverse("check_payment_status", args=[expired_subscription.id])
    )

    assert r.status_code == status.HTTP_200_OK


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name,args",
    [
        ("user-list", None),
        ("client-list", None),
        ("company-list", None),
    ],
)
def test_payment_token_cannot_reach_the_crm(
    api_client, expired_subscription, url_name, args
):
    """The whole point: paying does not double as access to the product."""
    token = issue_billing_access_token(
        expired_subscription.company.owner, expired_subscription
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    r = api_client.get(reverse(url_name, args=args))

    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert r.json()["error"]["code"] == "billing_scope_only"


@pytest.mark.django_db
def test_billing_scope_denial_is_not_mistaken_for_a_dead_session(
    api_client, expired_subscription
):
    """
    Clients wipe stored tokens on `subscription_inactive`. This denial must not
    use that code, or the browser would throw away the token mid-checkout.
    """
    token = issue_billing_access_token(
        expired_subscription.company.owner, expired_subscription
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    r = api_client.get(reverse("user-list"))
    body = r.json()["error"]

    assert body["code"] != "subscription_inactive"
    assert "subscription is not active" not in body["message"].lower()


@pytest.mark.django_db
def test_ordinary_session_is_unaffected(api_client, subscription, owner_user):
    """A normal token carries no scope claim, so the middleware ignores it."""
    from rest_framework_simplejwt.tokens import RefreshToken

    access = RefreshToken.for_user(owner_user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    assert api_client.get(reverse("user-list")).status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_no_token_for_staff(expired_subscription, employee_user):
    """Staff get no route back in; only the owner can pay."""
    assert issue_billing_access_token(employee_user, expired_subscription) is None


@pytest.mark.django_db
def test_no_token_for_another_companys_owner(expired_subscription, other_owner_user):
    assert issue_billing_access_token(other_owner_user, expired_subscription) is None


@pytest.mark.django_db
def test_expired_payment_token_is_rejected(api_client, expired_subscription):
    from accounts import billing_access

    with patch.object(billing_access, "BILLING_TOKEN_LIFETIME", timedelta(seconds=-1)):
        token = issue_billing_access_token(
            expired_subscription.company.owner, expired_subscription
        )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    r = api_client.get(reverse("check_payment_status", args=[expired_subscription.id]))

    assert r.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_forged_scope_claim_is_rejected(api_client, expired_subscription):
    """An unsigned/forged token never reaches the allowlist decision."""
    token = issue_billing_access_token(
        expired_subscription.company.owner, expired_subscription
    )
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered}")

    r = api_client.get(reverse("check_payment_status", args=[expired_subscription.id]))

    assert r.status_code == status.HTTP_401_UNAUTHORIZED
