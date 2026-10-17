"""Business-time engine: shifts, overnight windows, days off, company-hours fallback, DST."""
from datetime import date, datetime, time, timezone as dt_tz
from zoneinfo import ZoneInfo

import pytest

from reports.business_time import business_seconds_between


class _Co:
    def __init__(self, tz="UTC", hours=None):
        self.timezone = tz
        self.business_hours = hours or {}


class _User:
    def __init__(self, start=None, end=None, day_off=None, off_start=None, off_end=None):
        self.work_start_time = start
        self.work_end_time = end
        self.weekly_day_off = day_off
        self.time_off_start_date = off_start
        self.time_off_end_date = off_end


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=dt_tz.utc)


# 2026-10-05 is a Monday.
def test_no_calendar_returns_raw_elapsed():
    assert business_seconds_between(utc(2026, 10, 5, 22), utc(2026, 10, 6, 2), _User(), _Co()) == 4 * 3600


def test_assigned_overnight_answered_next_morning_counts_only_shift():
    user = _User(time(9), time(17))
    # Mon 23:00 -> Tue 09:05: only 5 minutes inside Tuesday's shift.
    assert business_seconds_between(utc(2026, 10, 5, 23), utc(2026, 10, 6, 9, 5), user, _Co()) == 5 * 60


def test_span_across_whole_days_sums_each_shift():
    user = _User(time(9), time(17))
    # Mon 12:00 -> Wed 10:00 = 5h (Mon) + 8h (Tue) + 1h (Wed)
    assert business_seconds_between(utc(2026, 10, 5, 12), utc(2026, 10, 7, 10), user, _Co()) == 14 * 3600


def test_weekly_day_off_and_time_off_are_skipped():
    user = _User(time(9), time(17), day_off=1)  # Tuesday off
    assert business_seconds_between(utc(2026, 10, 5, 12), utc(2026, 10, 7, 10), user, _Co()) == 6 * 3600
    on_leave = _User(time(9), time(17), off_start=date(2026, 10, 6), off_end=date(2026, 10, 6))
    assert business_seconds_between(utc(2026, 10, 5, 12), utc(2026, 10, 7, 10), on_leave, _Co()) == 6 * 3600


def test_overnight_shift_is_split_across_midnight():
    user = _User(time(22), time(6))
    assert business_seconds_between(utc(2026, 10, 5, 21), utc(2026, 10, 6, 7), user, _Co()) == 8 * 3600


def test_company_hours_used_when_employee_has_no_shift():
    co = _Co(hours={str(i): {"open": "10:00", "close": "14:00"} for i in range(5)})
    assert business_seconds_between(utc(2026, 10, 5, 8), utc(2026, 10, 5, 16), _User(), co) == 4 * 3600
    # Saturday is closed (no entry): zero business time.
    assert business_seconds_between(utc(2026, 10, 10, 8), utc(2026, 10, 10, 16), _User(), co) == 0


def test_employee_shift_wins_over_company_hours():
    co = _Co(hours={str(i): {"open": "10:00", "close": "14:00"} for i in range(7)})
    user = _User(time(9), time(17))
    assert business_seconds_between(utc(2026, 10, 5, 8), utc(2026, 10, 5, 18), user, co) == 8 * 3600


def test_window_is_evaluated_in_company_timezone():
    co = _Co(tz="Asia/Baghdad")  # UTC+3, no DST
    user = _User(time(9), time(17))
    # 06:00-14:00 UTC == 09:00-17:00 local
    assert business_seconds_between(utc(2026, 10, 5, 6), utc(2026, 10, 5, 14), user, co) == 8 * 3600


def test_dst_transition_day_uses_real_elapsed_time():
    co = _Co(tz="America/New_York")  # DST ends 2026-11-01 (25-hour day)
    user = _User(time(0, 0), time(23, 59))
    start = datetime(2026, 11, 1, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    end = datetime(2026, 11, 2, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    secs = business_seconds_between(start, end, user, co)
    assert secs == 25 * 3600 - 60


def test_end_before_start_is_zero():
    assert business_seconds_between(utc(2026, 10, 5, 12), utc(2026, 10, 5, 11), _User(time(9), time(17)), _Co()) == 0
