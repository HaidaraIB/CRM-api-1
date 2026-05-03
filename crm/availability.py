"""
Lead/deal assignment availability from weekly day off (company-local calendar).
"""
from __future__ import annotations

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


def local_today_weekday(company) -> int:
    """Return datetime.weekday() (Mon=0..Sun=6) for 'today' in the company's timezone."""
    tz = zone_for_company(company)
    return dj_timezone.now().astimezone(tz).date().weekday()


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
