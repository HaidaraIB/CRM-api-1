"""
Platform admin GET /api/v1/companies/dashboard-summary/
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status

from companies.models import Company
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

User = get_user_model()


@pytest.fixture
def super_admin(db):
    return User.objects.create_superuser(
        username="platform_dash_admin",
        email="platform_dash_admin@test.com",
        password="securepassword123",
    )


@pytest.fixture
def gateway(db):
    return PaymentGateway.objects.create(
        name="Platform Dash GW",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
    )


def _make_company(i: int) -> Company:
    owner = User.objects.create_user(
        username=f"pd_owner_{i}",
        email=f"pd_owner_{i}@test.com",
        password="testpass123",
        role="admin",
        email_verified=True,
        phone_verified=True,
    )
    company = Company.objects.create(
        name=f"Platform Co {i:03d}",
        domain=f"platform-co-{i}.example.com",
        owner=owner,
    )
    owner.company = company
    owner.save(update_fields=["company"])
    return company


def _seed_beyond_page_size(gateway):
    """25 companies / active subs / successful payments (past page size of 20)."""
    now = timezone.now()
    plan_a = Plan.objects.create(
        name="Starter",
        name_ar="أساسي",
        description="a",
        price_monthly=Decimal("10.00"),
        price_yearly=Decimal("100.00"),
    )
    plan_b = Plan.objects.create(
        name="Growth",
        name_ar="نمو",
        description="b",
        price_monthly=Decimal("50.00"),
        price_yearly=Decimal("500.00"),
    )
    companies = []
    for i in range(25):
        company = _make_company(i)
        companies.append(company)
        plan = plan_a if i < 15 else plan_b
        sub = Subscription.objects.create(
            company=company,
            plan=plan,
            is_active=True,
            end_date=now + timedelta(days=3 if i < 5 else 60),
            current_period_start=now - timedelta(days=10),
            billing_cycle=BillingCycle.MONTHLY,
        )
        # Backdate subscription created_at into the last 30 days for new_subscriptions KPI
        Subscription.objects.filter(pk=sub.pk).update(created_at=now - timedelta(days=i % 20))
        pay = Payment.objects.create(
            subscription=sub,
            amount=Decimal("10.00"),
            currency="USD",
            amount_usd=Decimal("10.00"),
            payment_method=gateway,
            payment_status=PaymentStatus.COMPLETED.value,
        )
        Payment.objects.filter(pk=pay.pk).update(created_at=now - timedelta(days=i % 15))

    # One pending payment that must not count toward MRR / revenue
    Payment.objects.create(
        subscription=Subscription.objects.filter(company=companies[0]).first(),
        amount=Decimal("999.00"),
        currency="USD",
        amount_usd=Decimal("999.00"),
        payment_method=gateway,
        payment_status=PaymentStatus.PENDING.value,
    )
    return plan_a, plan_b, companies


@pytest.mark.django_db
def test_platform_dashboard_summary_empty(api_client, super_admin):
    api_client.force_authenticate(user=super_admin)
    response = api_client.get("/api/v1/companies/dashboard-summary/")
    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data["mrr"] == 0
    assert data["active_tenants"] == 0
    assert data["new_subscriptions"] == 0
    assert data["expiring_subscriptions"] == 0
    assert data["recent_companies"] == []
    assert data["recent_payments"] == []
    assert isinstance(data["revenue_by_month"], list)
    assert isinstance(data["plan_distribution"], list)


@pytest.mark.django_db
def test_platform_dashboard_summary_beyond_page_size(api_client, super_admin, gateway):
    plan_a, plan_b, _companies = _seed_beyond_page_size(gateway)
    api_client.force_authenticate(user=super_admin)

    today = timezone.localdate()
    start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    # Walk back ~11 months for a wide window
    for _ in range(10):
        start = (start - timedelta(days=1)).replace(day=1)

    response = api_client.get(
        "/api/v1/companies/dashboard-summary/",
        {"start": start.isoformat(), "end": today.isoformat()},
    )
    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)

    # All 25 successful $10 payments fall in last 30 days → MRR = 250
    assert data["mrr"] == 250.0
    assert data["active_tenants"] == 25
    assert data["new_subscriptions"] == 25
    # First 5 subs expire in 3 days
    assert data["expiring_subscriptions"] == 5

    starter = next(p for p in data["plan_distribution"] if p["plan_id"] == plan_a.id)
    growth = next(p for p in data["plan_distribution"] if p["plan_id"] == plan_b.id)
    assert starter["count"] == 15
    assert growth["count"] == 10

    assert len(data["recent_companies"]) == 5
    assert len(data["recent_payments"]) == 5
    assert sum(m["revenue"] for m in data["revenue_by_month"]) == 250.0


@pytest.mark.django_db
def test_platform_dashboard_summary_date_range_filters_revenue(
    api_client, super_admin, gateway, company, plan
):
    now = timezone.now()
    sub = Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        end_date=now + timedelta(days=30),
        billing_cycle=BillingCycle.MONTHLY,
    )
    in_range = Payment.objects.create(
        subscription=sub,
        amount=Decimal("40.00"),
        currency="USD",
        amount_usd=Decimal("40.00"),
        payment_method=gateway,
        payment_status=PaymentStatus.COMPLETED.value,
    )
    out_of_range = Payment.objects.create(
        subscription=sub,
        amount=Decimal("60.00"),
        currency="USD",
        amount_usd=Decimal("60.00"),
        payment_method=gateway,
        payment_status=PaymentStatus.COMPLETED.value,
    )
    Payment.objects.filter(pk=in_range.pk).update(created_at=now - timedelta(days=5))
    Payment.objects.filter(pk=out_of_range.pk).update(created_at=now - timedelta(days=90))

    api_client.force_authenticate(user=super_admin)
    end = timezone.localdate()
    start = end - timedelta(days=30)
    response = api_client.get(
        "/api/v1/companies/dashboard-summary/",
        {"start": start.isoformat(), "end": end.isoformat()},
    )
    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert sum(m["revenue"] for m in data["revenue_by_month"]) == 40.0
    # MRR is last-30-days successful payments (independent of chart range params for out-of-window)
    assert data["mrr"] == 40.0


@pytest.mark.django_db
def test_platform_dashboard_summary_forbidden_for_tenant_admin(api_client, admin_user):
    api_client.force_authenticate(user=admin_user)
    response = api_client.get("/api/v1/companies/dashboard-summary/")
    assert response.status_code == status.HTTP_403_FORBIDDEN
