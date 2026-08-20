"""
Tests for measured CRM usage time ("actual working hours").

Arithmetic cases call ``credit_work_time`` directly with an explicit ``now`` so no
clock mocking is needed; wiring/permission cases go through the URLs.
"""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from accounts.models import User, WorkDaySummary
from accounts.work_tracking import MAX_CREDIT_SECONDS, credit_work_time
from conftest import api_body


@pytest.fixture
def tracking_company(company):
    company.work_hours_tracking_enabled = True
    company.work_hours_idle_timeout_minutes = 10
    company.save(update_fields=[
        "work_hours_tracking_enabled",
        "work_hours_idle_timeout_minutes",
    ])
    return company


@pytest.fixture
def tracked_employee(tracking_company, employee_user):
    """An employee in a company that has tracking switched on."""
    employee_user.refresh_from_db()
    return employee_user


def _seed_cursor(user, seconds_ago):
    """Plant the crediting cursor as if a ping happened ``seconds_ago``."""
    now = timezone.now()
    user.work_last_ping_at = now - timedelta(seconds=seconds_ago)
    user.save(update_fields=["work_last_ping_at"])
    return now


# ---------------------------------------------------------------- crediting


@pytest.mark.django_db
def test_first_ping_credits_nothing_and_sets_cursor(tracked_employee):
    result = credit_work_time(tracked_employee, source="web")

    assert result["tracking_enabled"] is True
    assert result["credited_seconds"] == 0
    tracked_employee.refresh_from_db()
    assert tracked_employee.work_last_ping_at is not None
    # Bootstrap must not invent a day row: we have no idea how long the tab was open.
    assert not WorkDaySummary.objects.filter(user=tracked_employee).exists()


@pytest.mark.django_db
def test_second_ping_credits_elapsed_seconds(tracked_employee):
    _seed_cursor(tracked_employee, seconds_ago=60)

    result = credit_work_time(tracked_employee, source="web")

    assert result["credited_seconds"] == 60
    assert result["today_seconds"] == 60
    row = WorkDaySummary.objects.get(user=tracked_employee)
    assert row.active_seconds == 60
    assert row.web_seconds == 60
    assert row.mobile_seconds == 0
    assert row.ping_count == 1


@pytest.mark.django_db
def test_mobile_ping_credits_mobile_bucket(tracked_employee):
    _seed_cursor(tracked_employee, seconds_ago=60)

    credit_work_time(tracked_employee, source="mobile")

    row = WorkDaySummary.objects.get(user=tracked_employee)
    assert row.active_seconds == 60
    assert row.mobile_seconds == 60
    assert row.web_seconds == 0


@pytest.mark.django_db
def test_credit_is_capped(tracked_employee, tracking_company):
    # Idle timeout raised so the gap is "not idle" but is still far beyond one interval,
    # which is exactly the case the cap exists for.
    tracking_company.work_hours_idle_timeout_minutes = 60
    tracking_company.save(update_fields=["work_hours_idle_timeout_minutes"])
    tracked_employee.refresh_from_db()
    _seed_cursor(tracked_employee, seconds_ago=45 * 60)

    result = credit_work_time(tracked_employee, source="web")

    assert result["credited_seconds"] == MAX_CREDIT_SECONDS
    assert WorkDaySummary.objects.get(user=tracked_employee).active_seconds == MAX_CREDIT_SECONDS


@pytest.mark.django_db
def test_gap_beyond_idle_timeout_credits_zero(tracked_employee):
    _seed_cursor(tracked_employee, seconds_ago=15 * 60)  # idle timeout is 10 min

    result = credit_work_time(tracked_employee, source="web")

    assert result["credited_seconds"] == 0
    row = WorkDaySummary.objects.get(user=tracked_employee)
    assert row.active_seconds == 0
    assert row.idle_pause_count == 1


@pytest.mark.django_db
def test_backwards_clock_credits_zero(tracked_employee):
    tracked_employee.work_last_ping_at = timezone.now() + timedelta(hours=1)
    tracked_employee.save(update_fields=["work_last_ping_at"])

    result = credit_work_time(tracked_employee, source="web")

    assert result["credited_seconds"] == 0
    assert WorkDaySummary.objects.get(user=tracked_employee).active_seconds == 0


