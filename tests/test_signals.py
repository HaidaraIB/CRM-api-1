"""
Tests for CRM signal callbacks and routing behavior.
"""
# pylint: disable=no-member

import pytest
from django.contrib.auth import get_user_model
from crm.models import Client
from crm.signals import get_least_busy_employee


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
