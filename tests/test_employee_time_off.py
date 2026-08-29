"""
Temporary employee unavailability: planned time off (date window) and the ad-hoc
"unavailable for N minutes" toggle.

Distinct from ``is_active=False`` deactivation (tests/test_employee_deactivation.py):
the user keeps login and keeps their leads, routing just skips them until they return.
"""
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from accounts.models import User
from conftest import api_body
from crm.assignment import get_arrival_assignee, get_least_busy_employee, has_assignable_employee
from crm.availability import (
    assignment_block_reason,
    user_accepts_new_assignments,
    user_is_on_time_off,
    user_is_temporarily_unavailable,
)
from crm.models import Client


def _error_payload(response):
    raw = getattr(response, "data", None)
    if raw is None:
        raw = json.loads(response.content.decode())
    return raw


def _make_employee(company, username, **kwargs):
    return User.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="x",
        company=company,
        role="employee",
        is_active=True,
        **kwargs,
    )


def _put_on_leave(user, *, days_before=0, days_after=0):
    """Leave window that contains today, in the company's local calendar."""
    today = timezone.now().date()
    user.time_off_start_date = today - timedelta(days=days_before)
    user.time_off_end_date = today + timedelta(days=days_after)
    user.save(update_fields=["time_off_start_date", "time_off_end_date"])
    return user


# ---------------------------------------------------------------- predicates


@pytest.mark.django_db
def test_today_inside_window_is_time_off(company, employee_user):
    _put_on_leave(employee_user, days_before=2, days_after=2)
    assert user_is_on_time_off(employee_user) is True
    assert user_accepts_new_assignments(employee_user) is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "days_before,days_after",
    [(0, 3), (3, 0), (0, 0)],  # today is the first day / the last day / the only day
)
def test_window_is_inclusive_on_both_ends(company, employee_user, days_before, days_after):
    _put_on_leave(employee_user, days_before=days_before, days_after=days_after)
    assert user_is_on_time_off(employee_user) is True


@pytest.mark.django_db
def test_past_window_expires_on_its_own(company, employee_user):
    today = timezone.now().date()
    employee_user.time_off_start_date = today - timedelta(days=10)
    employee_user.time_off_end_date = today - timedelta(days=3)
    employee_user.save(update_fields=["time_off_start_date", "time_off_end_date"])
    assert user_is_on_time_off(employee_user) is False
    assert user_accepts_new_assignments(employee_user) is True


@pytest.mark.django_db
def test_future_window_does_not_block_yet(company, employee_user):
    today = timezone.now().date()
    employee_user.time_off_start_date = today + timedelta(days=3)
    employee_user.time_off_end_date = today + timedelta(days=5)
    employee_user.save(update_fields=["time_off_start_date", "time_off_end_date"])
    assert user_accepts_new_assignments(employee_user) is True


@pytest.mark.django_db
def test_half_set_window_is_ignored(company, employee_user):
    """A start with no end must not be read as open-ended leave."""
    employee_user.time_off_start_date = timezone.now().date() - timedelta(days=1)
    employee_user.save(update_fields=["time_off_start_date"])
    assert user_is_on_time_off(employee_user) is False
    assert user_accepts_new_assignments(employee_user) is True


@pytest.mark.django_db
def test_unavailable_until_blocks_then_expires(company, employee_user):
    employee_user.unavailable_until = timezone.now() + timedelta(minutes=30)
    employee_user.save(update_fields=["unavailable_until"])
    assert user_is_temporarily_unavailable(employee_user) is True
    assert user_accepts_new_assignments(employee_user) is False

    employee_user.unavailable_until = timezone.now() - timedelta(minutes=1)
    employee_user.save(update_fields=["unavailable_until"])
    assert user_is_temporarily_unavailable(employee_user) is False
    assert user_accepts_new_assignments(employee_user) is True


@pytest.mark.django_db
def test_time_off_window_uses_company_timezone(company, employee_user):
    """Just past local midnight in Baghdad, "today" is already the first leave day."""
    company.timezone = "Asia/Baghdad"  # UTC+3
    company.save(update_fields=["timezone"])
    employee_user.refresh_from_db()

    local_today = timezone.now().astimezone(ZoneInfo("Asia/Baghdad")).date()
    employee_user.time_off_start_date = local_today
    employee_user.time_off_end_date = local_today
    employee_user.save(update_fields=["time_off_start_date", "time_off_end_date"])

    assert user_is_on_time_off(employee_user, company_for_calendar=company) is True


@pytest.mark.django_db
def test_time_off_reason_wins_over_weekly_day_off(monkeypatch, company, employee_user):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 3)
    employee_user.weekly_day_off = 3
    employee_user.save(update_fields=["weekly_day_off"])
    _put_on_leave(employee_user, days_after=4)

    assert assignment_block_reason(employee_user) == "time_off"


