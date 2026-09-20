"""Slot generation and booking validation for public demo reservations."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from django.utils import timezone

from .models import (
    WEEKDAY_KEYS,
    DemoBooking,
    DemoBookingBlockedDate,
    DemoBookingSettings,
    DemoBookingStatus,
)

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _weekday_key(d: date) -> str:
    return WEEKDAY_KEYS[d.weekday()]


def _parse_time(value: str) -> time | None:
    if not value:
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def get_zone(settings_obj: DemoBookingSettings) -> ZoneInfo:
    try:
        return ZoneInfo(settings_obj.timezone or "Asia/Baghdad")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Baghdad")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_phone(phone: str) -> str:
    return re.sub(r"\s+", "", (phone or "").strip())


def confirmed_bookings_queryset():
    return DemoBooking.objects.filter(status=DemoBookingStatus.CONFIRMED)


def occupied_starts_utc(from_dt: datetime, to_dt: datetime) -> set[datetime]:
    qs = confirmed_bookings_queryset().filter(
        starts_at__gte=from_dt,
        starts_at__lt=to_dt,
    )
    return {b.starts_at.replace(microsecond=0) for b in qs}


def blocked_dates_in_range(from_date: date, to_date: date) -> set[date]:
    return set(
        DemoBookingBlockedDate.objects.filter(
            date__gte=from_date,
            date__lte=to_date,
        ).values_list("date", flat=True)
    )


def iter_slot_starts_local(
    settings_obj: DemoBookingSettings,
    day: date,
    tz: ZoneInfo,
) -> list[datetime]:
    weekly = settings_obj.weekly_hours or {}
    day_cfg = weekly.get(_weekday_key(day)) or {}
    if not day_cfg.get("enabled"):
        return []
    start_t = _parse_time(day_cfg.get("start") or "")
    end_t = _parse_time(day_cfg.get("end") or "")
    if not start_t or not end_t:
        return []
    duration = max(5, int(settings_obj.duration_minutes or 30))
    local_start = datetime.combine(day, start_t, tzinfo=tz)
    local_end = datetime.combine(day, end_t, tzinfo=tz)
    if local_end <= local_start:
        return []
    slots: list[datetime] = []
    cursor = local_start
    delta = timedelta(minutes=duration)
    while cursor + delta <= local_end:
        slots.append(cursor)
        cursor += delta
    return slots


def compute_available_slots(
    settings_obj: DemoBookingSettings,
    from_date: date,
    to_date: date,
) -> list[dict]:
    if not settings_obj.is_enabled:
        return []
    tz = get_zone(settings_obj)
    now_utc = timezone.now()
    min_start_utc = now_utc + timedelta(hours=int(settings_obj.min_notice_hours or 0))
    horizon_end_utc = now_utc + timedelta(days=int(settings_obj.horizon_days or 14))
    blocked = blocked_dates_in_range(from_date, to_date)
    range_start_utc = datetime.combine(from_date, time.min, tzinfo=tz).astimezone(
        dt_timezone.utc
    )
    range_end_utc = (
        datetime.combine(to_date, time.max, tzinfo=tz).astimezone(dt_timezone.utc)
        + timedelta(days=1)
    )
    occupied = occupied_starts_utc(range_start_utc, range_end_utc)
    duration = timedelta(minutes=int(settings_obj.duration_minutes or 30))
    results: list[dict] = []
    day = from_date
    while day <= to_date:
        if day not in blocked:
            for local_start in iter_slot_starts_local(settings_obj, day, tz):
                start_utc = local_start.astimezone(dt_timezone.utc).replace(microsecond=0)
                end_utc = (start_utc + duration).replace(microsecond=0)
                if start_utc < min_start_utc:
                    continue
                if start_utc > horizon_end_utc:
                    continue
                if start_utc in occupied:
                    continue
                results.append(
                    {
                        "starts_at": start_utc.isoformat().replace("+00:00", "Z"),
                        "ends_at": end_utc.isoformat().replace("+00:00", "Z"),
                        "date": day.isoformat(),
                    }
                )
        day += timedelta(days=1)
    return results


def dates_with_slots(
    settings_obj: DemoBookingSettings,
    from_date: date,
    to_date: date,
) -> list[str]:
    slots = compute_available_slots(settings_obj, from_date, to_date)
    seen: set[str] = set()
    ordered: list[str] = []
    for s in slots:
        d = s["date"]
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def is_slot_available(settings_obj: DemoBookingSettings, starts_at_utc: datetime) -> bool:
    if not settings_obj.is_enabled:
        return False
    starts_at_utc = starts_at_utc.astimezone(dt_timezone.utc).replace(microsecond=0)
    tz = get_zone(settings_obj)
    day = starts_at_utc.astimezone(tz).date()
    if DemoBookingBlockedDate.objects.filter(date=day).exists():
        return False
    for slot in compute_available_slots(settings_obj, day, day):
        slot_start = datetime.fromisoformat(
            slot["starts_at"].replace("Z", "+00:00")
        ).astimezone(dt_timezone.utc).replace(microsecond=0)
        if slot_start == starts_at_utc:
            return True
    return False


def has_upcoming_confirmed_booking(*, email: str | None = None, phone: str | None = None) -> bool:
    now = timezone.now()
    q = Q(status=DemoBookingStatus.CONFIRMED, starts_at__gte=now)
    if email:
        q &= Q(email__iexact=normalize_email(email))
    if phone:
        q &= Q(phone=normalize_phone(phone))
    if not email and not phone:
        return False
    return DemoBooking.objects.filter(q).exists()
