"""
Measured CRM usage time ("actual working hours").

The crediting rule, and why it is shaped this way:

Each ping credits the wall-clock interval that has elapsed since the user's own
``User.work_last_ping_at`` cursor, computed entirely from the *server* clock. The
client sends only which app it is (``web``/``mobile``) — never a duration. Four
properties fall out of that choice:

1. A closed tab or a killed app needs no unload beacon: credit is retroactive, so
   the worst case is losing one ping interval.
2. Two clients (web + mobile) cannot double-count. The cursor is advanced with a
   conditional ``UPDATE ... WHERE work_last_ping_at = <observed>``; whichever ping
   loses that race credits nothing.
3. A throttled or frozen timer self-corrects, because elapsed time is measured, not
   counted in ticks.
4. Inflating hours costs real wall-clock time. A forged request can gain at most
   ``MAX_CREDIT_SECONDS``.

Credited seconds are bucketed into the *company-local* calendar day at write time,
which is what lets a session crossing local midnight split naturally and lets the
Employees Report aggregate with no timezone math at read time.
"""

import logging
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import Role, User, WorkDaySummary

try:  # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None  # type: ignore[assignment]

    class ZoneInfoNotFoundError(Exception):  # type: ignore[no-redef]
        pass


logger = logging.getLogger(__name__)

# How often clients ping. Served to clients in the ping/today responses so the
# cadence can be retuned server-side without a web or mobile release.
PING_INTERVAL_SECONDS = 60

# Hard ceiling on what a single ping may credit: 2x the interval, enough to absorb
# one dropped ping and nothing more. This is the anti-inflation bound.
MAX_CREDIT_SECONDS = 120

# Every company role is measured, including owners/admins. Derived from the enum
# rather than listed, so a new role is tracked the day it is added.
#
# super_admin is the only exclusion: it is the platform operator, not company staff,
# and it has no company to attribute hours to. (Impersonated sessions are refused
# separately in the ping view, so support work never lands on a tenant's numbers.)
#
# Mirrored on the clients by roleTracksWorkHours(), but enforced here.
WORK_TRACKED_ROLES = frozenset(
    role.value for role in Role if role is not Role.SUPER_ADMIN
)

VALID_SOURCES = frozenset({"web", "mobile"})
DEFAULT_IDLE_TIMEOUT_MINUTES = 10


def _company_zone(company):
    """
    Resolve a company's IANA timezone, falling back to UTC for blank/unknown values.

    Deliberately a local copy rather than importing ``notifications.dispatch`` or
    ``crm.availability``: both of those apps import ``accounts``, so reaching back
    into them from here would create an import cycle.
    """
    name = (getattr(company, "timezone", None) or "UTC").strip() or "UTC"
    if ZoneInfo is None:  # pragma: no cover
        return dt_timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Unknown timezone %r on company %s; falling back to UTC",
            name,
            getattr(company, "id", None),
        )
        return ZoneInfo("UTC")


def company_tracking_config(company):
    """Return ``(enabled, idle_timeout_seconds)`` for a company."""
    if not company:
        return False, DEFAULT_IDLE_TIMEOUT_MINUTES * 60
    enabled = bool(getattr(company, "work_hours_tracking_enabled", False))
    minutes = getattr(company, "work_hours_idle_timeout_minutes", None)
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_IDLE_TIMEOUT_MINUTES
    if minutes < 1:
        minutes = DEFAULT_IDLE_TIMEOUT_MINUTES
    return enabled, minutes * 60


def user_is_work_tracked(user) -> bool:
    """True when this user's CRM usage should accrue for their company."""
    if not user or not getattr(user, "is_active", False):
        return False
    company = getattr(user, "company", None)
    enabled, _ = company_tracking_config(company)
    if not enabled:
        return False
    return (getattr(user, "role", "") or "") in WORK_TRACKED_ROLES


def normalize_source(value) -> str:
    source = str(value or "").strip().lower()
    return source if source in VALID_SOURCES else "web"


