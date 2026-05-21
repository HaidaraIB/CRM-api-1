"""
AI management report: daily staff activity + hot leads (owners only).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from accounts.models import Role, User
from crm.models import Client, ClientCall, ClientTask, ClientVisit
from integrations.models import AIInsightStatus, AIManagementReport, ClientAIInsight, OpenAISettings
from integrations.services.ai_lead_analysis import TERMINAL_STAGE_NAMES, _is_terminal_stage, _stage_key

logger = logging.getLogger(__name__)

STAFF_ROLES = (
    Role.EMPLOYEE.value,
    Role.DOCTOR.value,
    Role.SUPERVISOR.value,
    Role.RECEPTION.value,
    Role.DATA_ENTRY.value,
)

MANAGEMENT_REPORT_PROMPT = """You are preparing a concise management dashboard report for a CRM owner.
Use ONLY the JSON data provided. Output valid JSON with this schema:
{
  "employee_performance_en": "<2-4 sentences summarizing today's team activity>",
  "employee_performance_ar": "<نفس الملخص بالعربية>",
  "hot_leads_summary_en": "<2-3 sentences on which hot leads need follow-up and why>",
  "hot_leads_summary_ar": "<نفس الملخص بالعربية>"
}
Rules:
- Be factual; do not invent metrics or leads not in the data.
- Arabic must be natural Modern Standard Arabic.
- Highlight top performers and anyone with zero activity today if relevant.
- For hot leads, prioritize urgent follow-ups from scores, AI insights, and lead type.
"""


def _today_start():
    now = timezone.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def build_employee_daily_stats(company) -> list[dict]:
    start = _today_start()
    staff = (
        User.objects.filter(company=company, is_active=True, role__in=STAFF_ROLES)
        .order_by("first_name", "last_name", "username")
    )
    rows = []
    for user in staff:
        name = user.get_full_name() or user.username
        tasks = ClientTask.objects.filter(
            client__company=company, created_by=user, created_at__gte=start
        ).count()
        calls = ClientCall.objects.filter(
            client__company=company, created_by=user, created_at__gte=start
        ).count()
        visits = ClientVisit.objects.filter(
            client__company=company, created_by=user, created_at__gte=start
        ).count()
        assigned_leads = Client.objects.filter(company=company, assigned_to=user).count()
        activity_total = tasks + calls + visits
        rows.append(
            {
                "user_id": user.id,
                "name": name,
                "role": user.role,
                "tasks_today": tasks,
                "calls_today": calls,
                "visits_today": visits,
                "activity_total": activity_total,
                "assigned_leads": assigned_leads,
            }
        )
    rows.sort(key=lambda r: (-r["activity_total"], r["name"]))
    return rows


def build_hot_leads_snapshot(company, *, limit: int = 10) -> list[dict]:
    seen: set[int] = set()
    items: list[dict] = []

    insight_qs = (
        ClientAIInsight.objects.filter(company=company, status=AIInsightStatus.PENDING)
        .select_related("client", "client__status", "client__assigned_to")
        .order_by("-ai_score")[:limit]
    )
    for ins in insight_qs:
        if ins.client_id in seen or _is_terminal_stage(ins.client):
            continue
        seen.add(ins.client_id)
        c = ins.client
        items.append(
            {
                "client_id": c.id,
                "name": c.name,
                "type": c.type,
                "priority": c.priority,
                "stage": _stage_key(c),
                "ai_score": ins.ai_score,
                "summary_en": ins.summary_en or ins.summary or "",
                "summary_ar": ins.summary_ar or "",
                "assigned_to_name": (
                    (c.assigned_to.get_full_name() or c.assigned_to.username)
                    if c.assigned_to
                    else None
                ),
                "source": "ai_insight",
            }
        )

    if len(items) < limit:
        client_qs = (
            Client.objects.filter(company=company)
            .select_related("status", "assigned_to")
            .filter(Q(type="hot") | Q(priority="high"))
            .order_by("-id")[: limit * 2]
        )
        for c in client_qs:
            if c.id in seen or _is_terminal_stage(c):
                continue
            seen.add(c.id)
            items.append(
                {
                    "client_id": c.id,
                    "name": c.name,
                    "type": c.type,
                    "priority": c.priority,
                    "stage": _stage_key(c),
                    "ai_score": None,
                    "summary_en": (c.notes or "")[:300],
                    "summary_ar": "",
                    "assigned_to_name": (
                        (c.assigned_to.get_full_name() or c.assigned_to.username)
                        if c.assigned_to
                        else None
                    ),
                    "source": "crm_hot",
                }
            )
            if len(items) >= limit:
                break

    return items[:limit]


def build_management_report_context(company) -> dict:
    return {
        "report_date": timezone.now().date().isoformat(),
        "employees": build_employee_daily_stats(company),
        "hot_leads": build_hot_leads_snapshot(company),
    }


def _call_openai_management_report(api_key: str, model: str, context: dict) -> tuple[dict, int | None]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": MANAGEMENT_REPORT_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    text = response.choices[0].message.content or "{}"
    tokens = getattr(response.usage, "total_tokens", None) if response.usage else None
    return json.loads(text), tokens


def serialize_report_payload(report: AIManagementReport | None, *, live_context: dict | None = None) -> dict:
    base = (report.payload if report and isinstance(report.payload, dict) else {}) or {}
    ctx = live_context or base.get("context") or {}
    return {
        "generated_at": report.generated_at.isoformat() if report else None,
        "model_used": report.model_used if report else "",
        "employees": ctx.get("employees") or base.get("employees") or [],
        "hot_leads": ctx.get("hot_leads") or base.get("hot_leads") or [],
        "employee_performance_en": base.get("employee_performance_en") or "",
        "employee_performance_ar": base.get("employee_performance_ar") or "",
        "hot_leads_summary_en": base.get("hot_leads_summary_en") or "",
        "hot_leads_summary_ar": base.get("hot_leads_summary_ar") or "",
        "report_date": ctx.get("report_date") or base.get("report_date"),
    }


def get_management_report_for_company(company, *, refresh_live: bool = True) -> dict:
    live = build_management_report_context(company) if refresh_live else None
    try:
        report = AIManagementReport.objects.get(company=company)
    except AIManagementReport.DoesNotExist:
        report = None
    data = serialize_report_payload(report, live_context=live)
    if live:
        data["employees"] = live["employees"]
        data["hot_leads"] = live["hot_leads"]
        data["report_date"] = live["report_date"]
    data["has_ai_summary"] = bool(
        data.get("employee_performance_en") or data.get("employee_performance_ar")
    )
    return data


def generate_management_report(company) -> dict:
    try:
        settings = OpenAISettings.objects.get(company=company)
    except OpenAISettings.DoesNotExist:
        return {"error": "OpenAI is not configured.", "code": "openai_not_configured"}

    if not settings.is_enabled:
        return {"skipped": True, "reason": "disabled"}

    api_key = settings.get_api_key()
    if not api_key:
        return {"error": "API key not configured.", "code": "openai_no_api_key"}

    context = build_management_report_context(company)
    model = settings.model or OpenAISettings.DEFAULT_MODEL
    try:
        ai_payload, tokens_used = _call_openai_management_report(api_key, model, context)
    except Exception as exc:
        err = str(exc)[:500]
        logger.exception("Management report AI failed for company %s", company.id)
        return {"error": err, "code": "ai_report_failed"}

    stored = {
        **context,
        "employee_performance_en": (ai_payload.get("employee_performance_en") or "")[:4000],
        "employee_performance_ar": (ai_payload.get("employee_performance_ar") or "")[:4000],
        "hot_leads_summary_en": (ai_payload.get("hot_leads_summary_en") or "")[:4000],
        "hot_leads_summary_ar": (ai_payload.get("hot_leads_summary_ar") or "")[:4000],
    }
    report, _ = AIManagementReport.objects.update_or_create(
        company=company,
        defaults={
            "payload": stored,
            "model_used": model,
            "tokens_used": tokens_used,
        },
    )
    out = serialize_report_payload(report, live_context=context)
    out["has_ai_summary"] = True
    return out
