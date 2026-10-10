"""Shared aggregation helpers for CRM tenant reports."""

from __future__ import annotations

import re
from datetime import datetime, time
from decimal import Decimal
from typing import Iterable

from django.db.models import QuerySet, Sum
from django.utils import timezone

from accounts.models import Role, User, WorkDaySummary
from crm.models import Campaign, Client, ClientCall, Deal
from settings.models import LeadStatus, StatusCategory

UNTOUCHED_SLUGS = {"untouched", "new_lead", "new", "newlead"}
FOLLOWING_SLUGS = {"following", "follow_up", "followup", "follow-up"}
MEETING_SLUGS = {"meeting", "qualified", "done_meeting", "done meeting"}
NO_ANSWER_SLUGS = {"no_answer", "no answer", "not_answered", "not answered"}
OUT_OF_SERVICE_SLUGS = {"out_of_service", "out of service", "outofservice"}
CONVERTED_SLUGS = MEETING_SLUGS | FOLLOWING_SLUGS | {
    "closed_won",
    "closed won",
    "won",
    "contacted",
}

STAFF_ROLES = [
    Role.ADMIN.value,
    Role.SUPERVISOR.value,
    Role.EMPLOYEE.value,
    Role.RECEPTION.value,
    Role.DOCTOR.value,
]


def status_slug(value: str | None) -> str:
    return re.sub(r"\s+", "_", (value or "").strip().lower()).replace("-", "_")


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_bounds(from_date: str | None, to_date: str | None):
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    start_dt = timezone.make_aware(datetime.combine(start, time.min)) if start else None
    end_dt = timezone.make_aware(datetime.combine(end, time.max)) if end else None
    return start_dt, end_dt


def _filter_clients(
    company,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    lead_type: str | None = None,
    user_id: int | None = None,
    campaign_id: int | None = None,
) -> QuerySet[Client]:
    qs = Client.objects.filter(company=company).select_related("status", "assigned_to")
    start_dt, end_dt = _date_bounds(from_date, to_date)
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    if lead_type and lead_type.lower() != "all":
        qs = qs.filter(type__iexact=lead_type)
    if user_id:
        qs = qs.filter(assigned_to_id=user_id)
    if campaign_id:
        qs = qs.filter(campaign_id=campaign_id)
    return qs


def _default_status_ids(company) -> set[int]:
    return set(
        LeadStatus.objects.filter(company=company, is_default=True).values_list("id", flat=True)
    )


def _status_maps(company):
    statuses = list(
        LeadStatus.objects.filter(company=company, is_active=True).values(
            "id", "name", "category", "is_default"
        )
    )
    category_by_id = {row["id"]: (row.get("category") or "").lower() for row in statuses}
    default_ids = {row["id"] for row in statuses if row.get("is_default")}
    return statuses, category_by_id, default_ids


def _lead_status_name(client: Client) -> str:
    return client.status.name if client.status_id and client.status else ""


def is_untouched_lead(client: Client, default_ids: set[int], category_by_id: dict[int, str]) -> bool:
    slug = status_slug(_lead_status_name(client))
    if slug in UNTOUCHED_SLUGS:
        return True
    if client.status_id in default_ids:
        return True
    category = category_by_id.get(client.status_id or 0)
    if category == StatusCategory.INACTIVE.value:
        return True
    return slug == ""


def matches_status_bucket(
    client: Client,
    bucket: str,
    category_by_id: dict[int, str],
) -> bool:
    slug = status_slug(_lead_status_name(client))
    category = category_by_id.get(client.status_id or 0)

    if bucket == "following":
        return slug in FOLLOWING_SLUGS or category == StatusCategory.FOLLOW_UP.value
    if bucket == "meeting":
        return slug in MEETING_SLUGS or "meeting" in slug
    if bucket == "no_answer":
        return slug in NO_ANSWER_SLUGS or "no_answer" in slug or "noanswer" in slug
    if bucket == "out_of_service":
        return slug in OUT_OF_SERVICE_SLUGS or "out_of_service" in slug
    return False


def is_converted_lead(client: Client, category_by_id: dict[int, str]) -> bool:
    slug = status_slug(_lead_status_name(client))
    if slug in CONVERTED_SLUGS or "won" in slug:
        return True
    category = category_by_id.get(client.status_id or 0)
    if category == StatusCategory.FOLLOW_UP.value:
        return True
    if category == StatusCategory.CLOSED.value and "won" in slug:
        return True
    return matches_status_bucket(client, "meeting", category_by_id) or matches_status_bucket(
        client, "following", category_by_id
    )


def _classify_call(call: ClientCall) -> str:
    method = (call.call_method.name if call.call_method_id and call.call_method else "").lower()
    if "no answer" in method or "not answered" in method:
        return "missed"
    if "answered" in method or "following" in method:
        return "answered"
    return "unknown"


def _report_users(company, user_id: int | None = None) -> Iterable[User]:
    qs = User.objects.filter(company=company, is_active=True, role__in=STAFF_ROLES).order_by(
        "first_name", "last_name", "username"
    )
    if user_id:
        qs = qs.filter(id=user_id)
    return qs


