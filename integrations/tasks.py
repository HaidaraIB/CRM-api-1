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

from django.core.cache import cache

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
_CAMPAIGN_TASK_BUDGET_SECONDS = 50  # stay under django-q cluster timeout (60s)
_CAMPAIGN_SEND_LOCK_TTL_SECONDS = 90
_CAMPAIGN_TASK_TIMEOUT_SECONDS = 55


def _campaign_send_lock_key(batch_id: int) -> str:
    return f"campaign_send:{batch_id}"


def _campaign_progress_offset(batch: MessageCampaignBatch) -> int:
    return int(batch.sent_count or 0) + int(batch.failed_count or 0)


def _resolve_batch_header_media_id(template, wa_account) -> tuple[str | None, str | None]:
    """Upload template header media once per batch; return (media_id, setup_error)."""
    from integrations.services.whatsapp_template_media import (
        read_template_header_bytes,
        template_requires_header_media,
    )
    from integrations.services.whatsapp_media import upload_media_to_meta

    if not template_requires_header_media(template):
        return None, None

    access_token = wa_account.get_access_token()
    if not access_token:
        return None, "whatsapp_no_access_token"

    try:
        data, mime, filename = read_template_header_bytes(template)
        media_id = upload_media_to_meta(
            phone_number_id=wa_account.phone_number_id,
            access_token=access_token,
            data=data,
            mime=mime,
            filename=filename,
        )
        return media_id, None
    except Exception as exc:  # noqa: BLE001 - surface as batch setup failure
        logger.warning("Campaign batch header media upload failed: %s", exc)
        return None, str(exc)[:512] or "whatsapp_header_media_upload_failed"


def send_campaign_batch_task(batch_id: int) -> None:
    """
    Worker entry point (runs in qcluster). Takes a primitive id, not a model
    instance, since django-q pickles task args and re-import happens in the
    worker process.

    Processes recipients in time-budgeted chunks, resuming from
    ``sent_count + failed_count`` and chaining another task when more remain.
    """
    lock_key = _campaign_send_lock_key(batch_id)
    if not cache.add(lock_key, "1", timeout=_CAMPAIGN_SEND_LOCK_TTL_SECONDS):
        logger.info("Skipping campaign batch task id=%s (another chunk is running)", batch_id)
        return

    batch = MessageCampaignBatch.objects.select_related("company", "requested_by").filter(id=batch_id).first()
    if batch is None or batch.status not in (CampaignBatchStatus.APPROVED, CampaignBatchStatus.SENDING):
        cache.delete(lock_key)
        logger.info(
            "Skipping campaign batch task id=%s (missing or not APPROVED/SENDING, status=%s)",
            batch_id,
            getattr(batch, "status", None),
        )
        return

    if batch.status == CampaignBatchStatus.APPROVED:
        batch.status = CampaignBatchStatus.SENDING
        batch.save(update_fields=["status"])

    company = batch.company
    is_sms = batch.channel == MessageCampaignBatch.CHANNEL_SMS
    sender_name = _user_display_name(batch.requested_by)
    audience = batch.audience_snapshot or []
    offset = _campaign_progress_offset(batch)
    sent = int(batch.sent_count or 0)
    failed = int(batch.failed_count or 0)

    twilio_settings = None
    wa_account = None
    template = None
    header_media_id = None
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
            else:
                header_media_id, media_err = _resolve_batch_header_media_id(template, wa_account)
                if media_err:
                    setup_error = media_err

    if setup_error:
        try:
            for recipient in audience[offset:]:
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
        finally:
            cache.delete(lock_key)
        return

    chunk_deadline = time.monotonic() + _CAMPAIGN_TASK_BUDGET_SECONDS
    more_remaining = False

    try:
        for recipient in audience[offset:]:
            if time.monotonic() >= chunk_deadline:
                more_remaining = True
                break

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
                        header_media_id=header_media_id,
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

        if more_remaining or _campaign_progress_offset(batch) < len(audience):
            enqueue_campaign_batch_send(batch_id)
            return

        batch.status = CampaignBatchStatus.COMPLETED
        batch.save(update_fields=["status"])
        notify_campaign_batch_complete(batch)
    except Exception:
        logger.exception("Campaign batch task crashed id=%s", batch_id)
        batch.status = CampaignBatchStatus.FAILED
        batch.save(update_fields=["status"])
        notify_campaign_batch_complete(batch)
        raise
    finally:
        cache.delete(lock_key)


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

        batch = MessageCampaignBatch.objects.filter(id=batch_id).only(
            "sent_count", "failed_count",
        ).first()
        offset = _campaign_progress_offset(batch) if batch else 0

        async_task(
            CAMPAIGN_SEND_TASK_PATH,
            batch_id,
            task_name=f"campaign_send:{batch_id}:{offset}"[:100],
            timeout=_CAMPAIGN_TASK_TIMEOUT_SECONDS,
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue campaign batch id=%s; leaving APPROVED for manual retry", batch_id)
        return False


def resume_stale_campaign_batches() -> int:
    """
    Re-enqueue campaign batches stuck in SENDING with no active worker lock.

    Covers worker kills that did not chain the next chunk. Returns the number
    of batches re-enqueued.
    """
    resumed = 0
    qs = MessageCampaignBatch.objects.filter(status=CampaignBatchStatus.SENDING)
    for batch in qs.iterator():
        if _campaign_progress_offset(batch) >= len(batch.audience_snapshot or []):
            batch.status = CampaignBatchStatus.COMPLETED
            batch.save(update_fields=["status"])
            notify_campaign_batch_complete(batch)
            continue
        if cache.get(_campaign_send_lock_key(batch.id)):
            continue
        if enqueue_campaign_batch_send(batch.id):
            resumed += 1
            logger.info("Re-enqueued stale campaign batch id=%s at offset=%s", batch.id, _campaign_progress_offset(batch))
    return resumed
