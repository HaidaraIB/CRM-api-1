"""
Queued Messaging Center campaign sends.

An approved campaign request must be sent independently of whichever browser
tab triggered the approval - unlike the owner's own instant-send flow (a
client-side loop in IntegrationsPage.tsx), this runs as a django-q task so a
large approved batch keeps sending even if the approving owner closes their
tab. Pattern copied from notifications/tasks.py.
"""
from __future__ import annotations

import logging
import time

from crm.models import Client
from integrations.campaign_notifications import notify_campaign_batch_complete
from integrations.models import (
    CampaignBatchStatus,
    LeadSMSMessage,
    MessageCampaignBatch,
    MessageCampaignFailure,
    MessageSendSource,
    MessageTemplate,
    TwilioSettings,
)
from integrations.services.company_sms import send_company_sms
from integrations.services.message_placeholders import (
    _user_display_name,
    render_message_placeholders_for_client,
)
from integrations.services.whatsapp_send import send_whatsapp_template_message
from integrations.whatsapp_account_sync import resolve_whatsapp_account_for_api

logger = logging.getLogger(__name__)

CAMPAIGN_SEND_TASK_PATH = "integrations.tasks.send_campaign_batch_task"

_WHATSAPP_SEND_PACING_SECONDS = 0.25  # same pacing as the owner's client-side loop


def send_campaign_batch_task(batch_id: int) -> None:
    """
    Worker entry point (runs in qcluster). Takes a primitive id, not a model
    instance, since django-q pickles task args and re-import happens in the
    worker process.
    """
    batch = MessageCampaignBatch.objects.select_related("company", "requested_by").filter(id=batch_id).first()
    if batch is None or batch.status != CampaignBatchStatus.APPROVED:
        logger.info("Skipping campaign batch task id=%s (missing or not APPROVED)", batch_id)
        return

    batch.status = CampaignBatchStatus.SENDING
    batch.save(update_fields=["status"])

    company = batch.company
    is_sms = batch.channel == MessageCampaignBatch.CHANNEL_SMS
    sender_name = _user_display_name(batch.requested_by)

    twilio_settings = None
    wa_account = None
    template = None
    setup_error = None

    if is_sms:
        twilio_settings = TwilioSettings.objects.filter(company=company, is_enabled=True).first()
        if not twilio_settings:
            setup_error = "sms_not_configured"
    else:
        template_id = batch.message_payload.get("template_id")
        template = MessageTemplate.objects.filter(id=template_id, company=company).first() if template_id else None
        if not template:
            setup_error = "whatsapp_template_not_found"
        else:
            wa_account, wa_err = resolve_whatsapp_account_for_api(
                company, batch.message_payload.get("phone_number_id")
            )
            if not wa_account:
                setup_error = wa_err or "no_connected_whatsapp_number"

    sent = 0
    failed = 0

    if setup_error:
        for recipient in batch.audience_snapshot:
            MessageCampaignFailure.objects.create(
                batch=batch,
                client_id=recipient.get("client_id"),
                phone_number=recipient.get("phone_number") or "",
                error=setup_error[:512],
            )
            failed += 1
        batch.sent_count = sent
        batch.failed_count = failed
        batch.status = CampaignBatchStatus.COMPLETED
        batch.save(update_fields=["sent_count", "failed_count", "status"])
        notify_campaign_batch_complete(batch)
        return

    try:
        for recipient in batch.audience_snapshot:
            client_id = recipient.get("client_id")
            client = Client.objects.filter(id=client_id, company=company).first()
            phone = recipient.get("phone_number") or (getattr(client, "phone_number", None) or "")
            error_message = None
            try:
                if is_sms:
                    body = render_message_placeholders_for_client(
                        batch.message_payload.get("body", ""), client, employee=batch.requested_by,
                    ) if client else batch.message_payload.get("body", "")
                    ok, external_id, error_key, error_msg, provider_used = send_company_sms(
                        twilio_settings, to_phone=phone, body=body,
                    )
                    if not ok:
                        error_message = error_msg or error_key or "send_failed"
                    else:
                        LeadSMSMessage.objects.create(
                            client=client,
                            phone_number=phone,
                            body=body,
                            direction=LeadSMSMessage.DIRECTION_OUTBOUND,
                            provider=provider_used,
                            external_message_id=external_id,
                            created_by=batch.requested_by,
                            send_source=MessageSendSource.CAMPAIGN,
                            campaign_batch=batch,
                        )
                else:
                    ok, external_id, error_key, error_msg, _details = send_whatsapp_template_message(
                        company,
                        wa_account,
                        to=phone,
                        template=template,
                        fill_client=client,
                        sender_name=sender_name,
                        created_by=batch.requested_by,
                        send_source=MessageSendSource.CAMPAIGN,
                        campaign_batch=batch,
                    )
                    if not ok:
                        error_message = error_msg or error_key or "send_failed"
                    time.sleep(_WHATSAPP_SEND_PACING_SECONDS)
            except Exception as exc:  # noqa: BLE001 - one recipient's failure must not abort the batch
                error_message = str(exc)[:512]

            if error_message:
                failed += 1
                MessageCampaignFailure.objects.create(
                    batch=batch, client=client, phone_number=phone, error=error_message[:512],
                )
            else:
                sent += 1

            batch.sent_count = sent
            batch.failed_count = failed
            batch.save(update_fields=["sent_count", "failed_count"])

        batch.status = CampaignBatchStatus.COMPLETED
        batch.save(update_fields=["status"])
    except Exception:
        logger.exception("Campaign batch task crashed id=%s", batch_id)
        batch.status = CampaignBatchStatus.FAILED
        batch.save(update_fields=["status"])
        raise
    finally:
        notify_campaign_batch_complete(batch)


def enqueue_campaign_batch_send(batch_id: int) -> bool:
    """
    Hand an approved campaign batch to the cluster.

    Unlike push (notifications/tasks.py::enqueue_push), there is no inline
    fallback here: a bulk send must never run synchronously inside the
    approve request/response cycle. If enqueueing fails, the batch stays
    APPROVED for manual retry.
    """
    try:
        from django_q.tasks import async_task

        async_task(
            CAMPAIGN_SEND_TASK_PATH,
            batch_id,
            task_name=f"campaign_send:{batch_id}"[:100],
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue campaign batch id=%s; leaving APPROVED for manual retry", batch_id)
        return False
