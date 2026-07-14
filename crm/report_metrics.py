"""Shared aggregation helpers for CRM tenant reports."""

from __future__ import annotations

import re
from datetime import datetime, time
from decimal import Decimal
from typing import Iterable

from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import Role, User
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
    disposition = ""
    if call.pbx_call_record_id and call.pbx_call_record:
        disposition = (call.pbx_call_record.disposition or "").lower()
    if disposition == "answered":
        return "answered"
    if disposition in {"no_answer", "busy", "missed"}:
        return "missed"

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
        "call_method", "pbx_call_record", "created_by"
    )
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    return qs


def _build_deals_by_assignee(company, clients: list[Client]) -> dict[int, list[Deal]]:
    client_ids = [client.id for client in clients]
    assignee_by_client = {client.id: client.assigned_to_id for client in clients}
    deals = Deal.objects.filter(company=company, client_id__in=client_ids)
    grouped: dict[int, list[Deal]] = {}
    for deal in deals:
        assignee_id = assignee_by_client.get(deal.client_id)
        if not assignee_id:
            continue
        grouped.setdefault(assignee_id, []).append(deal)
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
            "won_deals": sum(1 for deal in user_deals if (deal.stage or "").lower() == "won"),
            "total_client_calls": len(user_calls),
            "total_activities": len(user_calls),
            "following_leads": 0,
            "meeting_leads": 0,
        }
        row["following_leads"] = row["following"]
        row["meeting_leads"] = row["meeting"]

        if (
            row["total_leads"]
            or row["total_deals"]
            or row["total_calls"]
        ):
            rows.append(row)

    summary = {
        "total_calls": sum(row["total_calls"] for row in rows),
        "answered_calls": sum(row["answered_calls"] for row in rows),
        "not_answered_calls": sum(row["not_answered_calls"] for row in rows),
        "employee_count": len(rows),
        "total_teams": len(rows),
        "total_leads": sum(row["total_leads"] for row in rows),
        "total_activities": sum(row["total_activities"] for row in rows),
        "total_deals": sum(row["total_deals"] for row in rows),
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
    pbx_linked = 0
    answered = 0
    missed = 0
    unknown = 0
    by_user: dict[int, dict] = {}
    by_method: dict[str, dict] = {}

    for call in calls:
        is_manual = call.source == "manual" or not call.pbx_call_record_id
        if is_manual:
            manual += 1
        else:
            pbx_linked += 1

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
                "pbx_linked": 0,
            },
        )
        user_bucket["total"] += 1
        if kind == "answered" or kind == "unknown":
            user_bucket["answered"] += 1
        if kind == "missed":
            user_bucket["missed"] += 1
        if is_manual:
            user_bucket["manual"] += 1
        else:
            user_bucket["pbx_linked"] += 1

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
            "pbx_linked": pbx_linked,
            "answered": answered,
            "missed": missed,
            "unknown": unknown,
        },
        "by_user": sorted(by_user.values(), key=lambda row: row["name"].lower()),
        "by_method": sorted(by_method.values(), key=lambda row: (-row["total"], row["name"].lower())),
    }