def local_work_date(company, moment):
    """The company-local calendar date that ``moment`` (aware UTC) falls on."""
    return moment.astimezone(_company_zone(company)).date()


def _bucket_spans(company, start, end):
    """
    Split the credited window ``[start, end]`` into ``(local_date, seconds, span_end)``
    parts, cutting at company-local midnight.

    At most one cut is possible because the window is never longer than
    ``MAX_CREDIT_SECONDS``. ``span_end`` is carried so each day's row can stamp an
    activity timestamp that actually falls inside that day.
    """
    tz = _company_zone(company)
    start_date = start.astimezone(tz).date()
    end_date = end.astimezone(tz).date()
    total = int((end - start).total_seconds())

    if start_date == end_date:
        return [(end_date, total, end)] if total > 0 else []

    # Midnight that opens the *end* date, back in UTC.
    boundary = datetime.combine(end_date, time.min, tzinfo=tz).astimezone(dt_timezone.utc)
    before = int((boundary - start).total_seconds())
    after = total - before

    spans = []
    if before > 0:
        spans.append((start_date, before, boundary))
    if after > 0:
        spans.append((end_date, after, end))
    return spans


def _touch_day(company, user, work_date, moment, *, is_current_day):
    """Get-or-create the day row, keeping first/last activity honest."""
    summary, created = WorkDaySummary.objects.get_or_create(
        user=user,
        work_date=work_date,
        defaults={
            "company": company,
            "first_activity_at": moment,
            "last_activity_at": moment,
        },
    )
    if created:
        return summary

    updates = {"updated_at": timezone.now()}
    if is_current_day:
        updates["last_activity_at"] = moment
    WorkDaySummary.objects.filter(pk=summary.pk).update(**updates)
    return summary


def _apply_credit(company, user, window_start, now, credited, source, *, idle_gap):
    """
    Write ``credited`` seconds into the company-local day buckets they belong to and
    bump the per-ping counters. Returns the current day's total seconds.
    """
    source_field = "mobile_seconds" if source == "mobile" else "web_seconds"
    today = local_work_date(company, now)

    spans = _bucket_spans(company, window_start, now) if credited > 0 else []
    credited_by_date = {day: (seconds, span_end) for day, seconds, span_end in spans}

    # The current day's row is always touched, so an idle resume or a zero-credit ping
    # still records that the user was here.
    for day in sorted(set(credited_by_date) | {today}):
        seconds, span_end = credited_by_date.get(day, (0, now))
        is_current_day = day == today
        _touch_day(company, user, day, span_end, is_current_day=is_current_day)

        updates = {"updated_at": timezone.now()}
        if seconds:
            updates["active_seconds"] = F("active_seconds") + seconds
            updates[source_field] = F(source_field) + seconds
        if is_current_day:
            updates["ping_count"] = F("ping_count") + 1
            if idle_gap:
                updates["idle_pause_count"] = F("idle_pause_count") + 1
        WorkDaySummary.objects.filter(user=user, work_date=day).update(**updates)

    return (
        WorkDaySummary.objects.filter(user=user, work_date=today)
        .values_list("active_seconds", flat=True)
        .first()
        or 0
    )