def _user_display_name(user: User) -> str:
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or user.username or user.email or f"User {user.id}"


def _filter_calls(company, start_dt, end_dt):
    qs = ClientCall.objects.filter(client__company=company).select_related(
        "call_method", "created_by"
    )
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    return qs


def _worked_seconds_by_user(
    company,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    user_id: int | None = None,
) -> dict[int, int]:
    """
    Measured CRM usage seconds per user for the range, as one grouped aggregate.

    Filters on the raw parsed dates rather than :func:`_date_bounds`, because
    ``work_date`` is already a company-local calendar date while ``_date_bounds``
    builds aware datetimes in the Django server timezone. Company-local days are the
    right semantics for "hours worked on 2026-08-20"; note this can diverge by up to
    a day from the lead/call windows for tenants whose timezone isn't the server's.
    """
    qs = WorkDaySummary.objects.filter(company=company)
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    if start:
        qs = qs.filter(work_date__gte=start)
    if end:
        qs = qs.filter(work_date__lte=end)
    if user_id:
        qs = qs.filter(user_id=user_id)
    return {
        row_user_id: total or 0
        for row_user_id, total in qs.values("user_id")
        .annotate(total=Sum("active_seconds"))
        .values_list("user_id", "total")
    }


def _deal_is_won(deal) -> bool:
    from crm.deals.legacy import is_won_deal

    return is_won_deal(deal)


def _build_deals_by_assignee(company, clients: list[Client]) -> dict[int, list[Deal]]:
    """Group deals by the deal owner (deal.employee), not the lead assignee."""
    client_ids = [client.id for client in clients]
    deals = Deal.objects.filter(company=company, client_id__in=client_ids).select_related(
        "pipeline_stage"
    )
    grouped: dict[int, list[Deal]] = {}
    for deal in deals:
        if not deal.employee_id:
            continue
        grouped.setdefault(deal.employee_id, []).append(deal)
    return grouped


def build_employee_or_team_rows(
    company,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    lead_type: str | None = None,
    user_id: int | None = None,
):
    _, category_by_id, default_ids = _status_maps(company)
    clients = list(
        _filter_clients(
            company,
            from_date=from_date,
            to_date=to_date,
            lead_type=lead_type,
            user_id=user_id,
        )
    )
    start_dt, end_dt = _date_bounds(from_date, to_date)
    calls = list(_filter_calls(company, start_dt, end_dt))
    deals_by_assignee = _build_deals_by_assignee(company, clients)
    worked_seconds_by_user = _worked_seconds_by_user(
        company, from_date=from_date, to_date=to_date, user_id=user_id
    )

    leads_by_assignee: dict[int, list[Client]] = {}
    for client in clients:
        if not client.assigned_to_id:
            continue
        leads_by_assignee.setdefault(client.assigned_to_id, []).append(client)

    calls_by_user: dict[int, list[ClientCall]] = {}
    for call in calls:
        if not call.created_by_id:
            continue
        calls_by_user.setdefault(call.created_by_id, []).append(call)

    rows = []
    for user in _report_users(company, user_id=user_id):
        user_leads = leads_by_assignee.get(user.id, [])
        user_calls = calls_by_user.get(user.id, [])
        user_deals = deals_by_assignee.get(user.id, [])

        answered = 0
        missed = 0
        for call in user_calls:
            kind = _classify_call(call)
            if kind == "missed":
                missed += 1
            else:
                answered += 1

        row = {
            "id": user.id,
            "name": _user_display_name(user),
            "total_leads": len(user_leads),
            "touched_leads": sum(
                1 for lead in user_leads if not is_untouched_lead(lead, default_ids, category_by_id)
            ),
            "untouched_leads": sum(
                1 for lead in user_leads if is_untouched_lead(lead, default_ids, category_by_id)
            ),
            "following": sum(
                1
                for lead in user_leads
                if matches_status_bucket(lead, "following", category_by_id)
            ),
            "meeting": sum(
                1 for lead in user_leads if matches_status_bucket(lead, "meeting", category_by_id)
            ),
            "no_answer": sum(
                1 for lead in user_leads if matches_status_bucket(lead, "no_answer", category_by_id)
            ),
            "out_of_service": sum(
                1
                for lead in user_leads
                if matches_status_bucket(lead, "out_of_service", category_by_id)
            ),
            "total_calls": len(user_calls),
            "answered_calls": answered,
            "not_answered_calls": missed,
            "total_deals": len(user_deals),
            "won_deals": sum(1 for deal in user_deals if _deal_is_won(deal)),
            "total_client_calls": len(user_calls),
            "total_activities": len(user_calls),
            "worked_seconds": worked_seconds_by_user.get(user.id, 0),
            "following_leads": 0,
            "meeting_leads": 0,
        }
        row["following_leads"] = row["following"]
        row["meeting_leads"] = row["meeting"]

        # Measured hours count as activity: without this, someone who logged time but
        # was assigned no leads/deals/calls would disappear from the report entirely.
        if (
            row["total_leads"]
            or row["total_deals"]
            or row["total_calls"]
            or row["worked_seconds"]
        ):
            rows.append(row)

    total_worked_seconds = sum(row["worked_seconds"] for row in rows)
    summary = {
        "total_calls": sum(row["total_calls"] for row in rows),
        "answered_calls": sum(row["answered_calls"] for row in rows),
        "not_answered_calls": sum(row["not_answered_calls"] for row in rows),
        "employee_count": len(rows),
        "total_teams": len(rows),
        "total_leads": sum(row["total_leads"] for row in rows),
        "total_activities": sum(row["total_activities"] for row in rows),
        "total_deals": sum(row["total_deals"] for row in rows),
        "total_worked_seconds": total_worked_seconds,
        "avg_worked_seconds_per_employee": (
            total_worked_seconds // len(rows) if rows else 0
        ),
    }
    return rows, summary