# ---------------------------------------------------------------- routing


@pytest.mark.django_db
def test_least_busy_skips_employee_on_time_off(company):
    emp_off = _make_employee(company, "off_emp")
    emp_on = _make_employee(company, "on_emp")
    _put_on_leave(emp_off, days_after=3)

    assert get_least_busy_employee(company) == emp_on


@pytest.mark.django_db
def test_least_busy_returns_none_when_everyone_is_off(company):
    for i in range(2):
        _put_on_leave(_make_employee(company, f"off{i}"), days_after=1)

    assert get_least_busy_employee(company) is None
    assert has_assignable_employee(company) is False


@pytest.mark.django_db
def test_arrival_routing_skips_employee_on_time_off(company):
    """Walk-in arrivals use a different predicate; it must inherit time off too."""
    emp_off = _make_employee(company, "arr_off")
    emp_on = _make_employee(company, "arr_on")
    _put_on_leave(emp_off, days_after=3)

    assert get_arrival_assignee(company) == emp_on


@pytest.mark.django_db
def test_unavailable_toggle_removes_employee_from_pool(company):
    emp_away = _make_employee(company, "away_emp")
    emp_on = _make_employee(company, "here_emp")
    emp_away.unavailable_until = timezone.now() + timedelta(hours=2)
    emp_away.save(update_fields=["unavailable_until"])

    assert get_least_busy_employee(company) == emp_on
    assert get_arrival_assignee(company) == emp_on


@pytest.mark.django_db
def test_employee_on_time_off_keeps_their_leads(company):
    """The chosen behaviour: leave changes routing only, never ownership."""
    emp = _make_employee(company, "keeps_leads")
    lead = Client.objects.create(name="Existing", company=company, assigned_to=emp)
    _put_on_leave(emp, days_after=5)

    lead.refresh_from_db()
    assert lead.assigned_to_id == emp.id
    emp.refresh_from_db()
    assert emp.is_active is True


# ---------------------------------------------------------------- manual assignment


@pytest.mark.django_db
def test_create_client_rejects_assignee_on_time_off(
    authenticated_admin, company, employee_user
):
    _put_on_leave(employee_user, days_after=2)

    response = authenticated_admin.post(
        "/api/v1/clients/",
        {
            "name": "Blocked",
            "priority": "low",
            "type": "cold",
            "company": company.id,
            "assigned_to": employee_user.id,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_payload(response)["error"]["code"] == "employee_time_off"


@pytest.mark.django_db
def test_patch_client_rejects_assignee_marked_unavailable(
    authenticated_admin, company, employee_user
):
    employee_user.unavailable_until = timezone.now() + timedelta(hours=1)
    employee_user.save(update_fields=["unavailable_until"])
    lead = Client.objects.create(name="Lead", company=company, priority="low", type="cold")

    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"assigned_to": employee_user.id},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_payload(response)["error"]["code"] == "employee_unavailable"


@pytest.mark.django_db
def test_patch_client_allows_same_assignee_during_time_off(
    authenticated_admin, company, employee_user
):
    """Editing other fields on a lead they already own must keep working."""
    lead = Client.objects.create(
        name="Lead", company=company, priority="low", type="cold", assigned_to=employee_user
    )
    _put_on_leave(employee_user, days_after=2)

    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"name": "Updated", "assigned_to": employee_user.id},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert api_body(response)["name"] == "Updated"


