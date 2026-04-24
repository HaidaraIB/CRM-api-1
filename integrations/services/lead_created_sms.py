"""
Automated welcome SMS when a new Client (lead) is created.
Runs after transaction commit so ClientPhoneNumber rows exist.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from crm.models import Client, ClientPhoneNumber
from integrations.models import LeadSMSMessage, TwilioSettings
from integrations.policy import get_effective_integration_policy, get_plan_integration_access
from integrations.services.twilio_phone import normalize_phone_to_e164
from integrations.services.twilio_text import strip_ansi
from settings.models import SystemSettings
from subscriptions.entitlements import increment_monthly_usage, require_monthly_usage

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\[([^\]]+)\]")


def _first_name_from_client_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    return name.split()[0]


def build_lead_sms_placeholder_values(client: Client) -> dict[str, str]:
    """Lowercase keys for case-insensitive [placeholder] lookup."""
    name = (client.name or "").strip()
    phone = resolve_client_sms_phone(client) or ""
    status_name = (client.status.name or "").strip() if client.status_id and client.status else ""
    company_name = (client.company.name or "").strip() if client.company_id and client.company else ""
    budget_s = ""
    if client.budget is not None:
        try:
            budget_s = str(client.budget.normalize()) if isinstance(client.budget, Decimal) else str(client.budget)
        except Exception:
            budget_s = str(client.budget)
    return {
        "name": name,
        "first_name": _first_name_from_client_name(name) or name,
        "phone": phone,
        "lead_company_name": (client.lead_company_name or "").strip(),
        "status": status_name,
        "company_name": company_name,
        "budget": budget_s,
        "priority": (client.priority or "").strip(),
        "type": (client.type or "").strip(),
        "source": (client.source or "").strip(),
    }


def render_lead_created_sms_template(template: str, client: Client) -> str:
    values = build_lead_sms_placeholder_values(client)

    def repl(match: re.Match) -> str:
        key = (match.group(1) or "").strip().lower()
        if key in values:
            return values[key]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(repl, template or "")


def resolve_client_sms_phone(client: Client) -> str | None:
    raw = (client.phone_number or "").strip()
    if raw:
        return raw
    qs = ClientPhoneNumber.objects.filter(client_id=client.pk).order_by("-is_primary", "id")
    row = qs.first()
    if row and (row.phone_number or "").strip():
        return (row.phone_number or "").strip()
    return None


def _twilio_integration_allowed(company) -> bool:
    if not company:
        return False
    plan_gate = get_plan_integration_access(company, "twilio")
    if not plan_gate["enabled"]:
        return False
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform="twilio",
    )
    return bool(effective["enabled"])


def send_lead_created_welcome_sms(client_id: int) -> None:
    """
    Send welcome SMS if company settings allow and Twilio is configured.
    Swallows errors; never raises to callers (e.g. on_commit).
    """
    try:
        _send_lead_created_welcome_sms_impl(client_id)
    except Exception:
        logger.exception("send_lead_created_welcome_sms failed for client_id=%s", client_id)


def _send_lead_created_welcome_sms_impl(client_id: int) -> None:
    client = (
        Client.objects.select_related("company", "status")
        .filter(pk=client_id)
        .first()
    )
    if not client or not client.company_id:
        return

    company = client.company
    try:
        twilio_settings = TwilioSettings.objects.get(company=company)
    except TwilioSettings.DoesNotExist:
        return

    if not twilio_settings.lead_created_sms_enabled:
        return

    template = (twilio_settings.lead_created_sms_template or "").strip()
    if not template:
        logger.info("lead_created_sms: empty template, skip client_id=%s", client_id)
        return

    if not twilio_settings.is_enabled:
        logger.info("lead_created_sms: Twilio integration disabled, skip client_id=%s", client_id)
        return

    if not _twilio_integration_allowed(company):
        logger.info("lead_created_sms: integration/plan gate blocked, skip client_id=%s", client_id)
        return

    try:
        require_monthly_usage(
            company,
            "monthly_sms_messages",
            requested_delta=1,
            message="You have reached your monthly SMS limit. Please upgrade your plan.",
            error_key="plan_usage_monthly_sms_exceeded",
        )
    except ValidationError:
        logger.info("lead_created_sms: monthly SMS quota exceeded, skip client_id=%s", client_id)
        return

    account_sid = twilio_settings.account_sid
    auth_token = twilio_settings.get_auth_token()
    twilio_number = twilio_settings.twilio_number
    sender_id = (twilio_settings.sender_id or "").strip()
    from_value = sender_id if sender_id else (twilio_number or "")
    if not account_sid or not auth_token or not from_value:
        logger.info("lead_created_sms: incomplete Twilio credentials, skip client_id=%s", client_id)
        return

    phone_raw = resolve_client_sms_phone(client)
    if not phone_raw:
        logger.info("lead_created_sms: no phone for client_id=%s", client_id)
        return

    body = render_lead_created_sms_template(template, client)
    if not (body or "").strip():
        return

    to = normalize_phone_to_e164(phone_raw)

    try:
        from twilio.base.exceptions import TwilioRestException
        from twilio.rest import Client as TwilioClient

        twilio_client = TwilioClient(account_sid, auth_token)
        message = twilio_client.messages.create(
            body=body,
            from_=from_value,
            to=to,
        )
        twilio_sid = message.sid
    except TwilioRestException as e:
        logger.warning(
            "lead_created_sms Twilio error client_id=%s code=%s msg=%s",
            client_id,
            getattr(e, "code", None),
            getattr(e, "msg", str(e)),
        )
        return
    except Exception as e:
        logger.exception(
            "lead_created_sms Twilio send failed client_id=%s: %s",
            client_id,
            strip_ansi(str(e)),
        )
        return

    LeadSMSMessage.objects.create(
        client=client,
        phone_number=phone_raw,
        body=body,
        direction=LeadSMSMessage.DIRECTION_OUTBOUND,
        twilio_sid=twilio_sid,
        created_by=None,
    )
    increment_monthly_usage(company, "monthly_sms_messages", requested_delta=1)


def schedule_lead_created_welcome_sms(client_pk: int) -> None:
    """Register send after the surrounding DB transaction commits."""
    transaction.on_commit(lambda pk=client_pk: send_lead_created_welcome_sms(pk))
