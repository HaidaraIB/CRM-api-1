"""
"Business time" between two instants: seconds that fall inside an employee's work hours.

Window precedence for each company-local date:
  1. the employee's own shift (work_start_time/work_end_time), skipping their weekly day off
     and planned time off;
  2. otherwise the company's ``business_hours`` for that weekday;
  3. if neither is configured at all, there is no calendar to apply and the raw elapsed
     time is returned (so unconfigured companies get sensible, not zero, numbers).
Overnight windows (start >= end) are split into [00:00, end) and [start, 24:00) of each date.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from crm.availability import zone_for_company

_MAX_DAYS = 800


def _parse_hhmm(value) -> time | None:
    if isinstance(value, time):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        hh, mm = value.strip().split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def company_has_business_hours(company) -> bool:
    hours = getattr(company, "business_hours", None) or {}
    return any(
        isinstance(v, dict) and _parse_hhmm(v.get("open")) and _parse_hhmm(v.get("close"))
        for v in hours.values()
    )


def _user_has_shift(user) -> bool:
    return bool(
        user
        and getattr(user, "work_start_time", None) is not None
        and getattr(user, "work_end_time", None) is not None
    )


def has_work_calendar(user, company) -> bool:
    return _user_has_shift(user) or company_has_business_hours(company)


def _segments_for_date(d: date, user, company):
    """[(open_time, close_time_or_None_for_midnight)] for one local date; [] when closed."""
    if _user_has_shift(user):
        if user.weekly_day_off is not None and user.weekly_day_off == d.weekday():
            return []
        t_off_s = getattr(user, "time_off_start_date", None)
        t_off_e = getattr(user, "time_off_end_date", None)
        if t_off_s and t_off_e and t_off_s <= d <= t_off_e:
            return []
        start, end = user.work_start_time, user.work_end_time
    else:
        entry = (getattr(company, "business_hours", None) or {}).get(str(d.weekday()))
        if not isinstance(entry, dict):
            return []
        start, end = _parse_hhmm(entry.get("open")), _parse_hhmm(entry.get("close"))
        if start is None or end is None:
            return []
    if start == end:
        return []
    if start < end:
        return [(start, end)]
    return [(time(0, 0), end), (start, None)]


def business_seconds_between(start: datetime, end: datetime, user, company) -> int:
    """Seconds of [start, end] inside work hours; raw elapsed seconds when no calendar exists."""
    if end <= start:
        return 0
    if not has_work_calendar(user, company):
        return int((end - start).total_seconds())

    tz = zone_for_company(company)
    ls, le = start.astimezone(tz), end.astimezone(tz)
    d = ls.date()
    last = le.date()
    total = 0.0
    days = 0
    while d <= last and days < _MAX_DAYS:
        for open_t, close_t in _segments_for_date(d, user, company):
            seg_start = datetime.combine(d, open_t, tzinfo=tz)
            seg_end = (
                datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=tz)
                if close_t is None
                else datetime.combine(d, close_t, tzinfo=tz)
            )
            lo, hi = max(seg_start, ls), min(seg_end, le)
            if hi > lo:
                # Same-tzinfo subtraction is wall-clock; go through UTC so DST days are real elapsed time.
                total += (hi.astimezone(dt_timezone.utc) - lo.astimezone(dt_timezone.utc)).total_seconds()
        d += timedelta(days=1)
        days += 1
    return int(total)


def validate_business_hours(value) -> dict:
    """
    Normalise a ``Company.business_hours`` payload or raise ValueError with a short reason.
    Shape: {"0": {"open": "09:00", "close": "17:00"}, "6": null, ...}; a missing or null weekday is closed.
    Overnight windows (close earlier than open) are allowed; open == close is rejected.
    """
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("business_hours must be an object keyed by weekday (0=Monday .. 6=Sunday).")
    clean = {}
    for key, entry in value.items():
        day = str(key)
        if day not in {"0", "1", "2", "3", "4", "5", "6"}:
            raise ValueError("business_hours keys must be weekdays 0 (Monday) to 6 (Sunday).")
        if entry in (None, {}):
            continue
        if not isinstance(entry, dict):
            raise ValueError("Each weekday must be null or {open, close}.")
        opened, closed = _parse_hhmm(entry.get("open")), _parse_hhmm(entry.get("close"))
        if opened is None or closed is None:
            raise ValueError("open and close must be HH:MM times.")
        if opened == closed:
            raise ValueError("open and close cannot be the same time.")
        clean[day] = {"open": opened.strftime("%H:%M"), "close": closed.strftime("%H:%M")}
    return clean