@pytest.mark.django_db
def test_bulk_assign_rejects_target_on_time_off(
    authenticated_admin, company, employee_user
):
    _put_on_leave(employee_user, days_after=1)
    lead = Client.objects.create(name="Z", company=company, priority="low", type="cold")

    response = authenticated_admin.post(
        "/api/v1/clients/bulk_assign/",
        {"client_ids": [lead.id], "user_id": employee_user.id},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_payload(response)["error"]["code"] == "employee_time_off"


# ---------------------------------------------------------------- API: planned leave


def _user_url(user):
    return f"/api/v1/users/{user.id}/"


@pytest.mark.django_db
def test_admin_sets_time_off_window(authenticated_admin, employee_user):
    start = date.today() + timedelta(days=3)
    end = start + timedelta(days=4)

    response = authenticated_admin.patch(
        _user_url(employee_user),
        {"time_off_start_date": start.isoformat(), "time_off_end_date": end.isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    employee_user.refresh_from_db()
    assert employee_user.time_off_start_date == start
    assert employee_user.time_off_end_date == end


@pytest.mark.django_db
def test_admin_clears_time_off_window(authenticated_admin, employee_user):
    _put_on_leave(employee_user, days_after=3)

    response = authenticated_admin.patch(
        _user_url(employee_user),
        {"time_off_start_date": None, "time_off_end_date": None},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    employee_user.refresh_from_db()
    assert employee_user.time_off_start_date is None
    assert employee_user.time_off_end_date is None
    assert user_accepts_new_assignments(employee_user) is True


@pytest.mark.django_db
def test_half_set_window_is_rejected(authenticated_admin, employee_user):
    response = authenticated_admin.patch(
        _user_url(employee_user),
        {"time_off_start_date": date.today().isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    employee_user.refresh_from_db()
    assert employee_user.time_off_start_date is None


@pytest.mark.django_db
def test_end_before_start_is_rejected(authenticated_admin, employee_user):
    start = date.today() + timedelta(days=5)

    response = authenticated_admin.patch(
        _user_url(employee_user),
        {
            "time_off_start_date": start.isoformat(),
            "time_off_end_date": (start - timedelta(days=1)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    employee_user.refresh_from_db()
    assert employee_user.time_off_start_date is None


@pytest.mark.django_db
def test_employee_cannot_set_own_time_off(authenticated_employee, employee_user):
    """Self-service is deliberately closed: only user managers schedule leave."""
    start = date.today() + timedelta(days=1)

    response = authenticated_employee.patch(
        _user_url(employee_user),
        {
            "time_off_start_date": start.isoformat(),
            "time_off_end_date": (start + timedelta(days=2)).isoformat(),
        },
        format="json",
    )

    employee_user.refresh_from_db()
    assert employee_user.time_off_start_date is None


@pytest.mark.django_db
def test_user_payload_reports_availability(authenticated_admin, employee_user):
    _put_on_leave(employee_user, days_after=2)

    data = api_body(authenticated_admin.get(_user_url(employee_user)))

    assert data["availability"]["accepts_new_assignments"] is False
    assert data["availability"]["reason"] == "time_off"
    assert data["availability"]["until"] == employee_user.time_off_end_date.isoformat()


# ---------------------------------------------------------------- API: quick toggle


def _availability_url(user):
    return f"/api/v1/users/{user.id}/availability/"


@pytest.mark.django_db
def test_admin_marks_employee_unavailable_for_a_duration(authenticated_admin, employee_user):
    before = timezone.now()

    response = authenticated_admin.post(
        _availability_url(employee_user), {"duration_minutes": 60}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    employee_user.refresh_from_db()
    assert employee_user.unavailable_until is not None
    # Server-computed from the duration, so a skewed client clock buys nothing.
    delta = employee_user.unavailable_until - before
    assert timedelta(minutes=59) < delta <= timedelta(minutes=61)
    assert api_body(response)["user"]["availability"]["reason"] == "unavailable"


@pytest.mark.django_db
def test_admin_marks_employee_available_again(authenticated_admin, employee_user):
    employee_user.unavailable_until = timezone.now() + timedelta(hours=3)
    employee_user.save(update_fields=["unavailable_until"])

    response = authenticated_admin.post(
        _availability_url(employee_user), {"duration_minutes": 0}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    employee_user.refresh_from_db()
    assert employee_user.unavailable_until is None
    assert api_body(response)["user"]["availability"]["accepts_new_assignments"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("minutes", [-5, 24 * 60 + 1, "soon"])
def test_invalid_duration_is_rejected(authenticated_admin, employee_user, minutes):
    response = authenticated_admin.post(
        _availability_url(employee_user), {"duration_minutes": minutes}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    employee_user.refresh_from_db()
    assert employee_user.unavailable_until is None


@pytest.mark.django_db
def test_employee_cannot_toggle_own_availability(authenticated_employee, employee_user):
    response = authenticated_employee.post(
        _availability_url(employee_user), {"duration_minutes": 60}, format="json"
    )

    assert response.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )
    employee_user.refresh_from_db()
    assert employee_user.unavailable_until is None


@pytest.mark.django_db
def test_cannot_toggle_availability_across_companies(
    authenticated_admin, other_company, other_admin_user
):
    response = authenticated_admin.post(
        _availability_url(other_admin_user), {"duration_minutes": 60}, format="json"
    )

    assert response.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )
    other_admin_user.refresh_from_db()
    assert other_admin_user.unavailable_until is None


@pytest.mark.django_db
def test_unavailable_toggle_does_not_deactivate_or_clear_push_tokens(
    authenticated_admin, employee_user
):
    """The whole point of the toggle: the user stays logged in and reachable."""
    employee_user.fcm_tokens = ["token-1"]
    employee_user.save(update_fields=["fcm_tokens"])

    authenticated_admin.post(
        _availability_url(employee_user), {"duration_minutes": 120}, format="json"
    )

    employee_user.refresh_from_db()
    assert employee_user.is_active is True
    assert employee_user.fcm_tokens == ["token-1"]
