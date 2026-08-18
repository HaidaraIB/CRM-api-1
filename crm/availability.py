"""
Lead/deal assignment availability from weekly day off and working hours (company-local calendar).

Three "is this user available right now" predicates exist here and they answer different
questions — pick carefully:
  - user_is_within_working_hours        -> False when work_start_time/work_end_time are UNSET.
  - user_is_on_shift_for_urgent         -> day-off check + user_is_within_working_hours;
                                            also False when hours are unset (no shift = never urgent-eligible).
  - user_is_on_shift_or_unscheduled     -> day-off check + within-hours OR NO HOURS CONFIGURED.
                                            True when hours are unset (most users today have none).
Use the last one for real-time notification/assignment decisions (e.g. walk-in arrivals) where an
employee with no configured shift should still be reachable; use the urgent variant only where
"never eligible without an explicit shift" is the intended, stricter behavior.
"""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone


def zone_for_company(company) -> ZoneInfo:
    if not company:
        return ZoneInfo("UTC")
    name = (getattr(company, "timezone", None) or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def local_now_for_company(company):
    """Aware datetime 'now' in the company's timezone."""
    tz = zone_for_company(company)
    return dj_timezone.now().astimezone(tz)


def local_today_weekday(company) -> int:
    """Return datetime.weekday() (Mon=0..Sun=6) for 'today' in the company's timezone."""
    return local_now_for_company(company).date().weekday()


def user_accepts_new_assignments(user, company_for_calendar=None) -> bool:
    """
    False if user has weekly_day_off set and today (in company TZ) is that weekday.
    If company_for_calendar is set, "today" uses that company's timezone (e.g. the lead's company).
    """
    if not user or getattr(user, "weekly_day_off", None) is None:
        return True
    company = company_for_calendar if company_for_calendar is not None else getattr(
        user, "company", None
    )
    if not company:
        return True
    return user.weekly_day_off != local_today_weekday(company)


def _time_in_window(now_t: time, start: time, end: time) -> bool:
    """True if now_t is within [start, end], supporting overnight wrap (start > end)."""
    if start == end:
        return False
    if start < end:
        return start <= now_t <= end
    # Overnight: e.g. 22:00–06:00
    return now_t >= start or now_t <= end


def user_is_within_working_hours(user, company_for_calendar=None) -> bool:
    """
    True if user has both work_start_time and work_end_time set and current local time
    (company TZ) falls inside that daily window. Overnight ranges are supported.
    """
    if not user:
        return False
    start = getattr(user, "work_start_time", None)
    end = getattr(user, "work_end_time", None)
    if start is None or end is None:
        return False
    company = company_for_calendar if company_for_calendar is not None else getattr(
        user, "company", None
    )
    if not company:
        return False
    now_t = local_now_for_company(company).time().replace(microsecond=0)
    return _time_in_window(now_t, start, end)


def user_is_on_shift_for_urgent(user, company_for_calendar=None) -> bool:
    """
    Eligible for urgent auto-assign: not on weekly day off, and currently within working hours.
    Users without a working-hours window are never on-shift for urgent routing.
    """
    if not user:
        return False
    company = company_for_calendar if company_for_calendar is not None else getattr(
        user, "company", None
    )
    if not user_accepts_new_assignments(user, company_for_calendar=company):
        return False
    return user_is_within_working_hours(user, company_for_calendar=company)


def user_has_configured_working_hours(user) -> bool:
    """True only when both work_start_time and work_end_time are set."""
    if not user:
        return False
    return (
        getattr(user, "work_start_time", None) is not None
        and getattr(user, "work_end_time", None) is not None
    )


def user_is_on_shift_or_unscheduled(user, company_for_calendar=None) -> bool:
    """
    Notifiable/assignable *right now* for real-time routing (e.g. walk-in arrivals).

    Not on their weekly day off, AND either currently within their configured working-hours
    window OR they have no working-hours window configured at all (treated as always
    available — most users today have no shift set, and this must not silently exclude them).
    """
    if not user:
        return False
    company = company_for_calendar if company_for_calendar is not None else getattr(
        user, "company", None
    )
    if not user_accepts_new_assignments(user, company_for_calendar=company):
        return False
    if not user_has_configured_working_hours(user):
        return True
    return user_is_within_working_hours(user, company_for_calendar=company)
