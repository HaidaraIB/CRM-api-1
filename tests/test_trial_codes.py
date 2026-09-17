"""
Launch trial codes: validate, redeem, registration, billing conversion, expiry.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from conftest import api_body
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
    TrialCode,
    TrialCodeRedemption,
)
from subscriptions.services.billing import apply_successful_payment, resolve_checkout_pricing
from subscriptions.services.trial_codes import redeem_trial_code, validate_trial_code


@pytest.fixture
def paid_plan(db):
    return Plan.objects.create(
        name="Pro",
        description="paid",
        price_monthly=Decimal("49.00"),
        price_yearly=Decimal("490.00"),
        tier=2,
        visible=True,
    )


@pytest.fixture
def trial_code(paid_plan, db):
    return TrialCode.objects.create(
        code="LAUNCH30",
        label="launch",
        trial_days=30,
        plan=paid_plan,
        max_redemptions=5,
    )


@pytest.fixture
def super_admin_user(db):
    from accounts.models import User

    return User.objects.create_user(
        username="superadmin",
        email="super@test.com",
        password="testpass123",
        is_superuser=True,
        role="super_admin",
    )


def _register_payload(domain_suffix: str, trial_code: str | None = None):
    payload = {
        "company": {
            "name": f"Co {domain_suffix}",
            "domain": f"co-{domain_suffix}.example.com",
            "specialization": "services",
        },
        "owner": {
            "first_name": "A",
            "last_name": "B",
            "email": f"owner-{domain_suffix}@test.com",
            "username": f"owner_{domain_suffix}",
            "password": "SecurePass123!",
            "phone": f"+964770{domain_suffix[:7].zfill(7)}",
        },
    }
    if trial_code:
        payload["trial_code"] = trial_code
    else:
        payload["plan_id"] = None
    return payload


@pytest.mark.django_db
def test_validate_trial_code_public(api_client, trial_code):
    r = api_client.post(
        reverse("validate_trial_code_public"),
        {"code": "launch30"},
        format="json",
    )
    assert r.status_code == status.HTTP_200_OK
    data = api_body(r)
    assert data["valid"] is True
    assert data["trial_days"] == 30
    assert data["plan_id"] == trial_code.plan_id


@pytest.mark.django_db
def test_validate_unknown_code(api_client):
    r = api_client.post(
        reverse("validate_trial_code_public"),
        {"code": "NOPE99"},
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["error"]["code"] == "invalid_code"


@pytest.mark.django_db
def test_register_with_trial_code(api_client, trial_code):
    payload = _register_payload("reg1", trial_code="LAUNCH30")
    payload.pop("plan_id", None)
    with patch("accounts.email_registration_policy.effective_registration_email_verification_required", return_value=False):
        with patch("accounts.phone_otp_policy.effective_phone_otp_required", return_value=False):
            r = api_client.post(reverse("register_company"), payload, format="json")
    assert r.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    data = api_body(r)
    assert data.get("requires_payment") is False
    sub = Subscription.objects.filter(company_id=data["company"]["id"]).first()
    assert sub is not None
    assert sub.subscription_status == SubscriptionStatus.TRIALING
    assert sub.plan_id == trial_code.plan_id
    assert sub.trial_code_id == trial_code.id
    assert TrialCodeRedemption.objects.filter(code=trial_code).count() == 1
    trial_code.refresh_from_db()
    assert trial_code.redeemed_count == 1


@pytest.mark.django_db
def test_redeem_inactive_subscription(api_client, company, owner_user, paid_plan, trial_code, db):
    now = timezone.now()
    sub = Subscription.objects.create(
        company=company,
        plan=paid_plan,
        is_active=False,
        end_date=now - timedelta(days=1),
        billing_cycle=BillingCycle.MONTHLY,
    )
    api_client.force_authenticate(user=owner_user)
    r = api_client.post(
        reverse("redeem_trial_code_subscription"),
        {"code": "LAUNCH30"},
        format="json",
    )
    assert r.status_code == status.HTTP_200_OK
    data = api_body(r)
    assert data["is_active"] is True
    assert "access" in data
    sub.refresh_from_db()
    assert sub.is_active is True
    assert sub.subscription_status == SubscriptionStatus.TRIALING


@pytest.mark.django_db
def test_redeem_not_eligible_when_active(api_client, company, owner_user, subscription, trial_code):
    api_client.force_authenticate(user=owner_user)
    r = api_client.post(
        reverse("redeem_trial_code_subscription"),
        {"code": "LAUNCH30"},
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["error"]["code"] == "not_eligible"


@pytest.mark.django_db
def test_redeem_exhausted_code(api_client, company, owner_user, paid_plan, db):
    tc = TrialCode.objects.create(
        code="ONEUSE",
        trial_days=7,
        plan=paid_plan,
        max_redemptions=1,
        redeemed_count=1,
    )
    now = timezone.now()
    Subscription.objects.create(
        company=company,
        plan=paid_plan,
        is_active=False,
        end_date=now - timedelta(days=1),
    )
    api_client.force_authenticate(user=owner_user)
    r = api_client.post(
        reverse("redeem_trial_code_subscription"),
        {"code": "ONEUSE"},
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["error"]["code"] == "code_exhausted"


@pytest.mark.django_db
def test_already_redeemed_same_company(api_client, company, owner_user, paid_plan, trial_code, db):
    redeem_trial_code(raw_code="LAUNCH30", company=company, owner=owner_user)
    now = timezone.now()
    Subscription.objects.filter(company=company).update(
        is_active=False, end_date=now - timedelta(days=1)
    )
    api_client.force_authenticate(user=owner_user)
    r = api_client.post(
        reverse("redeem_trial_code_subscription"),
        {"code": "LAUNCH30"},
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["error"]["code"] == "already_redeemed"


@pytest.mark.django_db
def test_promo_trial_checkout_is_initial(company, paid_plan, trial_code, db):
    sub = redeem_trial_code(raw_code="LAUNCH30", company=company, owner=company.owner)
    assert sub.subscription_status == SubscriptionStatus.TRIALING
    plan, cycle, amount, intent = resolve_checkout_pricing(
        sub, target_plan_id=paid_plan.id, billing_cycle_param=BillingCycle.MONTHLY
    )
    assert intent == "initial"
    assert amount == Decimal("49.00")


@pytest.mark.django_db
def test_paid_conversion_clears_trial_code(company, paid_plan, trial_code, db):
    sub = redeem_trial_code(raw_code="LAUNCH30", company=company, owner=company.owner)
    gateway = PaymentGateway.objects.create(
        name="Stripe",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
    )
    payment = Payment.objects.create(
        subscription=sub,
        amount=Decimal("49.00"),
        currency="USD",
        payment_method=gateway,
        payment_status=PaymentStatus.COMPLETED.value,
        target_plan=paid_plan,
        billing_cycle=BillingCycle.MONTHLY,
    )
    apply_successful_payment(
        sub,
        amount_usd=49.0,
        target_plan=paid_plan,
        billing_cycle=BillingCycle.MONTHLY,
        exclude_payment_id=payment.id,
    )
    sub.refresh_from_db()
    assert sub.trial_code_id is None
    assert sub.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.django_db
def test_end_expired_promo_trial(company, paid_plan, trial_code, db):
    sub = redeem_trial_code(raw_code="LAUNCH30", company=company, owner=company.owner)
    Subscription.objects.filter(pk=sub.pk).update(
        end_date=timezone.now() - timedelta(hours=1)
    )
    from django.core.management import call_command

    call_command("end_expired_subscriptions")
    sub.refresh_from_db()
    assert sub.is_active is False


@pytest.mark.django_db
def test_deactivate_code_does_not_end_active_trial(company, paid_plan, trial_code, db):
    redeem_trial_code(raw_code="LAUNCH30", company=company, owner=company.owner)
    trial_code.is_active = False
    trial_code.save(update_fields=["is_active"])
    sub = Subscription.objects.get(company=company, is_active=True)
    assert sub.is_truly_active()


@pytest.mark.django_db
def test_admin_create_trial_code(api_client, super_admin_user, paid_plan):
    api_client.force_authenticate(user=super_admin_user)
    r = api_client.post(
        "/api/v1/trial-codes/",
        {
            "code": "CUSTOM1",
            "label": "vip",
            "trial_days": 14,
            "plan": paid_plan.id,
            "max_redemptions": 10,
        },
        format="json",
    )
    assert r.status_code == status.HTTP_201_CREATED
    assert TrialCode.objects.filter(code="CUSTOM1").exists()


@pytest.mark.django_db
def test_generate_batch(api_client, super_admin_user, paid_plan):
    api_client.force_authenticate(user=super_admin_user)
    r = api_client.post(
        "/api/v1/trial-codes/generate-batch/",
        {
            "label": "batch-a",
            "trial_days": 7,
            "plan": paid_plan.id,
            "quantity": 3,
        },
        format="json",
    )
    assert r.status_code == status.HTTP_201_CREATED
    data = api_body(r)
    assert data["created_count"] == 3
    assert TrialCode.objects.filter(label="batch-a").count() == 3