@pytest.mark.django_db
def test_concurrent_pings_do_not_double_credit(tracked_employee):
    """Two clients observing the same cursor must not both credit the interval."""
    _seed_cursor(tracked_employee, seconds_ago=60)

    # Two in-memory copies of the same row, both holding the pre-ping cursor.
    first = User.objects.get(pk=tracked_employee.pk)
    second = User.objects.get(pk=tracked_employee.pk)

    a = credit_work_time(first, source="web")
    b = credit_work_time(second, source="mobile")

    assert a["credited_seconds"] == 60
    assert b["credited_seconds"] == 0  # lost the conditional UPDATE race
    assert WorkDaySummary.objects.get(user=tracked_employee).active_seconds == 60


# ---------------------------------------------------------------- timezone


@pytest.mark.django_db
def test_day_bucketed_in_company_timezone(tracked_employee, tracking_company):
    tracking_company.timezone = "Asia/Baghdad"  # UTC+3
    tracking_company.save(update_fields=["timezone"])
    tracked_employee.refresh_from_db()

    # 22:00 UTC on the 20th is 01:00 local on the 21st.
    now = datetime(2026, 8, 20, 22, 0, 0, tzinfo=dt_timezone.utc)
    tracked_employee.work_last_ping_at = now - timedelta(seconds=60)
    tracked_employee.save(update_fields=["work_last_ping_at"])

    result = credit_work_time(tracked_employee, source="web", now=now)

    assert result["work_date"] == date(2026, 8, 21)
    row = WorkDaySummary.objects.get(user=tracked_employee)
    assert row.work_date == date(2026, 8, 21)
    assert row.active_seconds == 60


@pytest.mark.django_db
def test_session_straddling_local_midnight_splits_across_two_days(
    tracked_employee, tracking_company
):
    tracking_company.timezone = "Asia/Baghdad"  # UTC+3, so local midnight = 21:00 UTC
    tracking_company.save(update_fields=["timezone"])
    tracked_employee.refresh_from_db()

    # Window spans 23:59:30 -> 00:00:30 local.
    now = datetime(2026, 8, 20, 21, 0, 30, tzinfo=dt_timezone.utc)
    tracked_employee.work_last_ping_at = now - timedelta(seconds=60)
    tracked_employee.save(update_fields=["work_last_ping_at"])

    result = credit_work_time(tracked_employee, source="web", now=now)

    assert result["credited_seconds"] == 60
    rows = {r.work_date: r.active_seconds for r in WorkDaySummary.objects.filter(user=tracked_employee)}
    assert rows == {date(2026, 8, 20): 30, date(2026, 8, 21): 30}
    assert sum(rows.values()) == result["credited_seconds"]


# ---------------------------------------------------------------- gating


