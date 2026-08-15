"""
Shared "exactly-once" dispatch helpers for cron-driven notifications.

The pattern every scheduled notification job should follow:

1. Compute a *deterministic due instant* for each entity from data on the entity itself
   (e.g. ``last_contacted_at + 10h``), instead of notifying everything that matches a
   filter at whatever moment the cron happens to run.
2. Claim that (user, entity, due instant) pair via :func:`claim_dispatch`.
3. Only send when the claim succeeds.

Because the due instant is derived from the entity, any real activity moves it forward and
the job re-arms by itself — no "already notified" bookkeeping on the entity is needed.

Run the job frequently (every 15 min) and let each entity decide *when* it is due; this
replaces the old "sweep everything at a fixed hour" approach that produced bursts of
simultaneous pushes.
"""
import logging
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.models import ReminderDispatchLog

try:  # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None  # type: ignore[assignment]

    class ZoneInfoNotFoundError(Exception):  # type: ignore[no-redef]
        pass


logger = logging.getLogger(__name__)


def claim_dispatch(
    *,
    user,
    notification_type: str,
    obj,
    scheduled_for,
    minutes_before: int = 0,
    dedupe_key: str = "",
    expect_email: bool = False,
) -> Optional[ReminderDispatchLog]:
    """
    Try to claim the right to notify ``user`` about ``obj`` for the ``scheduled_for`` instant.

    Returns the :class:`ReminderDispatchLog` row the caller must fill in (setting
    ``push_sent`` / ``email_sent`` / ``last_error`` and saving), or ``None`` when this
    exact dispatch has already been satisfied and must not be repeated.

    "Satisfied" means the push went out and — only when ``expect_email`` is True — the email
    did too. Push-only notifications must pass ``expect_email=False``, otherwise the row
    never reaches a satisfied state and the job re-notifies on every subsequent tick.

    The row is created regardless of the recipient's notification preferences, so a muted
    recipient can never cause an unbounded retry loop.
    """
    if user is None or obj is None or scheduled_for is None:
        return None

    content_type = ContentType.objects.get_for_model(obj.__class__)

    lookup = {
        "user": user,
        "notification_type": notification_type,
        "content_type": content_type,
        "object_id": str(obj.pk),
        "scheduled_for": scheduled_for,
        "minutes_before": minutes_before,
        "dedupe_key": dedupe_key or "",
    }

    try:
        with transaction.atomic():
            log_row, _created = ReminderDispatchLog.objects.get_or_create(
                defaults={"push_sent": False, "email_sent": False},
                **lookup,
            )
    except IntegrityError:
        # Concurrent worker won the race; re-read and let the satisfied-check below decide.
        log_row = ReminderDispatchLog.objects.filter(**lookup).first()
        if log_row is None:
            return None

    if log_row.push_sent and (log_row.email_sent or not expect_email):
        return None

    return log_row


def mark_dispatched(
    log_row: Optional[ReminderDispatchLog],
    *,
    push_sent: Optional[bool] = None,
    email_sent: Optional[bool] = None,
    error: Optional[str] = None,
) -> None:
    """Persist the outcome of a claimed dispatch. No-op when ``log_row`` is None."""
    if log_row is None:
        return
    if push_sent is not None:
        log_row.push_sent = push_sent
    if email_sent is not None:
        log_row.email_sent = email_sent
    if error is not None:
        log_row.last_error = error
    log_row.save(update_fields=["push_sent", "email_sent", "last_error", "updated_at"])


def company_timezone(company) -> "ZoneInfo":
    """
    Resolve a company's IANA timezone, falling back to UTC for blank/unknown values.

    ``Company.timezone`` is free-text, so a bad value must not break a platform-wide job.
    """
    name = (getattr(company, "timezone", None) or "UTC").strip() or "UTC"
    if ZoneInfo is None:  # pragma: no cover
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Unknown timezone %r on company %s; falling back to UTC",
            name,
            getattr(company, "id", None),
        )
        return ZoneInfo("UTC")


def local_now(company) -> datetime:
    """Current time expressed in the company's local timezone."""
    return timezone.now().astimezone(company_timezone(company))


def due_local_slot(company, target_hour: int, *, now_local: Optional[datetime] = None):
    """
    For a job that should fire once per day at ``target_hour`` in the company's local time,
    return the UTC instant of today's slot, or ``None`` if that slot has not arrived yet.

    Designed for jobs running every 15-60 minutes: the returned instant is stable for the
    whole local day, so it doubles as the ``scheduled_for`` dedupe key and each company
    fires exactly once at its own local hour regardless of the server's timezone.
    """
    tz = company_timezone(company)
    now_local = now_local or timezone.now().astimezone(tz)

    slot_local = datetime.combine(now_local.date(), time(hour=target_hour), tzinfo=tz)
    if now_local < slot_local:
        return None
    return slot_local.astimezone(dt_timezone.utc)


def escalation_step(reference, sla_hours: int, max_steps: int, *, now=None):
    """
    Given the moment a lead was last touched, return ``(step, due_at, elapsed_hours)`` for
    the highest escalation rung that is currently due, or ``None`` when nothing is due.

    ``step`` counts full SLA periods elapsed (1 => ``sla_hours`` overdue, 2 => twice that,
    ...) and is *clamped* to ``max_steps`` so a permanently untouched lead goes quiet
    instead of nagging forever. Clamping rather than bailing out matters: a lead that first
    becomes visible well past the last rung (a backlog, or a cron outage) still gets its
    final alert, and because the clamped ``due_at`` is a fixed instant it is claimed once
    and never repeats.

    ``due_at`` is ``reference + step * sla_hours`` — a deterministic instant, which is what
    makes the dispatch claim stable across runs.
    """
    if reference is None or not sla_hours or sla_hours <= 0:
        return None

    now = now or timezone.now()
    elapsed_hours = (now - reference).total_seconds() / 3600.0
    if elapsed_hours < sla_hours:
        return None

    step = min(int(elapsed_hours // sla_hours), max_steps)
    if step < 1:
        return None

    due_at = reference + timedelta(hours=step * sla_hours)
    return step, due_at, step * sla_hours
