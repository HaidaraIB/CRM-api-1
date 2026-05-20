"""
AI lead analysis via tenant OpenAI API key (BYOK).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from crm.models import Client, ClientCall, ClientTask, ClientVisit
from integrations.models import (
    AIInsightPriorityLevel,
    AIInsightStatus,
    ClientAIInsight,
    OpenAISettings,
)

logger = logging.getLogger(__name__)

TERMINAL_STAGE_NAMES = frozenset(
    {"not_interested", "out_of_service", "cancellation"}
)

SYSTEM_PROMPT = """You analyze CRM leads for a sales/clinic team using only the provided data.
Identify leads that need urgent follow-up based on employee notes and activities.
Output valid JSON only with this schema:
{"leads":[{"client_id":<int>,"ai_score":<0-100>,"priority_level":"high"|"medium"|"low","summary":"<short insight>","reasoning":"<optional>","suggested_reminder_date":"<ISO8601 or null>","suggested_task_notes":"<actionable follow-up note>"}]}
Rules:
- Do not invent facts not present in the context.
- ai_score reflects urgency (higher = more urgent).
- suggested_reminder_date should be a realistic next follow-up time (company timezone UTC).
- Return at most one entry per client_id provided.
- If a lead does not need attention, omit it from the array.
"""


def _stage_key(client: Client) -> str:
    if not client.status_id:
        return ""
    name = getattr(client.status, "name", None) or ""
    return str(name).lower().replace(" ", "_")


def _is_terminal_stage(client: Client) -> bool:
    return _stage_key(client) in TERMINAL_STAGE_NAMES


def _build_activity_snapshot(client: Client) -> dict:
    tasks = list(
        ClientTask.objects.filter(client=client)
        .select_related("stage", "created_by")
        .order_by("-created_at")[:5]
    )
    calls = list(
        ClientCall.objects.filter(client=client)
        .select_related("call_method", "created_by")
        .order_by("-created_at")[:5]
    )
    visits = list(
        ClientVisit.objects.filter(client=client)
        .select_related("visit_type", "created_by")
        .order_by("-created_at")[:5]
    )
    return {
        "client_id": client.id,
        "name": client.name,
        "priority": client.priority,
        "stage": _stage_key(client),
        "notes": client.notes or "",
        "tasks": [
            {
                "notes": t.notes or "",
                "reminder_date": t.reminder_date.isoformat() if t.reminder_date else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "stage": t.stage.name if t.stage else None,
            }
            for t in tasks
        ],
        "calls": [
            {
                "notes": c.notes or "",
                "call_datetime": c.call_datetime.isoformat() if c.call_datetime else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in calls
        ],
        "visits": [
            {
                "summary": v.summary or "",
                "visit_datetime": v.visit_datetime.isoformat() if v.visit_datetime else None,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in visits
        ],
    }


def snapshot_hash(snapshot: dict) -> str:
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_eligible_clients(company, *, limit: int) -> list[Client]:
    cutoff = timezone.now() - timedelta(days=30)
    recent_task = ClientTask.objects.filter(
        client_id__in=Client.objects.filter(company=company).values("id"),
        created_at__gte=cutoff,
    ).values_list("client_id", flat=True)
    recent_call = ClientCall.objects.filter(
        client_id__in=Client.objects.filter(company=company).values("id"),
        created_at__gte=cutoff,
    ).values_list("client_id", flat=True)
    recent_visit = ClientVisit.objects.filter(
        client_id__in=Client.objects.filter(company=company).values("id"),
        created_at__gte=cutoff,
    ).values_list("client_id", flat=True)
    recent_ids = set(recent_task) | set(recent_call) | set(recent_visit)

    qs = (
        Client.objects.filter(company=company)
        .select_related("status", "assigned_to")
        .filter(Q(id__in=recent_ids) | Q(priority__in=("high", "medium")))
    )
    eligible = [c for c in qs if not _is_terminal_stage(c)]
    eligible.sort(
        key=lambda c: (
            0 if c.priority == "high" else 1 if c.priority == "medium" else 2,
            -(c.id),
        )
    )
    return eligible[:limit]


def _parse_leads_response(content: str) -> list[dict]:
    data = json.loads(content)
    if isinstance(data, dict) and "leads" in data:
        return data.get("leads") or []
    if isinstance(data, list):
        return data
    return []


def _call_openai(api_key: str, model: str, snapshots: list[dict]) -> tuple[list[dict], int | None]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    user_content = json.dumps({"leads": snapshots}, ensure_ascii=False)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    text = response.choices[0].message.content or "{}"
    tokens = getattr(response.usage, "total_tokens", None) if response.usage else None
    return _parse_leads_response(text), tokens


def test_openai_connection(api_key: str, model: str) -> tuple[bool, str]:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        client.chat.completions.create(
            model=model or OpenAISettings.DEFAULT_MODEL,
            messages=[{"role": "user", "content": "Reply with JSON: {\"ok\":true}"}],
            max_tokens=16,
            response_format={"type": "json_object"},
        )
        return True, ""
    except Exception as exc:
        logger.warning("OpenAI connection test failed: %s", exc)
        return False, str(exc)[:500]


@transaction.atomic
def persist_insight(
    *,
    company,
    client: Client,
    lead_payload: dict,
    snapshot_hash_value: str,
    model_used: str,
    tokens_used: int | None,
) -> ClientAIInsight | None:
    client_id = lead_payload.get("client_id")
    if client_id != client.id:
        return None
    try:
        ai_score = int(lead_payload.get("ai_score", 0))
    except (TypeError, ValueError):
        ai_score = 0
    ai_score = max(0, min(100, ai_score))
    priority_level = str(lead_payload.get("priority_level") or "medium").lower()
    if priority_level not in AIInsightPriorityLevel.values:
        priority_level = AIInsightPriorityLevel.MEDIUM

    reminder_raw = lead_payload.get("suggested_reminder_date")
    suggested_reminder_date = None
    if reminder_raw:
        suggested_reminder_date = parse_datetime(str(reminder_raw))
        if suggested_reminder_date and timezone.is_naive(suggested_reminder_date):
            suggested_reminder_date = timezone.make_aware(suggested_reminder_date)

    ClientAIInsight.objects.filter(
        client=client,
        status=AIInsightStatus.PENDING,
    ).update(status=AIInsightStatus.EXPIRED)

    return ClientAIInsight.objects.create(
        company=company,
        client=client,
        ai_score=ai_score,
        priority_level=priority_level,
        summary=(lead_payload.get("summary") or "")[:2000],
        reasoning=(lead_payload.get("reasoning") or "")[:4000] or None,
        suggested_reminder_date=suggested_reminder_date,
        suggested_task_notes=(lead_payload.get("suggested_task_notes") or "")[:4000] or None,
        source_snapshot_hash=snapshot_hash_value,
        status=AIInsightStatus.PENDING,
        model_used=model_used,
        tokens_used=tokens_used,
    )


def run_company_analysis(company, *, force: bool = False) -> dict:
    """
    Run AI analysis for one company. Returns stats dict.
    """
    try:
        settings = OpenAISettings.objects.get(company=company)
    except OpenAISettings.DoesNotExist:
        return {"skipped": True, "reason": "no_settings"}

    if not settings.is_enabled:
        return {"skipped": True, "reason": "disabled"}

    api_key = settings.get_api_key()
    if not api_key:
        settings.last_error = "API key not configured."
        settings.save(update_fields=["last_error"])
        return {"skipped": True, "reason": "no_api_key"}

    limit = settings.max_leads_per_run or OpenAISettings.DEFAULT_MAX_LEADS_PER_RUN
    clients = get_eligible_clients(company, limit=limit)
    if not clients:
        settings.last_analysis_at = timezone.now()
        settings.last_error = ""
        settings.save(update_fields=["last_analysis_at", "last_error"])
        return {"analyzed": 0, "created": 0}

    snapshots = []
    hash_by_client = {}
    for client in clients:
        snap = _build_activity_snapshot(client)
        h = snapshot_hash(snap)
        hash_by_client[client.id] = h
        if not force:
            pending = ClientAIInsight.objects.filter(
                client=client,
                status=AIInsightStatus.PENDING,
                source_snapshot_hash=h,
            ).exists()
            if pending:
                continue
        snapshots.append(snap)

    if not snapshots:
        settings.last_analysis_at = timezone.now()
        settings.last_error = ""
        settings.save(update_fields=["last_analysis_at", "last_error"])
        return {"analyzed": 0, "created": 0, "unchanged": len(clients)}

    model = settings.model or OpenAISettings.DEFAULT_MODEL
    try:
        leads_payload, tokens_used = _call_openai(api_key, model, snapshots)
    except Exception as exc:
        err = str(exc)[:500]
        settings.last_error = err
        settings.save(update_fields=["last_error"])
        logger.exception("OpenAI analysis failed for company %s", company.id)
        return {"error": err}

    client_map = {c.id: c for c in clients}
    created = 0
    for item in leads_payload:
        cid = item.get("client_id")
        client = client_map.get(cid)
        if not client:
            continue
        insight = persist_insight(
            company=company,
            client=client,
            lead_payload=item,
            snapshot_hash_value=hash_by_client.get(cid, ""),
            model_used=model,
            tokens_used=tokens_used,
        )
        if insight:
            created += 1

    settings.last_analysis_at = timezone.now()
    settings.last_error = ""
    settings.save(update_fields=["last_analysis_at", "last_error"])
    return {"analyzed": len(snapshots), "created": created, "tokens_used": tokens_used}
