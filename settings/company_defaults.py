"""
Idempotent default rows for per-company CRM settings (channels, stages, statuses,
call methods, visit types), keyed by company.specialization.
"""

from __future__ import annotations

from django.db import transaction

from companies.models import Company, Specialization

from .lead_status_automation import VISITED_AUTOMATION_KEY
from .models import (
    CallMethod,
    Channel,
    ChannelPriority,
    LeadStage,
    LeadStatus,
    StatusCategory,
    VisitType,
)


def _ensure_single_default_channel(company) -> None:
    qs = Channel.objects.filter(company=company, is_active=True)
    if not qs.exists() or qs.filter(is_default=True).exists():
        return
    first = qs.order_by("id").first()
    qs.update(is_default=False)
    Channel.objects.filter(pk=first.pk).update(is_default=True)


def _ensure_single_default_stage(company) -> None:
    qs = LeadStage.objects.filter(company=company, is_active=True)
    if not qs.exists() or qs.filter(is_default=True).exists():
        return
    first = qs.order_by("order", "id").first()
    qs.update(is_default=False)
    LeadStage.objects.filter(pk=first.pk).update(is_default=True)


def _ensure_single_default_status(company) -> None:
    qs = LeadStatus.objects.filter(
        company=company, is_active=True, is_hidden=False
    )
    if not qs.exists() or qs.filter(is_default=True).exists():
        return
    first = qs.order_by("id").first()
    LeadStatus.objects.filter(company=company).update(is_default=False)
    LeadStatus.objects.filter(pk=first.pk).update(is_default=True)


def _ensure_single_default_call_method(company) -> None:
    qs = CallMethod.objects.filter(company=company, is_active=True)
    if not qs.exists() or qs.filter(is_default=True).exists():
        return
    first = qs.order_by("id").first()
    qs.update(is_default=False)
    CallMethod.objects.filter(pk=first.pk).update(is_default=True)


def _ensure_single_default_visit_type(company) -> None:
    qs = VisitType.objects.filter(company=company, is_active=True)
    if not qs.exists() or qs.filter(is_default=True).exists():
        return
    first = qs.order_by("id").first()
    qs.update(is_default=False)
    VisitType.objects.filter(pk=first.pk).update(is_default=True)


def _channels_for_spec(spec: str) -> list[dict]:
    # Same starter set; default channel fixed in _ensure_single_default_channel
    # (prefers first created: WhatsApp first in list)
    return [
        {
            "name": "WhatsApp",
            "type": "WhatsApp",
            "priority": ChannelPriority.MEDIUM.value,
        },
        {
            "name": "Phone",
            "type": "Phone",
            "priority": ChannelPriority.HIGH.value,
        },
        {
            "name": "Website",
            "type": "Web",
            "priority": ChannelPriority.MEDIUM.value,
        },
        {
            "name": "Social media",
            "type": "Social",
            "priority": ChannelPriority.LOW.value,
        },
    ]


def _stages_for_spec(spec: str) -> list[dict]:
    if spec == Specialization.REAL_ESTATE.value:
        return [
            {
                "name": "New inquiry",
                "description": "",
                "color": "#3b82f6",
                "required": False,
                "auto_advance": False,
                "order": 0,
            },
            {
                "name": "Qualification",
                "description": "",
                "color": "#8b5cf6",
                "required": False,
                "auto_advance": False,
                "order": 1,
            },
            {
                "name": "Viewing scheduled",
                "description": "",
                "color": "#f59e0b",
                "required": False,
                "auto_advance": False,
                "order": 2,
            },
            {
                "name": "Negotiation",
                "description": "",
                "color": "#ec4899",
                "required": False,
                "auto_advance": False,
                "order": 3,
            },
            {
                "name": "Closed",
                "description": "",
                "color": "#22c55e",
                "required": False,
                "auto_advance": False,
                "order": 4,
            },
        ]
    if spec == Specialization.SERVICES.value:
        return [
            {
                "name": "New lead",
                "description": "",
                "color": "#3b82f6",
                "required": False,
                "auto_advance": False,
                "order": 0,
            },
            {
                "name": "Discovery",
                "description": "",
                "color": "#8b5cf6",
                "required": False,
                "auto_advance": False,
                "order": 1,
            },
            {
                "name": "Proposal",
                "description": "",
                "color": "#f59e0b",
                "required": False,
                "auto_advance": False,
                "order": 2,
            },
            {
                "name": "On-site appointment",
                "description": "",
                "color": "#ec4899",
                "required": False,
                "auto_advance": False,
                "order": 3,
            },
            {
                "name": "Completed",
                "description": "",
                "color": "#22c55e",
                "required": False,
                "auto_advance": False,
                "order": 4,
            },
        ]
    # products
    return [
        {
            "name": "New inquiry",
            "description": "",
            "color": "#3b82f6",
            "required": False,
            "auto_advance": False,
            "order": 0,
        },
        {
            "name": "Quote sent",
            "description": "",
            "color": "#8b5cf6",
            "required": False,
            "auto_advance": False,
            "order": 1,
        },
        {
            "name": "Order placed",
            "description": "",
            "color": "#f59e0b",
            "required": False,
            "auto_advance": False,
            "order": 2,
        },
        {
            "name": "Fulfillment",
            "description": "",
            "color": "#ec4899",
            "required": False,
            "auto_advance": False,
            "order": 3,
        },
        {
            "name": "Closed",
            "description": "",
            "color": "#22c55e",
            "required": False,
            "auto_advance": False,
            "order": 4,
        },
    ]


