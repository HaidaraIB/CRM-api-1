"""
Idempotent default rows for per-company CRM settings (channels, stages, statuses,
call methods, visit types), keyed by company.specialization.

Stored names are always the English seed keys. The UI translates those keys
for the current language. Enum fields such as channel type stay in their
stored keys.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.utils import IntegrityError

from companies.models import Company, Specialization
from crm_saas_api.db_sequences import reset_pk_sequences

from .lead_status_automation import VISITED_AUTOMATION_KEY
from .seed_i18n import seed_label, seed_language_for_company
from .models import (
    CallMethod,
    Channel,
    ChannelPriority,
    LeadStage,
    LeadStatus,
    StatusCategory,
    VisitType,
)

logger = logging.getLogger(__name__)

_SEED_MODELS = (Channel, LeadStage, LeadStatus, CallMethod, VisitType)


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


def _channels_for_spec(spec: str, lang: str) -> list[dict]:
    # Same starter set; default channel fixed in _ensure_single_default_channel
    # (prefers first created: WhatsApp first in list). `type` stays the enum key.
    return [
        {
            "name": seed_label(lang, "WhatsApp", "واتساب"),
            "type": "WhatsApp",
            "priority": ChannelPriority.MEDIUM.value,
        },
        {
            "name": seed_label(lang, "Phone", "الهاتف"),
            "type": "Phone",
            "priority": ChannelPriority.HIGH.value,
        },
        {
            "name": seed_label(lang, "Website", "الموقع الإلكتروني"),
            "type": "Web",
            "priority": ChannelPriority.MEDIUM.value,
        },
        {
            "name": seed_label(lang, "Social media", "وسائل التواصل"),
            "type": "Social",
            "priority": ChannelPriority.LOW.value,
        },
    ]


def _stages_for_spec(spec: str, lang: str) -> list[dict]:
    if spec == Specialization.REAL_ESTATE.value:
        return [
            {
                "name": seed_label(lang, "New inquiry", "استفسار جديد"),
                "description": "",
                "color": "#3b82f6",
                "required": False,
                "auto_advance": False,
                "order": 0,
            },
            {
                "name": seed_label(lang, "Qualification", "تأهيل"),
                "description": "",
                "color": "#8b5cf6",
                "required": False,
                "auto_advance": False,
                "order": 1,
            },
            {
                "name": seed_label(lang, "Viewing scheduled", "معاينة مجدولة"),
                "description": "",
                "color": "#f59e0b",
                "required": False,
                "auto_advance": False,
                "order": 2,
            },
            {
                "name": seed_label(lang, "Negotiation", "تفاوض"),
                "description": "",
                "color": "#ec4899",
                "required": False,
                "auto_advance": False,
                "order": 3,
            },
            {
                "name": seed_label(lang, "Closed", "مغلق"),
                "description": "",
                "color": "#22c55e",
                "required": False,
                "auto_advance": False,
                "order": 4,
            },
        ]
    if spec in (Specialization.SERVICES.value, Specialization.MEDICAL.value):
        if spec == Specialization.MEDICAL.value:
            return [
                {
                    "name": seed_label(lang, "New patient", "مريض جديد"),
                    "description": "",
                    "color": "#3b82f6",
                    "required": False,
                    "auto_advance": False,
                    "order": 0,
                },
                {
                    "name": seed_label(lang, "Registered", "مسجّل"),
                    "description": "",
                    "color": "#8b5cf6",
                    "required": False,
                    "auto_advance": False,
                    "order": 1,
                },
                {
                    "name": seed_label(lang, "With doctor", "عند الطبيب"),
                    "description": "",
                    "color": "#f59e0b",
                    "required": False,
                    "auto_advance": False,
                    "order": 2,
                },
                {
                    "name": seed_label(lang, "Follow-up", "متابعة"),
                    "description": "",
                    "color": "#ec4899",
                    "required": False,
                    "auto_advance": False,
                    "order": 3,
                },
                {
                    "name": seed_label(lang, "Completed", "مكتمل"),
                    "description": "",
                    "color": "#22c55e",
                    "required": False,
                    "auto_advance": False,
                    "order": 4,
                },
            ]
        return [
            {
                "name": seed_label(lang, "New lead", "عميل جديد"),
                "description": "",
                "color": "#3b82f6",
                "required": False,
                "auto_advance": False,
                "order": 0,
            },
            {
                "name": seed_label(lang, "Discovery", "استكشاف"),
                "description": "",
                "color": "#8b5cf6",
                "required": False,
                "auto_advance": False,
                "order": 1,
            },
            {
                "name": seed_label(lang, "Proposal", "عرض سعر"),
                "description": "",
                "color": "#f59e0b",
                "required": False,
                "auto_advance": False,
                "order": 2,
            },
            {
                "name": seed_label(lang, "On-site appointment", "موعد ميداني"),
                "description": "",
                "color": "#ec4899",
                "required": False,
                "auto_advance": False,
                "order": 3,
            },
            {
                "name": seed_label(lang, "Completed", "مكتمل"),
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
            "name": seed_label(lang, "New inquiry", "استفسار جديد"),
            "description": "",
            "color": "#3b82f6",
            "required": False,
            "auto_advance": False,
            "order": 0,
        },
        {
            "name": seed_label(lang, "Quote sent", "تم إرسال العرض"),
            "description": "",
            "color": "#8b5cf6",
            "required": False,
            "auto_advance": False,
            "order": 1,
        },
        {
            "name": seed_label(lang, "Order placed", "تم الطلب"),
            "description": "",
            "color": "#f59e0b",
            "required": False,
            "auto_advance": False,
            "order": 2,
        },
        {
            "name": seed_label(lang, "Fulfillment", "تجهيز الطلب"),
            "description": "",
            "color": "#ec4899",
            "required": False,
            "auto_advance": False,
            "order": 3,
        },
        {
            "name": seed_label(lang, "Closed", "مغلق"),
            "description": "",
            "color": "#22c55e",
            "required": False,
            "auto_advance": False,
            "order": 4,
        },
    ]


def _statuses_for_spec(spec: str, lang: str) -> list[dict]:
    base = [
        {
            "name": seed_label(lang, "New lead", "عميل جديد"),
            "category": StatusCategory.ACTIVE.value,
            "color": "#3b82f6",
            "is_default": True,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": seed_label(lang, "Contacted", "تم التواصل"),
            "category": StatusCategory.ACTIVE.value,
            "color": "#6366f1",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": seed_label(lang, "Qualified", "مؤهل"),
            "category": StatusCategory.ACTIVE.value,
            "color": "#8b5cf6",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": seed_label(lang, "Follow up", "متابعة"),
            "category": StatusCategory.FOLLOW_UP.value,
            "color": "#f59e0b",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": seed_label(lang, "Closed won", "مغلق رابح"),
            "category": StatusCategory.CLOSED.value,
            "color": "#22c55e",
            "is_default": False,
            "is_hidden": False,
            "automation_key": None,
        },
        {
            "name": seed_label(lang, "Closed lost", "مغلق خاسر"),
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
        Specialization.MEDICAL.value,
    ):
        visited = {
            "name": seed_label(lang, "Visited", "تمت الزيارة"),
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


def _call_methods_for_spec(spec: str, lang: str) -> list[dict]:
    return [
        {
            "name": seed_label(lang, "Phone call", "مكالمة هاتفية"),
            "description": "",
            "color": "#3b82f6",
        },
        {
            "name": seed_label(lang, "WhatsApp", "واتساب"),
            "description": "",
            "color": "#22c55e",
        },
        {
            "name": seed_label(lang, "Video call", "مكالمة فيديو"),
            "description": "",
            "color": "#8b5cf6",
        },
    ]


def _visit_types_for_spec(spec: str, lang: str) -> list[dict]:
    if spec == Specialization.REAL_ESTATE.value:
        return [
            {
                "name": seed_label(lang, "Property viewing", "معاينة عقار"),
                "description": "",
                "color": "#3b82f6",
            },
            {
                "name": seed_label(lang, "Office consultation", "استشارة في المكتب"),
                "description": "",
                "color": "#8b5cf6",
            },
            {
                "name": seed_label(lang, "Virtual tour", "جولة افتراضية"),
                "description": "",
                "color": "#f59e0b",
            },
        ]
    if spec in (Specialization.SERVICES.value, Specialization.MEDICAL.value):
        if spec == Specialization.MEDICAL.value:
            return [
                {
                    "name": seed_label(lang, "First consultation", "استشارة أولى"),
                    "description": "",
                    "color": "#3b82f6",
                },
                {
                    "name": seed_label(lang, "Follow-up visit", "زيارة متابعة"),
                    "description": "",
                    "color": "#8b5cf6",
                },
                {
                    "name": seed_label(lang, "Procedure / treatment", "إجراء أو علاج"),
                    "description": "",
                    "color": "#f59e0b",
                },
            ]
        return [
            {
                "name": seed_label(lang, "On-site service", "خدمة ميدانية"),
                "description": "",
                "color": "#3b82f6",
            },
            {
                "name": seed_label(lang, "Office appointment", "موعد في المكتب"),
                "description": "",
                "color": "#8b5cf6",
            },
            {
                "name": seed_label(lang, "Remote consultation", "استشارة عن بعد"),
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


def _seed_company_settings_rows(company: Company, spec: str) -> None:
    # English keys stay in the database so the UI can translate them either way.
    lang = "en"
    for row in _channels_for_spec(spec, lang):
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

    default_stage_name = _stages_for_spec(spec, lang)[0]["name"]
    for row in _stages_for_spec(spec, lang):
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

    for row in _statuses_for_spec(spec, lang):
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

    for row in _call_methods_for_spec(spec, lang):
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

    for row in _visit_types_for_spec(spec, lang):
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

    from settings.deal_pipeline_defaults import seed_company_deal_pipeline

    seed_company_deal_pipeline(company)


def _seed_name_pairs(spec: str, builder) -> list[tuple[str, str]]:
    english = builder(spec, "en")
    arabic = builder(spec, "ar")
    return [
        (en_row["name"], ar_row["name"])
        for en_row, ar_row in zip(english, arabic)
        if en_row["name"] != ar_row["name"]
    ]


def _rename_exact_names(model, company, pairs: list[tuple[str, str]]) -> int:
    updated = 0
    for en_name, ar_name in pairs:
        if model.objects.filter(company=company, name=ar_name).exists():
            continue
        for row in model.objects.filter(company=company, name=en_name):
            row.name = ar_name
            row.save(update_fields=["name", "updated_at"])
            updated += 1
    return updated


def restore_canonical_seed_names(company: Company) -> int:
    """Put untouched Arabic seed labels back to their English keys."""
    if not company or not company.pk:
        return 0
    spec = company.specialization or Specialization.REAL_ESTATE.value
    updated = 0
    for builder, model in (
        (_channels_for_spec, Channel),
        (_stages_for_spec, LeadStage),
        (_statuses_for_spec, LeadStatus),
        (_call_methods_for_spec, CallMethod),
        (_visit_types_for_spec, VisitType),
    ):
        pairs = [(ar, en) for en, ar in _seed_name_pairs(spec, builder)]
        updated += _rename_exact_names(model, company, pairs)

    from settings.deal_pipeline_defaults import restore_canonical_deal_seed_names

    updated += restore_canonical_deal_seed_names(company)
    return updated


def localize_existing_seed_names(company: Company) -> int:
    """Rename untouched English starter rows to the owner's language.

    Only a name that still equals the original English seed for this
    specialization is changed. Edited names are left alone, and a rename is
    skipped when the Arabic name is already in use.
    """
    if not company or not company.pk:
        return 0
    if seed_language_for_company(company) != "ar":
        return 0

    spec = company.specialization or Specialization.REAL_ESTATE.value
    updated = 0
    updated += _rename_exact_names(
        Channel, company, _seed_name_pairs(spec, _channels_for_spec)
    )
    updated += _rename_exact_names(
        LeadStage, company, _seed_name_pairs(spec, _stages_for_spec)
    )
    updated += _rename_exact_names(
        LeadStatus, company, _seed_name_pairs(spec, _statuses_for_spec)
    )
    updated += _rename_exact_names(
        CallMethod, company, _seed_name_pairs(spec, _call_methods_for_spec)
    )
    updated += _rename_exact_names(
        VisitType, company, _seed_name_pairs(spec, _visit_types_for_spec)
    )

    from settings.deal_pipeline_defaults import localize_existing_deal_seed_names

    updated += localize_existing_deal_seed_names(company)
    return updated


def seed_company_settings(company: Company) -> None:
    """
    Create starter settings rows for this company if missing (idempotent).
    Safe to call multiple times (e.g. signal + migration + serializer safety net).

    Retries once after resetting PK sequences when Postgres sequences are behind
    MAX(id) (common after fixture / explicit-id inserts).
    """
    if not company or not company.pk:
        return

    spec = company.specialization or Specialization.REAL_ESTATE.value

    try:
        with transaction.atomic():
            _seed_company_settings_rows(company, spec)
    except IntegrityError:
        logger.warning(
            "Company settings seed hit IntegrityError for company_id=%s; "
            "resetting PK sequences and retrying",
            company.pk,
            exc_info=True,
        )
        reset_pk_sequences(_SEED_MODELS)
        with transaction.atomic():
            _seed_company_settings_rows(company, spec)