def build_marketing_rows(
    company,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    lead_type: str | None = None,
    campaign_id: int | None = None,
):
    _, category_by_id, default_ids = _status_maps(company)
    campaigns_qs = Campaign.objects.filter(company=company, is_active=True).order_by("name")
    if campaign_id:
        campaigns_qs = campaigns_qs.filter(id=campaign_id)

    clients = list(
        _filter_clients(
            company,
            from_date=from_date,
            to_date=to_date,
            lead_type=lead_type,
            campaign_id=campaign_id,
        )
    )
    leads_by_campaign: dict[int, list[Client]] = {}
    for client in clients:
        if not client.campaign_id:
            continue
        leads_by_campaign.setdefault(client.campaign_id, []).append(client)

    rows = []
    for campaign in campaigns_qs:
        campaign_leads = leads_by_campaign.get(campaign.id, [])
        converted = sum(1 for lead in campaign_leads if is_converted_lead(lead, category_by_id))
        total_leads = len(campaign_leads)
        budget = Decimal(campaign.budget or 0)
        conversion_rate = (converted / total_leads * 100) if total_leads else 0
        cost_per_lead = (budget / total_leads) if total_leads else Decimal("0")

        rows.append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "budget": float(budget),
                "total_leads": total_leads,
                "converted_leads": converted,
                "conversion_rate": f"{conversion_rate:.1f}",
                "cost_per_lead": f"{cost_per_lead:.2f}",
            }
        )

    avg_conversion = (
        sum(float(row["conversion_rate"]) for row in rows) / len(rows) if rows else 0
    )
    summary = {
        "total_campaigns": len(rows),
        "total_budget": sum(row["budget"] for row in rows),
        "total_leads": sum(row["total_leads"] for row in rows),
        "avg_conversion_rate": f"{avg_conversion:.1f}",
    }
    return rows, summary


def _summarize_crm_calls(calls: list[ClientCall]) -> dict:
    manual = 0
    answered = 0
    missed = 0
    unknown = 0
    by_user: dict[int, dict] = {}
    by_method: dict[str, dict] = {}

    for call in calls:
        if call.source == "manual":
            manual += 1

        kind = _classify_call(call)
        if kind == "answered":
            answered += 1
        elif kind == "missed":
            missed += 1
        else:
            unknown += 1

        user_id = call.created_by_id or 0
        user_name = _user_display_name(call.created_by) if call.created_by_id else "Unknown"
        user_bucket = by_user.setdefault(
            user_id,
            {
                "id": user_id or None,
                "name": user_name,
                "total": 0,
                "answered": 0,
                "missed": 0,
                "manual": 0,
            },
        )
        user_bucket["total"] += 1
        if kind == "answered" or kind == "unknown":
            user_bucket["answered"] += 1
        if kind == "missed":
            user_bucket["missed"] += 1
        if call.source == "manual":
            user_bucket["manual"] += 1

        method_name = (
            call.call_method.name if call.call_method_id and call.call_method else "Unspecified"
        )
        method_bucket = by_method.setdefault(
            method_name,
            {"name": method_name, "total": 0, "answered": 0, "missed": 0},
        )
        method_bucket["total"] += 1
        if kind == "missed":
            method_bucket["missed"] += 1
        else:
            method_bucket["answered"] += 1

    return {
        "summary": {
            "total": len(calls),
            "manual": manual,
            "answered": answered,
            "missed": missed,
            "unknown": unknown,
        },
        "by_user": sorted(by_user.values(), key=lambda row: row["name"].lower()),
        "by_method": sorted(by_method.values(), key=lambda row: (-row["total"], row["name"].lower())),
    }


def build_call_report(
    company,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    user_id: int | None = None,
):
    start_dt, end_dt = _date_bounds(from_date, to_date)
    calls_qs = _filter_calls(company, start_dt, end_dt).select_related(
        "client", "call_method", "created_by"
    )
    if user_id:
        calls_qs = calls_qs.filter(created_by_id=user_id)
    calls = list(calls_qs)

    crm = _summarize_crm_calls(calls)

    return {
        "crm": crm,
    }
