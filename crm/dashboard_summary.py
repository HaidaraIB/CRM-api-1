"""
Server-side dashboard aggregates.

Formulas mirror CRM-project DashboardPage / useDashboardDerivedMetrics so the
dashboard can stop full-list pagination without changing displayed metrics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time
from decimal import Decimal
from typing import Any

from django.db.models import (
    Count,
    Max,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from accounts.models import Role, User
from crm.models import Client, ClientCall, ClientTask, ClientVisit, Deal, Task
from crm.serializers import ClientActivitySummaryMixin


ONLINE_WINDOW = timedelta(seconds=90)
HOT_STAGE_BOOST = {"following", "meeting", "done_meeting", "follow_after_meeting"}
HOT_STAGE_PENALTY = {"not_interested", "out_of_service", "cancellation"}
TEAM_GOAL_ROLES = {Role.EMPLOYEE.value, Role.DOCTOR.value, Role.DATA_ENTRY.value}
PRESENCE_EXCLUDED_ROLES = {Role.ADMIN.value, Role.SUPERVISOR.value, Role.SUPER_ADMIN.value}
VALID_DAYS = {7, 14, 30}
VALID_SOURCES = {"all", "meta_lead_form", "whatsapp", "manual"}


def _local_day_bounds(day=None):
    """Return [start, end) datetimes for a local calendar day."""
    day = day or timezone.localdate()
    start = timezone.make_aware(datetime.combine(day, time.min))
    end = start + timedelta(days=1)
    return start, end


def _user_display_name(user: User | None) -> str:
    if not user:
        return ""
    full = (user.get_full_name() or "").strip()
    return full or user.username or ""


def scoped_client_task_qs(user):
    """Role-scoped ClientTask queryset (parity with ClientTaskViewSet / mission bar)."""
    qs = ClientTask.objects.all()
    if user.is_admin() or user.is_reception():
        return qs.filter(client__company=user.company)
    if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
        return qs.filter(client__company=user.company)
    if user.is_assigned_clinical_staff():
        return qs.filter(client__assigned_to=user)
    return qs.none()


def scoped_deal_qs(user):
    """Role-scoped Deal queryset (parity with DealViewSet)."""
    qs = Deal.objects.all()
    if user.is_admin():
        return qs.filter(company=user.company)
    if user.is_supervisor() and user.supervisor_has_permission("manage_deals"):
        return qs.filter(company=user.company)
    if user.is_employee():
        return qs.filter(employee=user)
    return qs.none()


def scoped_task_qs(user):
    """Role-scoped deal-Task queryset (parity with TaskViewSet)."""
    qs = Task.objects.all()
    if user.is_admin():
        return qs.filter(deal__company=user.company)
    if user.is_supervisor() and user.supervisor_has_permission("manage_tasks"):
        return qs.filter(deal__company=user.company)
    if user.is_employee():
        return qs.filter(deal__employee=user)
    return qs.none()


def scoped_call_qs(user):
    qs = ClientCall.objects.all()
    if user.is_admin() or user.is_reception():
        return qs.filter(client__company=user.company)
    if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
        return qs.filter(client__company=user.company)
    if user.is_assigned_clinical_staff():
        return qs.filter(client__assigned_to=user)
    return qs.none()


def scoped_visit_qs(user):
    qs = ClientVisit.objects.all()
    if user.is_admin() or user.is_reception():
        return qs.filter(client__company=user.company)
    if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
        return qs.filter(client__company=user.company)
    if user.is_assigned_clinical_staff():
        return qs.filter(client__assigned_to=user)
    return qs.none()


def build_leads_overview(client_qs) -> dict[str, int]:
    """
    Mobile home dashboard lead cards (parity with crm_mobile DashboardScreen).
    Counts by type (fresh/cold) and status name (untouched/touched/following).
    """
    total = client_qs.count()
    by_type = {
        (row["type"] or "").lower(): row["c"]
        for row in client_qs.values("type").annotate(c=Count("id"))
    }
    by_status = {
        (row["status__name"] or "").lower(): row["c"]
        for row in client_qs.values("status__name").annotate(c=Count("id"))
    }
    return {
        "total": total,
        "fresh": by_type.get("fresh", 0),
        "cold": by_type.get("cold", 0),
        "untouched": by_status.get("untouched", 0),
        "touched": by_status.get("touched", 0),
        "following": by_status.get("following", 0),
    }


def build_mission_bar(user, client_qs) -> dict[str, int]:
    """Same four fields as GET /clients/mission-bar-summary/."""
    today = timezone.localdate()
    today_start, today_end = _local_day_bounds(today)

    today_new_leads = client_qs.filter(
        created_at__gte=today_start,
        created_at__lt=today_end,
    ).count()

    unassigned_leads = 0
    if user.is_admin():
        unassigned_leads = client_qs.filter(assigned_to__isnull=True).count()

    task_qs = scoped_client_task_qs(user)
    overdue_follow_ups = task_qs.filter(
        reminder_date__isnull=False,
        reminder_completed_at__isnull=True,
        reminder_date__lt=today_start,
    ).count()

    contact_today = (
        client_qs.filter(
            assigned_to__isnull=False,
            client_tasks__reminder_date__gte=today_start,
            client_tasks__reminder_date__lt=today_end,
            client_tasks__reminder_completed_at__isnull=True,
        )
        .distinct()
        .count()
    )

    return {
        "contact_today": contact_today,
        "overdue_follow_ups": overdue_follow_ups,
        "today_new_leads": today_new_leads,
        "unassigned_leads": unassigned_leads,
    }


def _format_pipeline_value(n: float | Decimal) -> str:
    n = float(n or 0)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(int(n) if n == int(n) else n)


def _activity_feedback_text(activity_type: str | None, activity) -> str | None:
    if not activity:
        return None
    mixin = ClientActivitySummaryMixin()
    # Reuse serializer mixin helpers via a tiny shim object
    class _Obj:
        pass

    obj = _Obj()
    obj._latest_activity_cache = (activity_type, activity)
    return mixin.get_last_feedback(obj)


def _activity_stage_text(activity_type: str | None, activity) -> str | None:
    if not activity:
        return None
    mixin = ClientActivitySummaryMixin()

    class _Obj:
        pass

    obj = _Obj()
    obj._latest_activity_cache = (activity_type, activity)
    return mixin.get_last_stage(obj)


def _latest_activity_maps(user, client_ids: list[int]):
    """Build client_id -> (kind, obj) for latest activity among scoped tasks/calls/visits."""
    if not client_ids:
        return {}

    latest: dict[int, tuple[str, Any]] = {}

    def consider(kind: str, row):
        cid = row.client_id
        prev = latest.get(cid)
        if prev is None or row.created_at > prev[1].created_at:
            latest[cid] = (kind, row)

    def scan(qs, kind: str):
        seen: set[int] = set()
        for row in qs.order_by("-created_at").iterator(chunk_size=500):
            if row.client_id in seen:
                continue
            seen.add(row.client_id)
            consider(kind, row)

    scan(
        scoped_client_task_qs(user)
        .filter(client_id__in=client_ids)
        .select_related("stage", "created_by"),
        "task",
    )
    scan(
        scoped_call_qs(user)
        .filter(client_id__in=client_ids)
        .select_related("call_method", "created_by"),
        "call",
    )
    scan(
        scoped_visit_qs(user)
        .filter(client_id__in=client_ids)
        .select_related("visit_type", "created_by"),
        "visit",
    )
    return latest


def build_dashboard_summary(
    user,
    client_qs,
    *,
    days: int = 7,
    source: str = "all",
    daily_target: int = 5,
    lite: bool = False,
) -> dict[str, Any]:
    days = days if days in VALID_DAYS else 7
    source = source if source in VALID_SOURCES else "all"
    daily_target = max(1, int(daily_target or 5))

    today = timezone.localdate()
    today_start, today_end = _local_day_bounds(today)
    three_days_ago_start, _ = _local_day_bounds(today - timedelta(days=3))
    window_start, _ = _local_day_bounds(today - timedelta(days=days - 1))
    trend_start, _ = _local_day_bounds(today - timedelta(days=6))

    client_qs = client_qs.select_related("status", "assigned_to")
    mission_bar = build_mission_bar(user, client_qs)
    overview = build_leads_overview(client_qs)

    # Mobile home only needs overview (+ mission_bar); skip heavy widgets.
    if lite:
        return {
            "mission_bar": mission_bar,
            "overview": overview,
            "stats": {
                "total_leads": overview["total"],
                "contact_today": mission_bar["contact_today"],
                "today_new_leads": mission_bar["today_new_leads"],
                "unassigned_leads": mission_bar["unassigned_leads"],
                "overdue_follow_ups": mission_bar["overdue_follow_ups"],
            },
            "lite": True,
            "days": days,
            "source": source,
        }

    # --- Stats ---
    total_leads = overview["total"]
    today_clients = client_qs.filter(created_at__gte=today_start, created_at__lt=today_end)
    today_touched = today_clients.exclude(status__name="Untouched").count()
    today_untouched = today_clients.filter(status__name="Untouched").count()

    # Parity with FE: unassigned, created before (today-3), no client-task on/after lead calendar day.
    delayed_candidates = list(
        client_qs.filter(
            created_at__lt=three_days_ago_start,
            assigned_to__isnull=True,
        ).values_list("id", "created_at")
    )
    delayed_leads = 0
    if delayed_candidates:
        candidate_ids = [cid for cid, _ in delayed_candidates]
        tasks_by_client: dict[int, list] = {}
        for cid, created in (
            ClientTask.objects.filter(client_id__in=candidate_ids)
            .values_list("client_id", "created_at")
        ):
            tasks_by_client.setdefault(cid, []).append(created)
        for cid, lead_created in delayed_candidates:
            lead_day = timezone.localtime(lead_created).date() if timezone.is_aware(lead_created) else lead_created.date()
            has_recent = False
            for act in tasks_by_client.get(cid, []):
                act_day = timezone.localtime(act).date() if timezone.is_aware(act) else act.date()
                if act_day >= lead_day:
                    has_recent = True
                    break
            if not has_recent:
                delayed_leads += 1

    deal_qs = scoped_deal_qs(user)
    total_deals = deal_qs.count()
    won_q = Q(stage__iexact="won") | Q(status__iexact="won")
    completed_deals = deal_qs.filter(won_q).count()
    open_agg = deal_qs.exclude(won_q).aggregate(
        pipeline=Coalesce(Sum("value"), Value(Decimal("0"))),
        open_count=Count("id"),
    )
    pipeline_sum = open_agg["pipeline"] or Decimal("0")
    value_agg = deal_qs.aggregate(
        total_value=Coalesce(Sum("value"), Value(Decimal("0"))),
    )
    total_value = float(value_agg["total_value"] or 0)
    avg_deal_size = int(round(total_value / total_deals)) if total_deals else 0
    win_rate = round((completed_deals / total_deals) * 100) if total_deals else 0
    active_todos = scoped_task_qs(user).count()

    stats = {
        "contact_today": mission_bar["contact_today"],
        "today_new_leads": mission_bar["today_new_leads"],
        "today_touched_leads": today_touched,
        "today_untouched_leads": today_untouched,
        "delayed_leads": delayed_leads,
        "total_leads": total_leads,
        "total_deals": total_deals,
        "active_todos": active_todos,
        "completed_deals": completed_deals,
        "pipeline_value": _format_pipeline_value(pipeline_sum),
        "pipeline_value_raw": float(pipeline_sum),
        "win_rate": win_rate,
        "average_deal_size": avg_deal_size,
        "unassigned_leads": mission_bar["unassigned_leads"],
        "overdue_follow_ups": mission_bar["overdue_follow_ups"],
    }

    # --- Week / trend series ---
    leads_for_chart = client_qs
    if source != "all":
        if source == "manual":
            leads_for_chart = client_qs.filter(Q(source="manual") | Q(source__isnull=True) | Q(source=""))
        else:
            leads_for_chart = client_qs.filter(source=source)

    lead_by_day = {
        row["day"]: row["c"]
        for row in leads_for_chart.filter(created_at__gte=window_start, created_at__lt=today_end)
        .annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(c=Count("id"))
    }
    week_series = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        week_series.append({"date": d.isoformat(), "leads_count": lead_by_day.get(d, 0)})

    # Trend always last 7 days (sparklines), unfiltered by source — matches current FE
    trend_lead_by_day = {
        row["day"]: row["c"]
        for row in client_qs.filter(created_at__gte=trend_start, created_at__lt=today_end)
        .annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(c=Count("id"))
    }
    contact_by_day = {
        row["day"]: row["c"]
        for row in scoped_client_task_qs(user)
        .filter(reminder_date__gte=trend_start, reminder_date__lt=today_end)
        .annotate(day=TruncDate("reminder_date", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(c=Count("id"))
    }
    leads_series = []
    contact_series = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        leads_series.append(trend_lead_by_day.get(d, 0))
        contact_series.append(contact_by_day.get(d, 0))

    # --- Funnel ---
    touched = client_qs.exclude(status__name__iexact="untouched").count()
    meeting = (
        scoped_client_task_qs(user)
        .filter(Q(stage__name__icontains="meeting"))
        .count()
    )
    funnel = {
        "total_leads": total_leads,
        "touched": touched,
        "meeting": meeting,
        "won": completed_deals,
    }

    # --- Stages (ClientTask stage counts, else lead status) ---
    stage_rows = list(
        scoped_client_task_qs(user)
        .values("stage__name")
        .annotate(value=Count("id"))
        .order_by("-value")
    )
    if any(r["stage__name"] for r in stage_rows):
        stages = [
            {"name": r["stage__name"] or "Untouched", "value": r["value"]}
            for r in stage_rows
            if r["value"]
        ]
    else:
        stages = [
            {"name": r["status__name"] or "Untouched", "value": r["value"]}
            for r in client_qs.values("status__name").annotate(value=Count("id"))
            if r["value"]
        ]

    # --- Company users ---
    company = getattr(user, "company", None)
    company_users = list(
        User.objects.filter(company=company, is_active=True).only(
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "last_seen_at",
        )
    ) if company else []
    user_by_id = {u.id: u for u in company_users}

    task_activity = {
        row["created_by_id"]: row["c"]
        for row in scoped_client_task_qs(user)
        .filter(created_by_id__isnull=False)
        .values("created_by_id")
        .annotate(c=Count("id"))
    }
    ranked = sorted(
        (
            {
                "id": u.id,
                "name": _user_display_name(u),
                "username": u.username,
                "role": u.role,
                "activity_count": task_activity.get(u.id, 0),
            }
            for u in company_users
        ),
        key=lambda r: r["activity_count"],
        reverse=True,
    )
    top_users = ranked[:3] if any(r["activity_count"] for r in ranked) else [
        {
            "id": u.id,
            "name": _user_display_name(u),
            "username": u.username,
            "role": u.role,
            "activity_count": 0,
        }
        for u in company_users[:3]
    ]

    now = timezone.now()
    presence_rows = []
    for u in company_users:
        if u.role in PRESENCE_EXCLUDED_ROLES:
            continue
        is_online = bool(u.last_seen_at and (now - u.last_seen_at) <= ONLINE_WINDOW)
        presence_rows.append(
            {
                "id": u.id,
                "name": _user_display_name(u),
                "username": u.username,
                "role": u.role,
                "is_online": is_online,
                "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
                "_seen_ts": u.last_seen_at.timestamp() if u.last_seen_at else 0,
            }
        )
    presence_rows.sort(key=lambda r: (0 if r["is_online"] else 1, -r["_seen_ts"]))
    employee_presence = [
        {k: v for k, v in row.items() if k != "_seen_ts"}
        for row in presence_rows[:8]
    ]

    # Team goals — today's client tasks by creator
    today_task_counts = {
        row["created_by_id"]: row["c"]
        for row in scoped_client_task_qs(user)
        .filter(
            created_by_id__isnull=False,
            created_at__gte=today_start,
            created_at__lt=today_end,
        )
        .values("created_by_id")
        .annotate(c=Count("id"))
    }
    team_goals = sorted(
        (
            {
                "id": u.id,
                "name": _user_display_name(u),
                "progress": today_task_counts.get(u.id, 0),
                "target": daily_target,
            }
            for u in company_users
            if u.role in TEAM_GOAL_ROLES
        ),
        key=lambda r: r["progress"],
        reverse=True,
    )

    # --- Contact today list ---
    contact_client_ids = list(
        client_qs.filter(
            assigned_to__isnull=False,
            client_tasks__reminder_date__gte=today_start,
            client_tasks__reminder_date__lt=today_end,
            client_tasks__reminder_completed_at__isnull=True,
        )
        .distinct()
        .values_list("id", flat=True)
    )
    contact_today_leads = []
    if contact_client_ids:
        clients = {
            c.id: c
            for c in client_qs.filter(id__in=contact_client_ids).select_related(
                "assigned_to", "status"
            )
        }
        open_today_tasks = (
            scoped_client_task_qs(user)
            .filter(
                client_id__in=contact_client_ids,
                reminder_date__gte=today_start,
                reminder_date__lt=today_end,
                reminder_completed_at__isnull=True,
            )
            .select_related("stage")
            .order_by("reminder_date")
        )
        task_by_client: dict[int, Any] = {}
        for ct in open_today_tasks:
            task_by_client.setdefault(ct.client_id, ct)
        for cid in contact_client_ids:
            lead = clients.get(cid)
            if not lead:
                continue
            task = task_by_client.get(cid)
            contact_today_leads.append(
                {
                    "id": lead.id,
                    "name": lead.name,
                    "assigned_user": _user_display_name(lead.assigned_to) or "Unknown",
                    "reminder_date": task.reminder_date.isoformat() if task and task.reminder_date else None,
                    "notes": (task.notes if task else "") or "",
                    "stage": (task.stage.name if task and task.stage_id else "") or "",
                }
            )
        contact_today_leads.sort(
            key=lambda r: (r["reminder_date"] or "9999", r["name"] or "")
        )

    # --- Activity timestamps for hot leads ---
    last_activity: dict[int, datetime] = {}

    def push_activity(cid, dt):
        if not cid or not dt:
            return
        prev = last_activity.get(cid)
        if prev is None or dt > prev:
            last_activity[cid] = dt

    for row in (
        scoped_client_task_qs(user)
        .values("client_id")
        .annotate(m=Max("created_at"), mr=Max("reminder_date"))
    ):
        push_activity(row["client_id"], row["m"])
        push_activity(row["client_id"], row["mr"])
    for row in scoped_call_qs(user).values("client_id").annotate(
        m=Max("created_at"), md=Max("call_datetime")
    ):
        push_activity(row["client_id"], row["m"])
        push_activity(row["client_id"], row["md"])
    for row in scoped_visit_qs(user).values("client_id").annotate(
        m=Max("created_at"), md=Max("visit_datetime")
    ):
        push_activity(row["client_id"], row["m"])
        push_activity(row["client_id"], row["md"])

    reminder_today_ids = set(
        scoped_client_task_qs(user)
        .filter(
            reminder_date__gte=today_start,
            reminder_date__lt=today_end,
        )
        .values_list("client_id", flat=True)
        .distinct()
    )

    # Latest activity map only for candidates we'll score (all scoped clients — needed for parity)
    client_rows = list(
        client_qs.values(
            "id",
            "name",
            "type",
            "priority",
            "notes",
            "assigned_to_id",
            "status__name",
        )
    )
    client_ids = [r["id"] for r in client_rows]
    latest_map = _latest_activity_maps(user, client_ids)

    now_ts = now.timestamp()
    hot_leads = []
    for row in client_rows:
        lead_id = row["id"]
        kind, activity = latest_map.get(lead_id, (None, None))
        last_stage = _activity_stage_text(kind, activity) or row["status__name"] or ""
        last_feedback = _activity_feedback_text(kind, activity) or row["notes"] or ""

        score = 0
        lead_type = (row["type"] or "").lower()
        if lead_type == "hot":
            score += 40
        elif lead_type == "fresh":
            score += 8
        priority = (row["priority"] or "").lower()
        if priority == "high":
            score += 30
        elif priority == "medium":
            score += 15
        stage_key = last_stage.lower().replace(" ", "_")
        if stage_key in HOT_STAGE_BOOST or last_stage.lower() in HOT_STAGE_BOOST:
            score += 25
        if stage_key in HOT_STAGE_PENALTY or last_stage.lower() in HOT_STAGE_PENALTY:
            score -= 20
        last_act = last_activity.get(lead_id)
        last_ts = last_act.timestamp() if last_act else 0
        if last_ts and now_ts - last_ts <= 3 * 24 * 3600:
            score += 15
        if not last_ts or now_ts - last_ts > 7 * 24 * 3600:
            score -= 15
        if last_stage.lower() == "untouched":
            score -= 10
        if lead_id in reminder_today_ids:
            score += 10

        bucket = "hot" if score >= 60 else "warm" if score >= 30 else "cold"
        if lead_type == "hot" and bucket != "hot":
            bucket = "hot"
        if bucket == "cold":
            continue

        assignee = user_by_id.get(row["assigned_to_id"])
        hot_leads.append(
            {
                "id": lead_id,
                "name": row["name"] or f"Lead #{lead_id}",
                "assigned_user": _user_display_name(assignee) or "Unknown",
                "stage": last_stage or "No Stage",
                "score": score,
                "bucket": bucket,
                "notes": last_feedback or "",
            }
        )
    hot_leads.sort(key=lambda r: r["score"], reverse=True)
    hot_leads = hot_leads[:6]

    # --- Latest feedbacks (top 5 with any last feedback) ---
    feedback_candidates = []
    for row in client_rows:
        kind, activity = latest_map.get(row["id"], (None, None))
        fb = _activity_feedback_text(kind, activity)
        if not fb:
            continue
        fb_at = activity.created_at if activity else None
        stage = _activity_stage_text(kind, activity) or ""
        assignee = user_by_id.get(row["assigned_to_id"])
        feedback_candidates.append(
            {
                "id": row["id"],
                "lead": row["name"] or "",
                "notes": fb,
                "stage": stage,
                "user": _user_display_name(assignee) or "Unknown",
                "last_feedback_at": fb_at.isoformat() if fb_at else None,
            }
        )
    feedback_candidates.sort(
        key=lambda r: r["last_feedback_at"] or "",
        reverse=True,
    )
    latest_feedbacks = feedback_candidates[:5]

    # --- Activity feed (latest 15 events) ---
    events = []
    for ct in (
        scoped_client_task_qs(user)
        .select_related("client", "stage", "created_by")
        .order_by("-created_at")[:15]
    ):
        events.append(
            {
                "id": f"task-{ct.id}",
                "kind": "task",
                "lead_id": ct.client_id,
                "lead_name": ct.client.name if ct.client_id else "",
                "actor_name": _user_display_name(ct.created_by) or "Unknown",
                "stage_name": ct.stage.name if ct.stage_id else "following",
                "created_at": ct.created_at.isoformat() if ct.created_at else None,
            }
        )
    for c in (
        scoped_call_qs(user)
        .select_related("client", "created_by")
        .order_by("-created_at")[:15]
    ):
        dt = c.created_at or getattr(c, "call_datetime", None)
        events.append(
            {
                "id": f"call-{c.id}",
                "kind": "call",
                "lead_id": c.client_id,
                "lead_name": c.client.name if c.client_id else "",
                "actor_name": _user_display_name(c.created_by) or "Unknown",
                "stage_name": "",
                "created_at": dt.isoformat() if dt else None,
            }
        )
    for v in (
        scoped_visit_qs(user)
        .select_related("client", "created_by")
        .order_by("-created_at")[:15]
    ):
        dt = v.created_at or getattr(v, "visit_datetime", None)
        events.append(
            {
                "id": f"visit-{v.id}",
                "kind": "visit",
                "lead_id": v.client_id,
                "lead_name": v.client.name if v.client_id else "",
                "actor_name": _user_display_name(v.created_by) or "Unknown",
                "stage_name": "",
                "created_at": dt.isoformat() if dt else None,
            }
        )
    events.sort(key=lambda e: e["created_at"] or "", reverse=True)
    activity_feed = events[:15]

    return {
        "mission_bar": mission_bar,
        "overview": overview,
        "stats": stats,
        "week_series": week_series,
        "trend_series": {
            "leads_series": leads_series,
            "contact_series": contact_series,
        },
        "funnel": funnel,
        "stages": stages,
        "top_users": top_users,
        "team_goals": team_goals,
        "hot_leads": hot_leads,
        "activity_feed": activity_feed,
        "latest_feedbacks": latest_feedbacks,
        "employee_presence": employee_presence,
        "contact_today_leads": contact_today_leads,
        "days": days,
        "source": source,
        "lite": False,
    }
