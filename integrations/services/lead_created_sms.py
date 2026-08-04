"""
Automated welcome SMS when a new Client (lead) is created.
Runs after transaction commit so ClientPhoneNumber rows exist.
"""
from __future__ import annotations

import logging

from django.db import transaction
from rest_framework.exceptions import ValidationError

from crm.models import Client, ClientPhoneNumber
from integrations.models import LeadSMSMessage, MessageSendSource, SmsProvider, TwilioSettings
from integrations.policy import is_integration_allowed
from integrations.services.company_sms import send_company_sms
from integrations.services.message_placeholders import (
    build_message_placeholder_values,
    render_message_placeholders,
)
from subscriptions.entitlements import increment_monthly_usage, require_monthly_usage

logger = logging.getLogger(__name__)


def build_lead_sms_placeholder_values(client: Client) -> dict[str, str]:
    """Lowercase / alias keys for [placeholder] and { placeholder } lookup."""
    return build_message_placeholder_values(client)


def render_lead_created_sms_template(template: str, client: Client) -> str:
    values = build_lead_sms_placeholder_values(client)
    return render_message_placeholders(template or "", values)


def resolve_client_sms_phone(client: Client) -> str | None:
    raw = (client.phone_number or "").strip()
    if raw:
        return raw
    qs = ClientPhoneNumber.objects.filter(client_id=client.pk).order_by("-is_primary", "id")
    row = qs.first()
    if row and (row.phone_number or "").strip():
        return (row.phone_number or "").strip()
    return None


def _sms_integration_allowed(company, provider: str) -> bool:
    return is_integration_allowed(company, provider or SmsProvider.TWILIO)


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
        Client.objects.select_related(
            "company",
            "status",
            "communication_way",
            "assigned_to",
        )
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

    sms_platform = twilio_settings.provider or SmsProvider.TWILIO
    if not _sms_integration_allowed(company, sms_platform):
        logger.info(
            "lead_created_sms: integration/plan gate blocked provider=%s, skip client_id=%s",
            sms_platform,
            client_id,
        )
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

    phone_raw = resolve_client_sms_phone(client)
    if not phone_raw:
        logger.info("lead_created_sms: no phone for client_id=%s", client_id)
        return

    body = render_lead_created_sms_template(template, client)
    if not (body or "").strip():
        return

    ok, external_id, error_key, error_msg, provider_used = send_company_sms(
        twilio_settings,
        to_phone=phone_raw,
        body=body,
    )
    if not ok:
        logger.warning(
            "lead_created_sms send failed client_id=%s provider=%s key=%s msg=%s",
            client_id,
            provider_used,
            error_key,
            error_msg,
        )
        return

    twilio_sid = external_id if provider_used == SmsProvider.TWILIO else None
    LeadSMSMessage.objects.create(
        client=client,
        phone_number=phone_raw,
        body=body,
        direction=LeadSMSMessage.DIRECTION_OUTBOUND,
        provider=provider_used,
        external_message_id=external_id,
        twilio_sid=twilio_sid,
        created_by=None,
        send_source=MessageSendSource.AUTO_WELCOME,
    )
    increment_monthly_usage(company, "monthly_sms_messages", requested_delta=1)


def schedule_lead_created_welcome_sms(client_pk: int) -> None:
    """Register send after the surrounding DB transaction commits."""
    transaction.on_commit(lambda pk=client_pk: send_lead_created_welcome_sms(pk))
