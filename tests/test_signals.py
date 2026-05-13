"""
Tests for CRM signal callbacks and routing behavior.
"""
# pylint: disable=no-member

import pytest
from django.contrib.auth import get_user_model
from crm.models import Client, ClientEvent
from crm.signals import get_least_busy_employee
from integrations.models import IntegrationAccount, IntegrationPlatform


User = get_user_model()


@pytest.mark.django_db
def test_get_least_busy_employee(company):
    """Test standard round robin allocation query for get_least_busy_employee"""
    emp1 = User.objects.create_user(
        username="emp1", email="emp1@test.com", role="employee", company=company, is_active=True
    )
    emp2 = User.objects.create_user(
        username="emp2", email="emp2@test.com", role="employee", company=company, is_active=True
    )
    emp3 = User.objects.create_user(
        username="emp3", email="emp3@test.com", role="employee", company=company, is_active=True
    )

    # emp1 gets 2 clients
    Client.objects.create(name="c1", company=company, assigned_to=emp1)
    Client.objects.create(name="c2", company=company, assigned_to=emp1)

    # emp2 gets 1 client
    Client.objects.create(name="c3", company=company, assigned_to=emp2)

    # emp3 has 0
    least_busy = get_least_busy_employee(company)
    assert least_busy == emp3

    # Add 1 to emp3
    Client.objects.create(name="c4", company=company, assigned_to=emp3)

    least_busy = get_least_busy_employee(company)
    assert least_busy in (emp2, emp3)  # both have 1 now, query decides tie


@pytest.mark.django_db
def test_integration_lead_respects_auto_assign_toggle(company):
    """Integration-linked leads follow company.auto_assign_enabled like any other new lead."""
    emp = User.objects.create_user(
        username="integ_emp",
        email="integ_emp@test.com",
        role="employee",
        company=company,
        is_active=True,
    )
    account = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.TIKTOK,
        name="TikTok test",
        status="connected",
        external_account_id="test_signals_tiktok",
    )

    company.auto_assign_enabled = False
    company.save(update_fields=["auto_assign_enabled"])
    client_off = Client.objects.create(
        name="Lead from TikTok",
        priority="medium",
        type="fresh",
        company=company,
        source="tiktok",
        integration_account=account,
    )
    client_off.refresh_from_db()
    assert client_off.assigned_to_id is None

    company.auto_assign_enabled = True
    company.save(update_fields=["auto_assign_enabled"])
    client_on = Client.objects.create(
        name="Lead from TikTok 2",
        priority="medium",
        type="fresh",
        company=company,
        source="tiktok",
        integration_account=account,
    )
    client_on.refresh_from_db()
    assert client_on.assigned_to_id == emp.id
    ev = ClientEvent.objects.filter(client=client_on, event_type="assignment").first()
    assert ev is not None
    assert "TikTok" in (ev.notes or "")
