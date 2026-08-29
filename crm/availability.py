"""
Lead/deal assignment availability from time off, weekly day off and working hours
(company-local calendar).

Temporary unavailability comes in two self-expiring flavours, both distinct from
``is_active=False`` deactivation (which revokes login and redistributes the book):
  - ``time_off_start_date``/``time_off_end_date`` -> planned leave, inclusive company-local dates.
  - ``unavailable_until``                         -> short ad-hoc absence, a timestamp.
Both are folded into ``user_accepts_new_assignments``, so every caller of the predicates
below inherits them without further wiring.

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


# Reasons a user is skipped by routing, most specific first.
BLOCK_TIME_OFF = "time_off"
BLOCK_UNAVAILABLE = "unavailable"
BLOCK_WEEKLY_DAY_OFF = "weekly_day_off"


def _calendar_company(user, company_for_calendar):
    if company_for_calendar is not None:
        return company_for_calendar
    return getattr(user, "company", None)


def user_is_on_time_off(user, company_for_calendar=None) -> bool:
    """
    True while today (company-local date) falls inside the user's planned leave window.
    Both bounds are inclusive; a half-set window is ignored rather than guessed at.
    """
    if not user:
        return False
    start = getattr(user, "time_off_start_date", None)
    end = getattr(user, "time_off_end_date", None)
    if start is None or end is None:
        return False
    company = _calendar_company(user, company_for_calendar)
    if not company:
        return False
    today = local_now_for_company(company).date()
    return start <= today <= end


def user_is_temporarily_unavailable(user) -> bool:
    """True while the ad-hoc 'away' timestamp is still in the future (absolute, no TZ math)."""
    if not user:
        return False
    until = getattr(user, "unavailable_until", None)
    if not until:
        return False
    return until > dj_timezone.now()


def user_is_on_weekly_day_off(user, company_for_calendar=None) -> bool:
    """True if the user has a weekly day off and today (company TZ) is that weekday."""
    if not user or getattr(user, "weekly_day_off", None) is None:
        return False
    company = _calendar_company(user, company_for_calendar)
    if not company:
        return False
    return user.weekly_day_off == local_today_weekday(company)


def assignment_block_reason(user, company_for_calendar=None) -> str | None:
    """
    Why this user cannot take a new assignment right now, or None if they can.

    Planned leave wins over the ad-hoc away flag, which wins over the weekly day off —
    the longer-lived reason is the more useful one to show an admin.
    """
    if not user:
        return None
    company = _calendar_company(user, company_for_calendar)
    if user_is_on_time_off(user, company_for_calendar=company):
        return BLOCK_TIME_OFF
    if user_is_temporarily_unavailable(user):
        return BLOCK_UNAVAILABLE
    if user_is_on_weekly_day_off(user, company_for_calendar=company):
        return BLOCK_WEEKLY_DAY_OFF
    return None


# Keeps the pre-existing employee_weekly_day_off key so clients already handling it don't break.
_BLOCK_ERRORS = {
    BLOCK_TIME_OFF: (
        "Cannot assign to this user while they are on time off.",
        "employee_time_off",
    ),
    BLOCK_UNAVAILABLE: (
        "Cannot assign to this user while they are marked unavailable.",
        "employee_unavailable",
    ),
    BLOCK_WEEKLY_DAY_OFF: (
        "Cannot assign to this user on their weekly day off.",
        "employee_weekly_day_off",
    ),
}


def assignment_block_message(reason: str) -> str:
    return _BLOCK_ERRORS[reason][0]


def assignment_block_error_key(reason: str) -> str:
    return _BLOCK_ERRORS[reason][1]


def assignment_block_error(reason: str, field: str = "assigned_to") -> dict:
    """Serializer/ValidationError payload for a reason from assignment_block_reason."""
    message, error_key = _BLOCK_ERRORS[reason]
    return {field: message, "error_key": error_key}


def user_accepts_new_assignments(user, company_for_calendar=None) -> bool:
    """
    False while the user is on planned time off, flagged temporarily unavailable, or on
    their weekly day off (all evaluated in company TZ where date-based).
    If company_for_calendar is set, "today" uses that company's timezone (e.g. the lead's company).
    """
    return assignment_block_reason(user, company_for_calendar=company_for_calendar) is None


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