@pytest.mark.django_db
def test_ping_noop_when_tracking_disabled(api_client, employee_user, subscription):
    api_client.force_authenticate(user=employee_user)  # company default is OFF

    response = api_client.post(reverse("work_session_ping"), {"source": "web"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data["tracking_enabled"] is False
    assert data["reason"] == "tracking_disabled"
    assert not WorkDaySummary.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role",
    ["admin", "supervisor", "employee", "data_entry", "reception", "doctor", "call_center"],
)
def test_every_company_role_accrues(api_client, tracking_company, subscription, role):
    """All company roles are tracked, owners/admins included."""
    staff = User.objects.create_user(
        username=f"tracked_{role}",
        email=f"tracked_{role}@test.com",
        password="testpass123",
        company=tracking_company,
        role=role,
    )
    _seed_cursor(staff, seconds_ago=60)
    api_client.force_authenticate(user=staff)

    response = api_client.post(reverse("work_session_ping"), {"source": "web"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data["tracking_enabled"] is True
    assert data["credited_seconds"] == 60
    assert WorkDaySummary.objects.get(user=staff).active_seconds == 60


@pytest.mark.django_db
def test_super_admin_does_not_accrue(api_client, tracking_company, subscription):
    """The platform operator is not company staff and has no hours to attribute."""
    platform_user = User.objects.create_user(
        username="platform_operator",
        email="platform@test.com",
        password="testpass123",
        company=tracking_company,
        role="super_admin",
    )
    api_client.force_authenticate(user=platform_user)

    response = api_client.post(reverse("work_session_ping"), {"source": "web"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data["tracking_enabled"] is False
    assert data["reason"] == "role_not_tracked"
    platform_user.refresh_from_db()
    assert platform_user.work_last_ping_at is None


@pytest.mark.django_db
def test_impersonated_session_does_not_accrue(api_client, tracked_employee, subscription):
    _seed_cursor(tracked_employee, seconds_ago=60)
    # is_impersonating() reads the "impersonation" claim off request.auth.
    api_client.force_authenticate(user=tracked_employee, token={"impersonation": True})

    response = api_client.post(reverse("work_session_ping"), {"source": "web"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert api_body(response)["reason"] == "impersonation"
    assert not WorkDaySummary.objects.exists()


@pytest.mark.django_db
def test_client_supplied_duration_is_ignored(api_client, tracked_employee, subscription):
    """The core anti-inflation property: a forged duration must buy nothing."""
    _seed_cursor(tracked_employee, seconds_ago=60)
    api_client.force_authenticate(user=tracked_employee)

    response = api_client.post(
        reverse("work_session_ping"),
        {"source": "web", "seconds": 28800, "credited_seconds": 28800},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert api_body(response)["credited_seconds"] <= MAX_CREDIT_SECONDS
    assert WorkDaySummary.objects.get(user=tracked_employee).active_seconds <= MAX_CREDIT_SECONDS


@pytest.mark.django_db
def test_ping_requires_authentication(api_client):
    response = api_client.post(reverse("work_session_ping"), {"source": "web"}, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_today_endpoint_returns_only_own_hours(
    api_client, tracking_company, tracked_employee, admin_user, subscription
):
    now = timezone.now()
    WorkDaySummary.objects.create(
        company=tracking_company,
        user=admin_user,
        work_date=now.date(),
        active_seconds=9999,
        first_activity_at=now,
        last_activity_at=now,
    )
    api_client.force_authenticate(user=tracked_employee)

    response = api_client.get(reverse("work_session_today"))

    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data["today_seconds"] == 0
    assert data["tracking_enabled"] is True
    assert data["idle_timeout_minutes"] == 10


# ------------------------------------------------- company summary (Employees page)


@pytest.mark.django_db
def test_summary_returns_today_and_range_per_user(
    api_client, tracking_company, admin_user, employee_user, subscription
):
    today = timezone.now().date()
    _seed_hours(tracking_company, employee_user, 3600, day=today)
    _seed_hours(tracking_company, employee_user, 1800, day=today - timedelta(days=2))
    _seed_hours(tracking_company, admin_user, 900, day=today)
    api_client.force_authenticate(user=admin_user)

    response = api_client.get(reverse("work_session_summary"), {"days": 7})

    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data["tracking_enabled"] is True
    assert data["days"] == 7
    by_user = {row["user_id"]: row for row in data["users"]}
    assert by_user[employee_user.id]["today_seconds"] == 3600
    assert by_user[employee_user.id]["range_seconds"] == 5400
    # Admins are tracked too, so the owner appears in the team summary.
    assert by_user[admin_user.id]["today_seconds"] == 900


@pytest.mark.django_db
def test_summary_range_window_excludes_older_days(
    api_client, tracking_company, admin_user, employee_user, subscription
):
    today = timezone.now().date()
    _seed_hours(tracking_company, employee_user, 3600, day=today)
    _seed_hours(tracking_company, employee_user, 1800, day=today - timedelta(days=10))
    api_client.force_authenticate(user=admin_user)

    data = api_body(api_client.get(reverse("work_session_summary"), {"days": 7}))

    row = next(r for r in data["users"] if r["user_id"] == employee_user.id)
    assert row["range_seconds"] == 3600


@pytest.mark.django_db
def test_summary_is_tenant_isolated(
    api_client, tracking_company, admin_user, employee_user,
    other_company, other_admin_user, subscription,
):
    _seed_hours(tracking_company, employee_user, 3600)
    _seed_hours(other_company, other_admin_user, 9999)
    api_client.force_authenticate(user=admin_user)

    data = api_body(api_client.get(reverse("work_session_summary")))

    assert other_admin_user.id not in [row["user_id"] for row in data["users"]]


@pytest.mark.django_db
def test_summary_forbidden_for_plain_employee(authenticated_employee, tracking_company):
    """Employees see only their own total, via work-sessions/today/."""
    response = authenticated_employee.get(reverse("work_session_summary"))
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_summary_reports_disabled_without_querying(authenticated_admin, company):
    """Company default is OFF — the page must be told, not handed empty data."""
    data = api_body(authenticated_admin.get(reverse("work_session_summary")))

    assert data["tracking_enabled"] is False
    assert data["users"] == []


# ---------------------------------------------------------------- report


def _seed_hours(company, user, seconds, day=None):
    day = day or timezone.now().date()
    now = timezone.now()
    return WorkDaySummary.objects.create(
        company=company,
        user=user,
        work_date=day,
        active_seconds=seconds,
        web_seconds=seconds,
        first_activity_at=now,
        last_activity_at=now,
    )


@pytest.mark.django_db
def test_employee_report_includes_worked_seconds(
    authenticated_admin, tracking_company, employee_user
):
    today = timezone.now().date()
    _seed_hours(tracking_company, employee_user, 3600, day=today)
    _seed_hours(tracking_company, employee_user, 1800, day=today - timedelta(days=1))

    response = authenticated_admin.get(reverse("reports_employees"))

    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    row = next(r for r in data["rows"] if r["id"] == employee_user.id)
    assert row["worked_seconds"] == 5400
    assert data["summary"]["total_worked_seconds"] == 5400


@pytest.mark.django_db
def test_employee_report_row_kept_when_only_hours(
    authenticated_admin, tracking_company, employee_user
):
    """Regression guard: hours alone must keep a user in the report."""
    _seed_hours(tracking_company, employee_user, 7200)

    data = api_body(authenticated_admin.get(reverse("reports_employees")))

    ids = [r["id"] for r in data["rows"]]
    assert employee_user.id in ids


@pytest.mark.django_db
def test_employee_report_hours_scoped_to_date_range(
    authenticated_admin, tracking_company, employee_user
):
    today = timezone.now().date()
    _seed_hours(tracking_company, employee_user, 3600, day=today)
    _seed_hours(tracking_company, employee_user, 1800, day=today - timedelta(days=30))

    data = api_body(
        authenticated_admin.get(
            reverse("reports_employees"),
            {"from": today.isoformat(), "to": today.isoformat()},
        )
    )

    row = next(r for r in data["rows"] if r["id"] == employee_user.id)
    assert row["worked_seconds"] == 3600


@pytest.mark.django_db
def test_employee_report_hours_are_tenant_isolated(
    authenticated_admin, tracking_company, employee_user, other_company, other_admin_user
):
    _seed_hours(tracking_company, employee_user, 3600)
    _seed_hours(other_company, other_admin_user, 9999)

    data = api_body(authenticated_admin.get(reverse("reports_employees")))

    assert data["summary"]["total_worked_seconds"] == 3600
    assert other_admin_user.id not in [r["id"] for r in data["rows"]]


@pytest.mark.django_db
def test_employee_report_requires_report_permission(authenticated_employee):
    response = authenticated_employee.get(reverse("reports_employees"))
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------- settings


def _settings_url(company):
    return reverse("company-update-assignment-settings", args=[company.id])


@pytest.mark.django_db
def test_update_work_hours_settings_persists(authenticated_admin, company):
    response = authenticated_admin.patch(
        _settings_url(company),
        {"work_hours_tracking_enabled": True, "work_hours_idle_timeout_minutes": 15},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    company.refresh_from_db()
    assert company.work_hours_tracking_enabled is True
    assert company.work_hours_idle_timeout_minutes == 15


@pytest.mark.django_db
@pytest.mark.parametrize(
    "minutes,expected",
    [(0, status.HTTP_400_BAD_REQUEST), (121, status.HTTP_400_BAD_REQUEST),
     (1, status.HTTP_200_OK), (120, status.HTTP_200_OK)],
)
def test_idle_timeout_validation_bounds(authenticated_admin, company, minutes, expected):
    response = authenticated_admin.patch(
        _settings_url(company),
        {"work_hours_idle_timeout_minutes": minutes},
        format="json",
    )
    assert response.status_code == expected
    if expected == status.HTTP_400_BAD_REQUEST:
        assert "invalid_work_hours_idle_timeout_minutes" in str(response.data)


@pytest.mark.django_db
def test_non_admin_cannot_update_work_hours_settings(authenticated_employee, company):
    response = authenticated_employee.patch(
        _settings_url(company),
        {"work_hours_tracking_enabled": True},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    company.refresh_from_db()
    assert company.work_hours_tracking_enabled is False


@pytest.mark.django_db
def test_me_endpoint_exposes_work_hours_settings(authenticated_admin, tracking_company):
    """Guards the multi-place serializer wiring the web client depends on."""
    data = api_body(authenticated_admin.get(reverse("user-me")))

    assert data["company"]["work_hours_tracking_enabled"] is True
    assert data["company"]["work_hours_idle_timeout_minutes"] == 10
