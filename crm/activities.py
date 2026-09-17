"""
Unified activities feed: client tasks (actions) + client calls, merged and sorted.

Used by GET /api/v1/activities/ for the CRM Activities page (server-side filters + pagination).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from crm.dashboard_summary import scoped_call_qs, scoped_client_task_qs, _user_display_name


def _parse_activity_filters(request) -> dict:
    params = request.query_params
    return {
        "user_id": (params.get("user") or "").strip(),
        "stage": (params.get("stage") or "").strip(),
        "lead_type": (params.get("lead_type") or "").strip(),
        "time_period": (params.get("time_period") or "").strip(),
        "date_from": (params.get("date_from") or "").strip(),
        "date_to": (params.get("date_to") or "").strip(),
        "search": (params.get("search") or "").strip(),
    }


def _parse_date_only(value: str) -> datetime | None:
    if not value:
        return None
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    return timezone.make_aware(datetime.combine(day, time.min))


def _time_period_bounds(period: str) -> tuple[datetime | None, datetime | None]:
    if not period or period.lower() == "all":
        return None, None

    today = timezone.localdate()
    start_today = timezone.make_aware(datetime.combine(today, time.min))
    end_today = start_today + timedelta(days=1)

    if period == "today":
        return start_today, end_today
    if period == "yesterday":
        start = start_today - timedelta(days=1)
        return start, start_today
    if period == "last7":
        return start_today - timedelta(days=7), None
    if period == "thisMonth":
        start = timezone.make_aware(datetime.combine(today.replace(day=1), time.min))
        return start, None
    return None, None


def _apply_created_at_filters(qs, filters: dict):
    period_start, period_end = _time_period_bounds(filters.get("time_period", ""))
    date_from = _parse_date_only(filters.get("date_from", ""))
    date_to_raw = filters.get("date_to", "")
    date_to = _parse_date_only(date_to_raw)
    date_to_end = None
    if date_to is not None:
        date_to_end = date_to + timedelta(days=1) - timedelta(microseconds=1)

    lower = period_start
    if date_from is not None and (lower is None or date_from > lower):
        lower = date_from

    upper = period_end
    if date_to_end is not None:
        if upper is None or date_to_end < upper:
            upper = date_to_end

    if lower is not None:
        qs = qs.filter(created_at__gte=lower)
    if upper is not None:
        qs = qs.filter(created_at__lte=upper)
    return qs


def _apply_search_filter(qs, search: str):
    if not search:
        return qs
    return qs.filter(
        Q(client__name__icontains=search)
        | Q(notes__icontains=search)
        | Q(created_by__first_name__icontains=search)
        | Q(created_by__last_name__icontains=search)
        | Q(created_by__username__icontains=search)
    )


def _serialize_client_task(ct) -> dict:
    return {
        "id": f"client_task-{ct.id}",
        "type": "client_task",
        "user": _user_display_name(ct.created_by) or "Unknown",
        "lead": ct.client.name if ct.client_id else "",
        "stage": ct.stage.name if ct.stage_id else "",
        "call_method": "",
        "notes": ct.notes or "",
        "created_at": ct.created_at.isoformat() if ct.created_at else None,
    }


def _serialize_client_call(cc) -> dict:
    return {
        "id": f"client_call-{cc.id}",
        "type": "client_call",
        "user": _user_display_name(cc.created_by) or "Unknown",
        "lead": cc.client.name if cc.client_id else "",
        "stage": "",
        "call_method": cc.call_method.name if cc.call_method_id else "",
        "notes": cc.notes or "",
        "created_at": cc.created_at.isoformat() if cc.created_at else None,
    }


def build_activities_list(user, filters: dict) -> list[dict]:
    """Return merged activity rows newest-first (not paginated)."""
    tasks_qs = scoped_client_task_qs(user).select_related(
        "client", "stage", "created_by",
    )
    calls_qs = scoped_call_qs(user).select_related(
        "client", "call_method", "created_by",
    )

    user_id = filters.get("user_id") or ""
    if user_id and user_id.lower() != "all":
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = None
        if uid is not None:
            tasks_qs = tasks_qs.filter(created_by_id=uid)
            calls_qs = calls_qs.filter(created_by_id=uid)

    stage = filters.get("stage") or ""
    if stage and stage.lower() != "all":
        tasks_qs = tasks_qs.filter(stage__name=stage)
        calls_qs = calls_qs.filter(call_method__name=stage)

    lead_type = filters.get("lead_type") or ""
    if lead_type and lead_type.lower() != "all":
        tasks_qs = tasks_qs.filter(client__type__iexact=lead_type)
        calls_qs = calls_qs.filter(client__type__iexact=lead_type)

    tasks_qs = _apply_created_at_filters(tasks_qs, filters)
    calls_qs = _apply_created_at_filters(calls_qs, filters)

    search = filters.get("search") or ""
    tasks_qs = _apply_search_filter(tasks_qs, search)
    calls_qs = _apply_search_filter(calls_qs, search)

    rows: list[dict] = []
    rows.extend(_serialize_client_task(ct) for ct in tasks_qs)
    rows.extend(_serialize_client_call(cc) for cc in calls_qs)
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows


def parse_activity_filters_from_request(request) -> dict:
    return _parse_activity_filters(request)
