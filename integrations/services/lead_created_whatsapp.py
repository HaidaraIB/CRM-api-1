"""
Automated welcome WhatsApp template when a new Client (lead) is created.
Runs after transaction commit so ClientPhoneNumber rows exist.
"""
from __future__ import annotations

import logging

from django.db import transaction
from rest_framework.exceptions import ValidationError

from crm.models import Client
from integrations.models import MessageSendSource, TwilioSettings
from integrations.policy import is_integration_allowed
from integrations.services.lead_created_sms import resolve_client_sms_phone
from integrations.services.whatsapp_template_send import send_approved_whatsapp_template
from subscriptions.entitlements import increment_monthly_usage, require_monthly_usage

logger = logging.getLogger(__name__)


def send_lead_created_welcome_whatsapp(client_id: int) -> None:
    """
    Send welcome WhatsApp template if company settings allow and WhatsApp is connected.
    Swallows errors; never raises to callers (e.g. on_commit).
    """
    try:
        _send_lead_created_welcome_whatsapp_impl(client_id)
    except Exception:
        logger.exception("send_lead_created_welcome_whatsapp failed for client_id=%s", client_id)


def _send_lead_created_welcome_whatsapp_impl(client_id: int) -> None:
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
        settings = TwilioSettings.objects.select_related("lead_created_whatsapp_template").get(
            company=company
        )
    except TwilioSettings.DoesNotExist:
        return

    if not settings.lead_created_whatsapp_enabled:
        return

    template = settings.lead_created_whatsapp_template
    if template is None:
        logger.info("lead_created_whatsapp: no template, skip client_id=%s", client_id)
        return

    if not is_integration_allowed(company, "whatsapp"):
        logger.info(
            "lead_created_whatsapp: integration/plan gate blocked, skip client_id=%s",
            client_id,
        )
        return

    try:
        require_monthly_usage(
            company,
            "monthly_whatsapp_messages",
            requested_delta=1,
            message="You have reached your monthly WhatsApp messages limit. Please upgrade your plan.",
            error_key="plan_usage_monthly_whatsapp_exceeded",
        )
    except ValidationError:
        logger.info(
            "lead_created_whatsapp: monthly WhatsApp quota exceeded, skip client_id=%s",
            client_id,
        )
        return

    phone_raw = resolve_client_sms_phone(client)
    if not phone_raw:
        logger.info("lead_created_whatsapp: no phone for client_id=%s", client_id)
        return

    ok, _wam_id, error_key, _detail = send_approved_whatsapp_template(
        company=company,
        template=template,
        to_phone=phone_raw,
        client=client,
        send_source=MessageSendSource.AUTO_WELCOME,
        created_by=None,
        persist_message=True,
    )
    if not ok:
        logger.warning(
            "lead_created_whatsapp send failed client_id=%s key=%s",
            client_id,
            error_key,
        )
        return

    increment_monthly_usage(company, "monthly_whatsapp_messages", requested_delta=1)


def schedule_lead_created_welcome_whatsapp(client_pk: int) -> None:
    """Register send after the surrounding DB transaction commits."""
    transaction.on_commit(lambda pk=client_pk: send_lead_created_welcome_whatsapp(pk))