def _build_pbx_report_section(company, from_date: str | None, to_date: str | None):
    from django.db.models import Avg

    from integrations.models import (
        PbxCallDisposition,
        PbxCallDirection,
        PbxCallRecord,
        PbxEventType,
        PbxSettings,
        UserPbxExtension,
    )

    try:
        settings = PbxSettings.objects.get(company=company)
    except PbxSettings.DoesNotExist:
        return {"enabled": False, "summary": None, "agents": [], "_from_date": from_date, "_to_date": to_date}

    if not settings.is_enabled:
        return {"enabled": False, "summary": None, "agents": [], "_from_date": from_date, "_to_date": to_date}

    qs = PbxCallRecord.objects.filter(company=company, event_type=PbxEventType.HANGUP)
    if from_date:
        qs = qs.filter(started_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(started_at__date__lte=to_date)

    summary = {
        "total": qs.count(),
        "inbound": qs.filter(direction=PbxCallDirection.INBOUND).count(),
        "outbound": qs.filter(direction=PbxCallDirection.OUTBOUND).count(),
        "answered": qs.filter(disposition=PbxCallDisposition.ANSWERED).count(),
        "missed": qs.filter(
            disposition__in=[PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY]
        ).count(),
        "avg_duration_sec": round(qs.aggregate(avg=Avg("billsec"))["avg"] or 0, 1),
    }

    agents = []
    for ext in qs.values_list("extension", flat=True).distinct():
        if not ext:
            continue
        ext_qs = qs.filter(extension=ext)
        mapping = (
            UserPbxExtension.objects.filter(company=company, extension=ext)
            .select_related("user")
            .first()
        )
        agents.append(
            {
                "extension": ext,
                "user_id": mapping.user_id if mapping else None,
                "username": mapping.user.username if mapping else None,
                "total": ext_qs.count(),
                "answered": ext_qs.filter(disposition=PbxCallDisposition.ANSWERED).count(),
                "missed": ext_qs.filter(
                    disposition__in=[PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY]
                ).count(),
                "avg_duration_sec": round(ext_qs.aggregate(avg=Avg("billsec"))["avg"] or 0, 1),
            }
        )

    return {
        "enabled": True,
        "summary": summary,
        "agents": agents,
        "_from_date": from_date,
        "_to_date": to_date,
    }


def _build_combined_call_summary(crm_summary: dict, pbx_section: dict, company):
    from integrations.models import PbxCallDisposition, PbxCallRecord, PbxEventType

    combined = {
        "total": crm_summary["total"],
        "answered": crm_summary["answered"],
        "missed": crm_summary["missed"],
        "manual": crm_summary["manual"],
        "pbx_cdr_unlinked": 0,
        "avg_duration_sec": 0.0,
    }

    if not pbx_section.get("enabled") or not pbx_section.get("summary"):
        return combined

    linked_ids = set(
        ClientCall.objects.filter(
            client__company=company,
            pbx_call_record_id__isnull=False,
        ).values_list("pbx_call_record_id", flat=True)
    )

    pbx_qs = PbxCallRecord.objects.filter(
        company=company,
        event_type=PbxEventType.HANGUP,
    )
    if pbx_section.get("_from_date"):
        pbx_qs = pbx_qs.filter(started_at__date__gte=pbx_section["_from_date"])
    if pbx_section.get("_to_date"):
        pbx_qs = pbx_qs.filter(started_at__date__lte=pbx_section["_to_date"])

    unlinked_qs = pbx_qs.exclude(id__in=linked_ids)
    unlinked_total = unlinked_qs.count()
    unlinked_answered = unlinked_qs.filter(disposition=PbxCallDisposition.ANSWERED).count()
    unlinked_missed = unlinked_qs.filter(
        disposition__in=[PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY]
    ).count()

    combined["pbx_cdr_unlinked"] = unlinked_total
    combined["total"] = crm_summary["total"] + unlinked_total
    combined["answered"] = crm_summary["answered"] + unlinked_answered
    combined["missed"] = crm_summary["missed"] + unlinked_missed

    duration_values = list(unlinked_qs.values_list("billsec", flat=True))
    linked_calls = ClientCall.objects.filter(
        client__company=company,
        pbx_call_record_id__in=pbx_qs.values_list("id", flat=True),
    ).select_related("pbx_call_record")
    for call in linked_calls:
        if call.pbx_call_record:
            duration_values.append(call.pbx_call_record.billsec or 0)
    if duration_values:
        combined["avg_duration_sec"] = round(sum(duration_values) / len(duration_values), 1)
    elif pbx_section["summary"]:
        combined["avg_duration_sec"] = pbx_section["summary"]["avg_duration_sec"]

    return combined


def build_call_report(
    company,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    user_id: int | None = None,
):
    start_dt, end_dt = _date_bounds(from_date, to_date)
    calls_qs = _filter_calls(company, start_dt, end_dt).select_related(
        "client", "call_method", "pbx_call_record", "created_by"
    )
    if user_id:
        calls_qs = calls_qs.filter(created_by_id=user_id)
    calls = list(calls_qs)

    crm = _summarize_crm_calls(calls)
    pbx = _build_pbx_report_section(company, from_date, to_date)
    combined = _build_combined_call_summary(crm["summary"], pbx, company)

    return {
        "crm": crm,
        "pbx": {
            "enabled": pbx["enabled"],
            "summary": pbx["summary"],
            "agents": pbx["agents"],
        },
        "combined": {"summary": combined},
    }
