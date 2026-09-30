"""Super-admin delete guards for platform-managed resources."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status

from companies.models import Company
from demo_bookings.models import DemoBooking, DemoBookingStatus
from platform_content.models import GuideArticle, GuideCategory
from subscriptions.models import PaymentGateway, PaymentGatewayStatus, Plan

User = get_user_model()


def _error_code(response) -> str:
    body = response.json()
    return body.get("error", {}).get("code") or body.get("code")


@pytest.fixture
def gateway(db):
    return PaymentGateway.objects.create(
        name="Guard Test GW",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
    )


@pytest.fixture
def super_admin(db):
    return User.objects.create_superuser(
        username="delete_guard_admin",
        email="delete_guard_admin@test.com",
        password="securepassword123",
    )


def _empty_company(db) -> Company:
    owner = User.objects.create_user(
        username="empty_co_owner",
        email="empty_co_owner@test.com",
        password="testpass123",
        role="admin",
        email_verified=True,
        phone_verified=True,
    )
    company = Company.objects.create(
        name="Empty Co",
        domain="empty-co.example.com",
        owner=owner,
    )
    owner.company = company
    owner.save(update_fields=["company"])
    return company


@pytest.mark.django_db
def test_company_delete_blocked_when_subscriptions_exist(api_client, super_admin, company, subscription):
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/companies/{company.id}/")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_code(r) == "has_billing"
    assert Company.objects.filter(pk=company.id).exists()


@pytest.mark.django_db
def test_company_delete_allowed_without_subscriptions(api_client, super_admin, db):
    company = _empty_company(db)
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/companies/{company.id}/")
    assert r.status_code == status.HTTP_204_NO_CONTENT
    assert not Company.objects.filter(pk=company.id).exists()


@pytest.mark.django_db
def test_plan_delete_blocked_with_subscription(api_client, super_admin, company, subscription):
    plan_id = subscription.plan_id
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/plans/{plan_id}/")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_code(r) == "plan_in_use"


@pytest.mark.django_db
def test_plan_delete_allowed_when_unused(api_client, super_admin, db):
    plan = Plan.objects.create(
        name="Orphan Plan",
        name_ar="",
        description="d",
        price_monthly=Decimal("1.00"),
        price_yearly=Decimal("10.00"),
    )
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/plans/{plan.id}/")
    assert r.status_code == status.HTTP_204_NO_CONTENT
    assert not Plan.objects.filter(pk=plan.id).exists()


@pytest.mark.django_db
def test_subscription_delete_forbidden(api_client, super_admin, subscription):
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/subscriptions/{subscription.id}/")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_code(r) == "deactivate_instead"


@pytest.mark.django_db
def test_payment_gateway_delete_forbidden(api_client, super_admin, gateway):
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/payment-gateways/{gateway.id}/")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_code(r) == "disable_instead"


@pytest.mark.django_db
def test_guide_category_delete_blocked_with_articles(api_client, super_admin, db):
    cat = GuideCategory.objects.create(name_en="Cat", name_ar="فئة")
    GuideArticle.objects.create(
        title_en="T",
        title_ar="ع",
        body_en="b",
        body_ar="ن",
        category=cat,
    )
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/guide-categories/{cat.id}/")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_code(r) == "category_in_use"


@pytest.mark.django_db
def test_guide_category_delete_allowed_when_empty(api_client, super_admin, db):
    cat = GuideCategory.objects.create(name_en="Empty", name_ar="فارغ")
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/guide-categories/{cat.id}/")
    assert r.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_demo_booking_delete_blocked_for_confirmed(api_client, super_admin, db):
    from datetime import timedelta
    from django.utils import timezone

    starts = timezone.now() + timedelta(days=30)
    booking = DemoBooking.objects.create(
        name="Guest",
        email="g@test.com",
        phone="+9647000000001",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=30),
        status=DemoBookingStatus.CONFIRMED,
    )
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/demo-bookings/{booking.id}/")
    assert r.status_code == status.HTTP_409_CONFLICT
    assert _error_code(r) == "invalid_status"


@pytest.mark.django_db
def test_demo_booking_delete_allowed_for_pending(api_client, super_admin, db):
    from datetime import timedelta
    from django.utils import timezone

    starts = timezone.now() + timedelta(days=31)
    booking = DemoBooking.objects.create(
        name="Guest",
        email="g2@test.com",
        phone="+9647000000002",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=30),
        status=DemoBookingStatus.PENDING,
    )
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/demo-bookings/{booking.id}/")
    assert r.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_limited_admin_destroy_disables_user_login(api_client, super_admin, db):
    from accounts.models import LimitedAdmin

    target = User.objects.create_user(
        username="limited_target",
        email="limited_target@test.com",
        password="testpass123",
        is_staff=True,
    )
    la = LimitedAdmin.objects.create(user=target, is_active=True, created_by=super_admin)
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/v1/limited-admins/{la.id}/")
    assert r.status_code == status.HTTP_204_NO_CONTENT
    assert not LimitedAdmin.objects.filter(pk=la.id).exists()
    target.refresh_from_db()
    assert target.is_active is False