def credit_work_time(user, *, source="web", now=None):
    """
    Credit the interval since this user's last ping and advance the cursor.

    Returns a dict describing the outcome; ``credited_seconds`` is 0 for the first
    ping after login (bootstrap), for a gap that exceeded the idle timeout, for a
    backwards clock, and for a ping that lost the cursor race to another client.
    """
    company = getattr(user, "company", None)
    enabled, idle_seconds = company_tracking_config(company)
    now = now or timezone.now()

    if not user_is_work_tracked(user):
        return {
            "tracking_enabled": False,
            "credited_seconds": 0,
            "today_seconds": 0,
            "work_date": None,
            "idle_timeout_minutes": idle_seconds // 60,
        }

    source = normalize_source(source)
    prev = user.work_last_ping_at
    idle_gap = False

    if prev is None:
        # Bootstrap: we have no idea how long this session has been open, so credit
        # nothing and just plant the cursor.
        credited = 0
    elif now <= prev:
        # Clock skew, a duplicate, or an out-of-order delivery.
        credited = 0
    elif (now - prev).total_seconds() >= idle_seconds:
        # The user was away longer than the idle timeout; that gap is not work.
        credited = 0
        idle_gap = True
    else:
        credited = min(int((now - prev).total_seconds()), MAX_CREDIT_SECONDS)

    with transaction.atomic():
        # Conditional update = the whole concurrency story. No row lock on `users`,
        # which is read on every authenticated request.
        rows = User.objects.filter(pk=user.pk, work_last_ping_at=prev).update(
            work_last_ping_at=now,
            last_seen_at=now,
            last_seen_source=source,
        )
        if rows != 1:
            # Another client advanced the cursor first; crediting again would double-count.
            return {
                "tracking_enabled": True,
                "credited_seconds": 0,
                "today_seconds": today_seconds_for(user, now=now),
                "work_date": local_work_date(company, now),
                "idle_timeout_minutes": idle_seconds // 60,
            }

        user.work_last_ping_at = now
        user.last_seen_at = now
        user.last_seen_source = source

        if prev is None:
            today_seconds = today_seconds_for(user, now=now)
        else:
            window_start = now - timedelta(seconds=credited)
            today_seconds = _apply_credit(
                company, user, window_start, now, credited, source, idle_gap=idle_gap
            )

    return {
        "tracking_enabled": True,
        "credited_seconds": credited,
        "today_seconds": today_seconds,
        "work_date": local_work_date(company, now),
        "idle_timeout_minutes": idle_seconds // 60,
    }


def today_seconds_for(user, *, now=None) -> int:
    """Measured seconds accrued by this user on the current company-local day."""
    company = getattr(user, "company", None)
    now = now or timezone.now()
    return (
        WorkDaySummary.objects.filter(user=user, work_date=local_work_date(company, now))
        .values_list("active_seconds", flat=True)
        .first()
        or 0
    )


def company_user_totals(company, *, days=7, now=None):
    """
    Per-user measured seconds for a company: today's total and the trailing window.

    One grouped query over a tiny slice (users x days), aggregated in Python so both
    numbers come from the same fetch. Feeds the Employees page, which needs a figure
    for every user on the visible page without an N+1 per card.
    """
    now = now or timezone.now()
    today = local_work_date(company, now)
    days = max(1, min(int(days or 7), 366))
    start = today - timedelta(days=days - 1)

    totals: dict[int, dict[str, int]] = {}
    rows = WorkDaySummary.objects.filter(
        company=company, work_date__gte=start, work_date__lte=today
    ).values_list("user_id", "work_date", "active_seconds")

    for user_id, work_date, seconds in rows:
        entry = totals.setdefault(user_id, {"today_seconds": 0, "range_seconds": 0})
        entry["range_seconds"] += seconds or 0
        if work_date == today:
            entry["today_seconds"] += seconds or 0

    return {"work_date": today, "days": days, "totals": totals}


def today_summary_for(user, *, now=None):
    """Payload for the employee-facing "today" indicator."""
    company = getattr(user, "company", None)
    enabled, idle_seconds = company_tracking_config(company)
    now = now or timezone.now()
    work_date = local_work_date(company, now)

    summary = WorkDaySummary.objects.filter(user=user, work_date=work_date).first()
    return {
        "tracking_enabled": enabled and user_is_work_tracked(user),
        "ping_interval_seconds": PING_INTERVAL_SECONDS,
        "idle_timeout_minutes": idle_seconds // 60,
        "work_date": work_date,
        "today_seconds": summary.active_seconds if summary else 0,
        "last_activity_at": summary.last_activity_at if summary else None,
    }
