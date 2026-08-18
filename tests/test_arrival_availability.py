"""Tests for user_is_on_shift_or_unscheduled — the walk-in-arrival availability predicate."""
import datetime

import pytest

from crm.availability import user_is_on_shift_or_unscheduled


@pytest.mark.django_db
class TestUserIsOnShiftOrUnscheduled:
    def test_unset_hours_means_available(self, employee_user, company):
        employee_user.work_start_time = None
        employee_user.work_end_time = None
        employee_user.weekly_day_off = None
        employee_user.save(update_fields=["work_start_time", "work_end_time", "weekly_day_off"])
        assert user_is_on_shift_or_unscheduled(employee_user, company_for_calendar=company) is True

    def test_unset_hours_but_on_weekly_day_off_means_unavailable(self, employee_user, company, monkeypatch):
        from crm import availability as availability_module

        employee_user.work_start_time = None
        employee_user.work_end_time = None
        employee_user.weekly_day_off = 2  # Wednesday
        employee_user.save(update_fields=["work_start_time", "work_end_time", "weekly_day_off"])
        monkeypatch.setattr(availability_module, "local_today_weekday", lambda c: 2)
        assert user_is_on_shift_or_unscheduled(employee_user, company_for_calendar=company) is False

    def test_configured_hours_inside_window(self, employee_user, company, monkeypatch):
        from crm import availability as availability_module

        employee_user.work_start_time = datetime.time(9, 0)
        employee_user.work_end_time = datetime.time(17, 0)
        employee_user.weekly_day_off = None
        employee_user.save(update_fields=["work_start_time", "work_end_time", "weekly_day_off"])

        class _FakeNow:
            def time(self):
                return datetime.time(12, 0)

            def date(self):
                return datetime.date(2026, 1, 5)  # Monday

        monkeypatch.setattr(availability_module, "local_now_for_company", lambda c: _FakeNow())
        assert user_is_on_shift_or_unscheduled(employee_user, company_for_calendar=company) is True

    def test_configured_hours_outside_window(self, employee_user, company, monkeypatch):
        from crm import availability as availability_module

        employee_user.work_start_time = datetime.time(9, 0)
        employee_user.work_end_time = datetime.time(17, 0)
        employee_user.weekly_day_off = None
        employee_user.save(update_fields=["work_start_time", "work_end_time", "weekly_day_off"])

        class _FakeNow:
            def time(self):
                return datetime.time(20, 0)

            def date(self):
                return datetime.date(2026, 1, 5)

        monkeypatch.setattr(availability_module, "local_now_for_company", lambda c: _FakeNow())
        assert user_is_on_shift_or_unscheduled(employee_user, company_for_calendar=company) is False

    def test_overnight_window_wraps_correctly(self, employee_user, company, monkeypatch):
        from crm import availability as availability_module

        employee_user.work_start_time = datetime.time(22, 0)
        employee_user.work_end_time = datetime.time(6, 0)
        employee_user.weekly_day_off = None
        employee_user.save(update_fields=["work_start_time", "work_end_time", "weekly_day_off"])

        class _FakeNow:
            def time(self):
                return datetime.time(23, 30)

            def date(self):
                return datetime.date(2026, 1, 5)

        monkeypatch.setattr(availability_module, "local_now_for_company", lambda c: _FakeNow())
        assert user_is_on_shift_or_unscheduled(employee_user, company_for_calendar=company) is True

    def test_non_utc_company_timezone_does_not_error(self, employee_user, company):
        company.timezone = "Asia/Baghdad"
        company.save(update_fields=["timezone"])
        employee_user.work_start_time = None
        employee_user.work_end_time = None
        employee_user.save(update_fields=["work_start_time", "work_end_time"])
        # Should not raise regardless of the real current time.
        assert user_is_on_shift_or_unscheduled(employee_user, company_for_calendar=company) in (True, False)

    def test_none_user_is_false(self, company):
        assert user_is_on_shift_or_unscheduled(None, company_for_calendar=company) is False
