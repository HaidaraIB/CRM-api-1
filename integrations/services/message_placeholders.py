"""
Shared named message placeholders for SMS, WhatsApp free-text, and Meta template conversion.

Supports both bracket and curly forms, e.g.:
  [Customer Name] / { اسم العميل }
  [Phone] / { رقم الهاتف }

Meta Cloud API still receives positional {{1}}, {{2}} after conversion at submit/send time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

# Match [name] and { name } but not Meta {{1}} positional tokens.
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_CURLY_RE = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")


def _norm_key(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip()).casefold()


def _first_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    return name.split()[0]


def _user_display_name(user) -> str:
    if user is None:
        return ""
    full = ""
    try:
        full = (user.get_full_name() or "").strip()
    except Exception:
        full = ""
    if full:
        return full
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    combined = f"{first} {last}".strip()
    if combined:
        return combined
    return (getattr(user, "username", None) or "").strip()


def _client_customer_name(client) -> str:
    name = (getattr(client, "name", None) or "").strip()
    if name.lower().startswith("whatsapp:"):
        rest = name.split(":", 1)[-1].strip()
        return rest or name
    return name


def _resolve_phone(client) -> str:
    raw = (getattr(client, "phone_number", None) or "").strip()
    if raw:
        return raw
    try:
        from crm.models import ClientPhoneNumber

        row = (
            ClientPhoneNumber.objects.filter(client_id=client.pk)
            .order_by("-is_primary", "id")
            .first()
        )
        if row and (row.phone_number or "").strip():
            return (row.phone_number or "").strip()
    except Exception:
        pass
    return ""


def _tenant_company_name(client) -> str:
    company = getattr(client, "company", None)
    if company is not None:
        return (getattr(company, "name", None) or "").strip()
    return ""


def _status_name(client) -> str:
    status = getattr(client, "status", None)
    if status is not None:
        return (getattr(status, "name", None) or "").strip()
    return ""


def _channel_name(client) -> str:
    channel = getattr(client, "communication_way", None)
    if channel is not None:
        return (getattr(channel, "name", None) or "").strip()
    return (getattr(client, "source", None) or "").strip()


def _latest_task_stage(client) -> str:
    try:
        task = client.client_tasks.select_related("stage").order_by("-created_at").first()
        if task and task.stage_id:
            return (task.stage.name or "").strip()
    except Exception:
        pass
    return ""


def _latest_visit_type(client) -> str:
    try:
        visit = client.client_visits.select_related("visit_type").order_by("-created_at").first()
        if visit and visit.visit_type_id:
            return (visit.visit_type.name or "").strip()
    except Exception:
        pass
    return ""


def _company_now(client, now: Optional[datetime] = None) -> datetime:
    if now is not None:
        return now
    company = getattr(client, "company", None)
    tz_name = (getattr(company, "timezone", None) or "").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return dj_timezone.now().astimezone(tz)


def _budget_str(client) -> str:
    lo = getattr(client, "budget", None)
    hi = getattr(client, "budget_max", None)
    if lo is None and hi is None:
        return ""
    try:
        if lo is not None and hi is not None and hi != lo:
            lo_s = str(lo.normalize()) if isinstance(lo, Decimal) else str(lo)
            hi_s = str(hi.normalize()) if isinstance(hi, Decimal) else str(hi)
            return f"{lo_s}–{hi_s}"
        val = lo if lo is not None else hi
        return str(val.normalize()) if isinstance(val, Decimal) else str(val)
    except Exception:
        return str(lo or hi or "")


# Canonical id -> list of display aliases (any of these match inside [] or {})
PLACEHOLDER_ALIAS_GROUPS: list[tuple[str, list[str]]] = [
    (
        "customer_name",
        [
            "اسم العميل",
            "اسم_العميل",
            "Customer Name",
            "customer_name",
            "name",
            "client_name",
        ],
    ),
    ("first_name", ["first_name", "الاسم الاول", "الاسم الأول"]),
    (
        "phone",
        [
            "رقم الهاتف",
            "رقم_الهاتف",
            "الهاتف",
            "Phone",
            "phone",
            "phone_number",
        ],
    ),
    (
        "employee_name",
        [
            "اسم الموظف",
            "اسم_الموظف",
            "Employee Name",
            "employee_name",
            "assigned_to",
            "staff_name",
        ],
    ),
    (
        "company_name",
        [
            "اسم الشركة",
            "اسم_الشركة",
            "الشركة",
            "شركة",
            "Company",
            "company_name",
            "company",
        ],
    ),
    (
        "current_date",
        [
            "التاريخ الحالي",
            "التاريخ_الحالي",
            "Current Date",
            "current_date",
            "date",
        ],
    ),
    (
        "current_time",
        [
            "الوقت الحالي",
            "الوقت_الحالي",
            "Current Time",
            "current_time",
            "time",
        ],
    ),
    ("status", ["الحالة", "Status", "status"]),
    ("stage", ["المرحلة", "Stage", "stage", "last_stage"]),
    (
        "channel",
        [
            "قناة التواصل",
            "قناة_التواصل",
            "Channel",
            "channel",
            "communication_way",
            "source",
        ],
    ),
    (
        "visit_type",
        [
            "نوع الزيارة",
            "نوع_الزيارة",
            "Visit Type",
            "visit_type",
        ],
    ),
    ("profession", ["المهنة", "Profession", "profession"]),
    # Legacy WhatsApp/SMS template chips
    ("lead_company_name", ["lead_company_name", "شركة العميل", "Lead Company"]),
    ("amount", ["المبلغ", "Amount", "amount", "budget"]),
    (
        "invoice_number",
        ["رقم الفاتورة", "رقم_الفاتورة", "Invoice Number", "invoice_number"],
    ),
    ("priority", ["priority", "الأولوية"]),
    ("type", ["type", "النوع"]),
]


def _alias_to_canonical() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for canonical, aliases in PLACEHOLDER_ALIAS_GROUPS:
        for alias in aliases:
            mapping[_norm_key(alias)] = canonical
    return mapping


ALIAS_TO_CANONICAL = _alias_to_canonical()


def build_message_placeholder_values(
    client,
    *,
    employee=None,
    now: Optional[datetime] = None,
) -> dict[str, str]:
    """
    Canonical placeholder values for a lead/client.
    employee: optional override (defaults to client.assigned_to).
    """
    name = _client_customer_name(client)
    company_name = _tenant_company_name(client) or (
        getattr(client, "lead_company_name", None) or ""
    ).strip()
    local_now = _company_now(client, now)
    assignee = employee if employee is not None else getattr(client, "assigned_to", None)

    return {
        "customer_name": name,
        "first_name": _first_name(name) or name,
        "phone": _resolve_phone(client),
        "employee_name": _user_display_name(assignee),
        "company_name": company_name,
        "current_date": local_now.strftime("%Y-%m-%d"),
        "current_time": local_now.strftime("%H:%M"),
        "status": _status_name(client),
        "stage": _latest_task_stage(client),
        "channel": _channel_name(client),
        "visit_type": _latest_visit_type(client),
        "profession": (getattr(client, "profession", None) or "").strip(),
        "lead_company_name": (getattr(client, "lead_company_name", None) or "").strip(),
        "amount": _budget_str(client),
        "invoice_number": (getattr(client, "invoice_number", None) or "").strip(),
        "priority": (getattr(client, "priority", None) or "").strip(),
        "type": (getattr(client, "type", None) or "").strip(),
        # Backward-compatible snake keys used by welcome SMS
        "name": name,
        "company": company_name,
        "source": (getattr(client, "source", None) or "").strip() or _channel_name(client),
        "budget": _budget_str(client),
    }


def lookup_placeholder_value(values: dict[str, str], raw_key: str) -> Optional[str]:
    key = _norm_key(raw_key)
    if not key:
        return None
    if key in values:
        return values[key]
    canonical = ALIAS_TO_CANONICAL.get(key)
    if canonical and canonical in values:
        return values[canonical]
    # Also allow canonical lookup via values that used alias as key
    return None


def render_message_placeholders(
    text: str,
    values: dict[str, str],
    *,
    keep_unresolved: bool = True,
) -> str:
    """Replace [alias] and { alias } tokens. Leaves {{1}} Meta tokens untouched."""
    if not text:
        return text or ""

    def repl(match: re.Match) -> str:
        raw = match.group(1) or ""
        resolved = lookup_placeholder_value(values, raw)
        if resolved is None or resolved == "":
            return match.group(0) if keep_unresolved else ""
        return resolved

    out = _BRACKET_RE.sub(repl, text)
    out = _CURLY_RE.sub(repl, out)
    return out


def render_message_placeholders_for_client(
    text: str,
    client,
    *,
    employee=None,
    now: Optional[datetime] = None,
) -> str:
    values = build_message_placeholder_values(client, employee=employee, now=now)
    return render_message_placeholders(text, values)


# --- Meta conversion helpers (named tokens → {{n}} in appearance order) ---

Getter = Callable[[Any], str]


def _getter_for_canonical(canonical: str) -> Getter:
    def getter(client) -> str:
        values = build_message_placeholder_values(client)
        return (values.get(canonical) or "").strip()

    return getter


# Sample strings Meta example rows / parameter fallbacks
CANONICAL_SAMPLES: dict[str, str] = {
    "customer_name": "Customer",
    "first_name": "Customer",
    "phone": "9647700000000",
    "employee_name": "Employee",
    "company_name": "Company",
    "current_date": "2026-01-01",
    "current_time": "12:00",
    "status": "Status",
    "stage": "Stage",
    "channel": "Channel",
    "visit_type": "Visit",
    "profession": "Profession",
    "lead_company_name": "Company",
    "amount": "100",
    "invoice_number": "INV-001",
    "priority": "high",
    "type": "fresh",
}


def build_meta_placeholder_defs() -> list[tuple[str, str, str, Getter]]:
    """
    (regex, canonical, sample, getter) entries for left-to-right Meta {{n}} conversion.
    Includes both [alias] and { alias } forms for each known placeholder.
    """
    defs: list[tuple[str, str, str, Getter]] = []
    for canonical, aliases in PLACEHOLDER_ALIAS_GROUPS:
        parts: list[str] = []
        for alias in aliases:
            esc = re.escape(alias)
            parts.append(rf"\[\s*{esc}\s*\]")
            parts.append(rf"(?<!\{{)\{{\s*{esc}\s*\}}(?!\}})")
        if not parts:
            continue
        pattern = "|".join(parts)
        sample = CANONICAL_SAMPLES.get(canonical, "Sample")
        defs.append((pattern, canonical, sample, _getter_for_canonical(canonical)))
    return defs


META_PLACEHOLDER_DEFS = build_meta_placeholder_defs()

# Reverse of CANONICAL_SAMPLES: recovers the variable behind a Meta example value
# when a template was submitted before the map was persisted. Samples are not unique
# ("Customer" covers customer_name and first_name) — first group wins, matching the
# left-to-right precedence of PLACEHOLDER_ALIAS_GROUPS.
SAMPLE_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in PLACEHOLDER_ALIAS_GROUPS:
    _sample = CANONICAL_SAMPLES.get(_canonical)
    if _sample and _sample not in SAMPLE_TO_CANONICAL:
        SAMPLE_TO_CANONICAL[_sample] = _canonical


def canonical_for_sample(sample: str) -> Optional[str]:
    """Meta example value (e.g. "Employee") -> canonical placeholder id."""
    return SAMPLE_TO_CANONICAL.get((sample or "").strip())