def _statuses_for_spec(spec: str) -> list[dict]:
    base = [
        {
            "name": "New lead",
            "category": StatusCategory.ACTIVE.value,
            "color": "#3b82f6",
            "is_default": True,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": "Contacted",
            "category": StatusCategory.ACTIVE.value,
            "color": "#6366f1",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": "Qualified",
            "category": StatusCategory.ACTIVE.value,
            "color": "#8b5cf6",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": "Follow up",
            "category": StatusCategory.FOLLOW_UP.value,
            "color": "#f59e0b",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": "Closed won",
            "category": StatusCategory.CLOSED.value,
            "color": "#22c55e",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": "Closed lost",
            "category": StatusCategory.CLOSED.value,
            "color": "#64748b",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
    ]
    if spec in (
        Specialization.REAL_ESTATE.value,
        Specialization.SERVICES.value,
    ):
        visited = {
            "name": "Visited",
            "category": StatusCategory.ACTIVE.value,
            "color": "#6366f1",
            "is_default": False,
            "is_hidden": False,
            "automation_key": VISITED_AUTOMATION_KEY,
        }
        # Insert after "Follow up" conceptually — after Qualified, before Follow up
        out = base[:3] + [visited] + base[3:]
        return out
    return base


def _call_methods_for_spec(spec: str) -> list[dict]:
    return [
        {
            "name": "Phone call",
            "description": "",
            "color": "#3b82f6",
        },
        {
            "name": "WhatsApp",
            "description": "",
            "color": "#22c55e",
        },
        {
            "name": "Video call",
            "description": "",
            "color": "#8b5cf6",
        },
    ]


def _visit_types_for_spec(spec: str) -> list[dict]:
    if spec == Specialization.REAL_ESTATE.value:
        return [
            {
                "name": "Property viewing",
                "description": "",
                "color": "#3b82f6",
            },
            {
                "name": "Office consultation",
                "description": "",
                "color": "#8b5cf6",
            },
            {
                "name": "Virtual tour",
                "description": "",
                "color": "#f59e0b",
            },
        ]
    if spec == Specialization.SERVICES.value:
        return [
            {
                "name": "On-site service",
                "description": "",
                "color": "#3b82f6",
            },
            {
                "name": "Office appointment",
                "description": "",
                "color": "#8b5cf6",
            },
            {
                "name": "Remote consultation",
                "description": "",
                "color": "#f59e0b",
            },
        ]
    return []


def _mark_default_stage(company, name: str) -> None:
    if not LeadStage.objects.filter(company=company, name=name).exists():
        _ensure_single_default_stage(company)
        return
    LeadStage.objects.filter(company=company).update(is_default=False)
    LeadStage.objects.filter(company=company, name=name).update(is_default=True)


def seed_company_settings(company: Company) -> None:
    """
    Create starter settings rows for this company if missing (idempotent).
    Safe to call multiple times (e.g. signal + migration + serializer safety net).
    """
    if not company or not company.pk:
        return

    spec = company.specialization or Specialization.REAL_ESTATE.value

    with transaction.atomic():
        for row in _channels_for_spec(spec):
            Channel.objects.get_or_create(
                company=company,
                name=row["name"],
                defaults={
                    "type": row["type"],
                    "priority": row["priority"],
                    "is_active": True,
                    "is_default": False,
                },
            )
        _ensure_single_default_channel(company)

        default_stage_name = _stages_for_spec(spec)[0]["name"]
        for row in _stages_for_spec(spec):
            LeadStage.objects.get_or_create(
                company=company,
                name=row["name"],
                defaults={
                    "description": row["description"],
                    "color": row["color"],
                    "required": row["required"],
                    "auto_advance": row["auto_advance"],
                    "order": row["order"],
                    "is_active": True,
                    "is_default": row["name"] == default_stage_name,
                },
            )
        _ensure_single_default_stage(company)
        _mark_default_stage(company, default_stage_name)

        for row in _statuses_for_spec(spec):
            automation_key = row.get("automation_key")
            defaults = {
                "description": "",
                "category": row["category"],
                "color": row["color"],
                "is_default": row["is_default"],
                "is_hidden": row["is_hidden"],
                "is_active": True,
            }
            if automation_key:
                defaults["automation_key"] = automation_key
            LeadStatus.objects.get_or_create(
                company=company,
                name=row["name"],
                defaults=defaults,
            )
        _ensure_single_default_status(company)

        for row in _call_methods_for_spec(spec):
            CallMethod.objects.get_or_create(
                company=company,
                name=row["name"],
                defaults={
                    "description": row["description"],
                    "color": row["color"],
                    "is_active": True,
                    "is_default": False,
                },
            )
        _ensure_single_default_call_method(company)

        for row in _visit_types_for_spec(spec):
            VisitType.objects.get_or_create(
                company=company,
                name=row["name"],
                defaults={
                    "description": row["description"],
                    "color": row["color"],
                    "is_active": True,
                    "is_default": False,
                },
            )
        if spec in (
            Specialization.REAL_ESTATE.value,
            Specialization.SERVICES.value,
        ):
            _ensure_single_default_visit_type(company)
