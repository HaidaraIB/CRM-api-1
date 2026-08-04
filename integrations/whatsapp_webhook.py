"""
WhatsApp Business API Webhook Handler
- GET: التحقق (hub.mode, hub.verify_token, hub.challenge) → إرجاع challenge
- POST: استقبال الرسائل من entry[0].changes[0].value.messages وربطها بـ tenant عبر phone_number_id
"""
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from .models import IntegrationAccount, IntegrationLog, WhatsAppAccount, LeadWhatsAppMessage
from crm.models import ClientEvent
import json
import hmac
import hashlib
import logging
from django.conf import settings
from settings.models import SystemSettings
from .policy import get_effective_integration_policy, get_plan_integration_access

logger = logging.getLogger(__name__)


def verify_whatsapp_webhook_signature(request):
    """
    التحقق من توقيع WhatsApp Webhook.
    الأولوية لـ WHATSAPP_CLIENT_SECRET، مع fallback إلى META_CLIENT_SECRET.
    """
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature:
        return False
    
    if not signature.startswith('sha256='):
        return False
    
    received_signature = signature[7:]
    
    app_secret = (
        getattr(settings, 'WHATSAPP_CLIENT_SECRET', '')
        or getattr(settings, 'META_CLIENT_SECRET', '')
    )
    if not app_secret:
        logger.warning("Neither WHATSAPP_CLIENT_SECRET nor META_CLIENT_SECRET is set")
        return False
    
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        request.body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(received_signature, expected_signature)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    """
    Webhook endpoint لاستقبال الرسائل من WhatsApp Business API
    
    GET: للتحقق من Webhook (WhatsApp Challenge)
    POST: لاستقبال الرسائل الواردة
    """
    if request.method == 'GET':
        # WhatsApp Webhook Verification
        mode = request.GET.get('hub.mode')
        token = (request.GET.get('hub.verify_token') or '').strip()
        challenge = request.GET.get('hub.challenge')
        
        verify_token = (
            getattr(settings, 'WHATSAPP_WEBHOOK_VERIFY_TOKEN', None)
            or getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')
        )
        if isinstance(verify_token, str):
            verify_token = verify_token.strip()
        token_ok = token == verify_token
        if mode == 'subscribe' and token_ok:
            logger.info("WhatsApp webhook GET verify succeeded (use this callback URL in Meta with the same verify token)")
            return HttpResponse(challenge, content_type='text/plain')
        else:
            logger.warning(
                "WhatsApp webhook GET verify failed: mode=%s token_configured=%s token_match=%s "
                "incoming_token_len=%s expected_token_len=%s",
                mode,
                bool(verify_token),
                token_ok,
                len(token),
                len(verify_token),
            )
            return HttpResponse('Forbidden', status=403)
    
    # POST: استقبال الرسائل
    if request.method == 'POST':
        # اختياري: التحقق من IP إذا ضُبط WHATSAPP_WEBHOOK_ALLOWED_IPS (قائمة عناوين Meta)
        allowed_ips = getattr(settings, 'WHATSAPP_WEBHOOK_ALLOWED_IPS', None)
        if allowed_ips:
            client_ip = request.META.get('REMOTE_ADDR', '')
            if client_ip not in list(allowed_ips):
                logger.warning("WhatsApp webhook: IP %s not in allowed list", client_ip)
                return HttpResponse('Forbidden', status=403)
        # التحقق من التوقيع
        if not verify_whatsapp_webhook_signature(request):
            logger.warning("WhatsApp webhook signature verification failed")
            return HttpResponse('Unauthorized', status=401)
        
        try:
            payload = json.loads(request.body)
            entry = payload.get('entry', [])
            n_changes = sum(len(e.get('changes') or []) for e in entry)
            logger.info(
                "WhatsApp webhook POST: entries=%s changes=%s (full payload at DEBUG)",
                len(entry),
                n_changes,
            )
            logger.debug("WhatsApp webhook payload: %s", json.dumps(payload, indent=2))
            
            # WhatsApp يرسل البيانات في entry[0].changes[0].value
            if not entry:
                logger.warning("No entry in WhatsApp webhook payload")
                return JsonResponse({'status': 'ok'}, status=200)
            
            for entry_item in entry:
                waba_id = str(entry_item.get('id') or '').strip() or None
                changes = entry_item.get('changes', [])
                for change in changes:
                    field = (change.get('field') or '').strip()
                    value = change.get('value', {}) or {}

                    try:
                        if field == 'smb_app_state_sync':
                            process_smb_app_state_sync(value, waba_id=waba_id)
                        elif field == 'history':
                            process_history_sync(value, waba_id=waba_id)
                        elif field == 'smb_message_echoes':
                            process_smb_message_echoes(value, waba_id=waba_id)
                        elif field == 'account_update':
                            process_account_update(value, waba_id=waba_id)
                        elif field == 'calls':
                            from integrations.services.whatsapp_calling import (
                                process_calls_webhook_value,
                            )

                            process_calls_webhook_value(value, waba_id=waba_id)
                        elif field == 'messages' or (
                            not field and ('messages' in value or 'statuses' in value)
                        ):
                            # field=messages carries inbound messages and/or delivery statuses
                            phone_number_id = value.get('metadata', {}).get('phone_number_id')
                            messages = value.get('messages') or []
                            statuses = value.get('statuses') or []
                            if messages:
                                if not phone_number_id:
                                    logger.warning(
                                        "WhatsApp webhook: missing phone_number_id in value.metadata"
                                    )
                                else:
                                    logger.info(
                                        "WhatsApp webhook inbound: phone_number_id=%s messages_count=%s",
                                        phone_number_id,
                                        len(messages),
                                    )
                                    for message in messages:
                                        try:
                                            process_whatsapp_message(message, phone_number_id)
                                        except Exception as e:
                                            logger.error(
                                                "Error processing WhatsApp message: %s",
                                                e,
                                                exc_info=True,
                                            )
                            if statuses:
                                logger.info(
                                    "WhatsApp webhook statuses: phone_number_id=%s count=%s",
                                    phone_number_id,
                                    len(statuses),
                                )
                                for status_obj in statuses:
                                    try:
                                        process_whatsapp_status_update(status_obj, phone_number_id)
                                    except Exception as e:
                                        logger.error(
                                            "Error processing WhatsApp status update: %s",
                                            e,
                                            exc_info=True,
                                        )
                        else:
                            logger.debug(
                                "WhatsApp webhook unhandled field=%s keys=%s",
                                field or '(none)',
                                list(value.keys()) if isinstance(value, dict) else type(value),
                            )
                    except Exception as e:
                        logger.error(
                            "Error processing WhatsApp webhook change field=%s: %s",
                            field,
                            e,
                            exc_info=True,
                        )
                        continue
            
            return JsonResponse({'status': 'ok'}, status=200)
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON in WhatsApp webhook payload")
            return HttpResponse('Bad Request', status=400)
        except Exception as e:
            logger.error(f"Error processing WhatsApp webhook: {str(e)}", exc_info=True)
            return HttpResponse('Internal Server Error', status=500)


def process_platform_admin_inbound(message):
    """
    Inbound to the platform WhatsApp number: map sender to a company owner for admin-panel thread.
    """
    from accounts.models import User, Role
    from companies.models import AdminTenantWhatsAppMessage
    from accounts.platform_whatsapp import normalize_phone_digits

    from_number = message.get("from")
    message_id = message.get("id")
    message_type = message.get("type")
    if message_type == "text":
        text_body = message.get("text", {}).get("body", "")
    else:
        text_body = f"[{message_type} message]"

    digits = normalize_phone_digits(from_number or "")
    if not digits:
        return

    qs = User.objects.filter(role=Role.ADMIN.value, company__isnull=False).select_related("company")
    for user in qs.iterator(chunk_size=500):
        if normalize_phone_digits(user.phone or "") != digits:
            continue
        company = user.company
        if company.owner_id != user.id:
            continue
        AdminTenantWhatsAppMessage.objects.create(
            company=company,
            direction=AdminTenantWhatsAppMessage.DIRECTION_INBOUND,
            body=(text_body or "")[:65535],
            whatsapp_message_id=message_id,
        )
        logger.info(
            "Platform WhatsApp inbound matched company_id=%s",
            company.id,
        )
        return
    logger.info(
        "Platform WhatsApp inbound: no tenant owner matched for ...%s",
        digits[-4:] if len(digits) >= 4 else "****",
    )


def process_whatsapp_message(message, phone_number_id):
    """
    معالجة رسالة WhatsApp واردة.
    Multi-tenant: نستخرج phone_number_id → نبحث في WhatsAppAccount → نحصل على tenant (company).
    Platform Company WhatsApp number always routes to admin↔owner thread (never tenant CRM),
    even if a WhatsAppAccount row incorrectly reuses the same phone_number_id.
    """
    from accounts.platform_whatsapp import effective_platform_phone_number_id
    from integrations.services.whatsapp_media import (
        apply_meta_media_to_message,
        extract_meta_media_info,
        media_body_from_meta_message,
    )

    from_number = message.get('from')
    message_id = message.get('id')
    message_type = message.get('type')

    if message_type == 'text':
        text_body = message.get('text', {}).get('body', '')
    else:
        text_body = media_body_from_meta_message(message) or f"[{message_type} message]"

    if not from_number:
        logger.warning("No 'from' number in WhatsApp message")
        return

    platform_pid = effective_platform_phone_number_id()
    if platform_pid and str(phone_number_id) == str(platform_pid):
        process_platform_admin_inbound(message)
        return

    wa_account = WhatsAppAccount.objects.filter(
        phone_number_id=phone_number_id,
        status='connected',
    ).select_related('company', 'integration_account').first()

    if not wa_account:
        logger.warning(
            "No WhatsAppAccount found for phone_number_id=%s. "
            "This ID must equal whatsapp_accounts.phone_number_id for your connected number. "
            "Meta dashboard 'Test' events often use a sample ID (e.g. 123456123) — send a real message to your business "
            "number instead, or compare with phone_number_id in your successful outbound send logs / "
            "python manage.py whatsapp_debug_check",
            phone_number_id,
        )
        return

    try:
        logger.info(
            "WhatsApp inbound matched tenant: phone_number_id=%s company_id=%s",
            phone_number_id,
            wa_account.company_id,
        )
        company = wa_account.company
        gate = get_effective_integration_policy(
            SystemSettings.get_settings().integration_policies or {},
            company_id=company.id,
            platform="whatsapp",
        )
        plan_gate = get_plan_integration_access(company, "whatsapp")
        if not plan_gate["enabled"]:
            logger.info(
                "WhatsApp inbound ignored (integration not in plan) company_id=%s phone_number_id=%s",
                company.id,
                phone_number_id,
            )
            return
        if not gate["enabled"]:
            logger.info(
                "WhatsApp inbound ignored (integration disabled) company_id=%s phone_number_id=%s",
                company.id,
                phone_number_id,
            )
            return
        account = wa_account.integration_account

        from integrations.services.whatsapp_client import (
            ensure_client_for_whatsapp_phone,
            touch_client_last_contacted,
        )

        client = ensure_client_for_whatsapp_phone(company, from_number, integration_account=account)
        if not client:
            logger.warning("WhatsApp inbound: could not resolve client for from=%s", from_number)
            return

        touch_client_last_contacted(client)

        if message_id and LeadWhatsAppMessage.objects.filter(whatsapp_message_id=message_id).exists():
            logger.info("WhatsApp inbound duplicate skipped message_id=%s", message_id)
            return

        from datetime import datetime, timezone as dt_timezone

        row = LeadWhatsAppMessage(
            client=client,
            phone_number=from_number,
            body=text_body,
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            whatsapp_message_id=message_id,
            phone_number_id=str(phone_number_id) if phone_number_id else None,
            is_read=False,
        )
        access_token = wa_account.get_access_token()
        if access_token and extract_meta_media_info(message):
            apply_meta_media_to_message(row, message, access_token=access_token)
        row.save()
        # Prefer Meta message timestamp for accurate 24h customer-service window.
        ts_raw = message.get('timestamp')
        if ts_raw:
            try:
                ts = datetime.fromtimestamp(int(ts_raw), tz=dt_timezone.utc)
                LeadWhatsAppMessage.objects.filter(pk=row.pk).update(created_at=ts)
            except (TypeError, ValueError, OSError):
                pass

        # Customer replied ⇒ treat recent outbound as read (covers disabled read receipts).
        LeadWhatsAppMessage.objects.filter(
            client=client,
            direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
        ).exclude(
            delivery_status='failed',
        ).exclude(
            delivery_status='read',
        ).update(delivery_status='read', delivery_error=None)

        ClientEvent.objects.create(
            client=client,
            event_type='whatsapp_message',
            new_value=text_body[:255],
            notes=f"WhatsApp message received: {message_type}",
        )

        if account:
            IntegrationLog.objects.create(
                account=account,
                action='whatsapp_message_received',
                status='success',
                message=f'WhatsApp message received from {from_number}',
                response_data={
                    'message_id': message_id,
                    'message_type': message_type,
                    'from': from_number,
                },
            )

        logger.info("Processed WhatsApp message from %s for client %s", from_number, client.id)

        from integrations.services.whatsapp_push import notify_whatsapp_inbound

        notify_whatsapp_inbound(
            client=client,
            body=text_body,
            phone=from_number,
            message_id=message_id,
        )

    except Exception as e:
        logger.error("Error processing WhatsApp message: %s", e, exc_info=True)
        raise


# Meta status progression; never downgrade (e.g. read → delivered).
_WA_STATUS_RANK = {
    'pending': 0,
    'sent': 1,
    'delivered': 2,
    'read': 3,
    'failed': 4,
}


def process_whatsapp_status_update(status_obj, phone_number_id=None):
    """
    Update outbound LeadWhatsAppMessage rows from Meta delivery webhooks.
    Status values: sent, delivered, read, failed.
    """
    message_id = status_obj.get('id')
    status = (status_obj.get('status') or '').strip().lower()
    recipient = status_obj.get('recipient_id')
    errors = status_obj.get('errors') or []

    if not message_id or not status:
        return

    error_text = ''
    if errors:
        err = errors[0] if isinstance(errors[0], dict) else {}
        code = err.get('code')
        title = err.get('title') or err.get('message') or ''
        details = err.get('error_data', {}).get('details') if isinstance(err.get('error_data'), dict) else ''
        error_text = ': '.join(
            part for part in (str(code) if code else '', title, details) if part
        )[:512]

    qs = LeadWhatsAppMessage.objects.filter(
        whatsapp_message_id=message_id,
        direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
    )
    msg_row = qs.only('id', 'delivery_status').first()
    updated = 0
    if msg_row:
        current = (msg_row.delivery_status or '').strip().lower() or 'pending'
        new_rank = _WA_STATUS_RANK.get(status, -1)
        cur_rank = _WA_STATUS_RANK.get(current, -1)
        # Always apply failed; otherwise only advance forward.
        if status == 'failed' or new_rank >= cur_rank:
            updated = qs.filter(pk=msg_row.pk).update(
                delivery_status=status,
                delivery_error=error_text if status == 'failed' else None,
            )
        else:
            updated = 0
            logger.debug(
                "WhatsApp status ignored (no downgrade): wam_id=%s current=%s incoming=%s",
                message_id,
                current,
                status,
            )

    redacted_recipient = (
        f"...{str(recipient)[-4:]}" if recipient and len(str(recipient)) >= 4 else '????'
    )

    if status == 'failed':
        logger.warning(
            "WhatsApp message delivery failed: wam_id=%s phone_number_id=%s recipient=%s error=%s",
            message_id,
            phone_number_id,
            redacted_recipient,
            error_text or 'unknown',
        )
        msg = LeadWhatsAppMessage.objects.filter(whatsapp_message_id=message_id).select_related(
            'client__company'
        ).first()
        if msg and msg.client_id:
            account = WhatsAppAccount.objects.filter(
                company_id=msg.client.company_id,
                status='connected',
            ).select_related('integration_account').first()
            if account and account.integration_account_id:
                IntegrationLog.objects.create(
                    account_id=account.integration_account_id,
                    action='whatsapp_message_delivery_failed',
                    status='error',
                    message=f'WhatsApp delivery failed to {redacted_recipient}',
                    response_data={
                        'whatsapp_message_id': message_id,
                        'recipient_id': recipient,
                        'delivery_status': status,
                        'error': error_text,
                        'phone_number_id': phone_number_id,
                    },
                )
    elif updated:
        logger.info(
            "WhatsApp message status %s: wam_id=%s recipient=%s",
            status,
            message_id,
            redacted_recipient,
        )
    else:
        logger.debug(
            "WhatsApp status %s for wam_id=%s (no matching outbound LeadWhatsAppMessage)",
            status,
            message_id,
        )


def _resolve_wa_account(phone_number_id=None, waba_id=None):
    qs = WhatsAppAccount.objects.filter(status='connected').select_related(
        'company', 'integration_account'
    )
    if phone_number_id:
        wa = qs.filter(phone_number_id=str(phone_number_id)).first()
        if wa:
            return wa
    if waba_id:
        return qs.filter(waba_id=str(waba_id)).first()
    return None


def _gate_whatsapp_inbound(wa_account):
    """Return True if plan/policy allow processing for this WhatsApp account."""
    if not wa_account:
        return False
    company = wa_account.company
    gate = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform="whatsapp",
    )
    plan_gate = get_plan_integration_access(company, "whatsapp")
    if not plan_gate["enabled"] or not gate["enabled"]:
        logger.info(
            "WhatsApp coexistence webhook ignored (disabled) company_id=%s phone_number_id=%s",
            company.id,
            wa_account.phone_number_id,
        )
        return False
    return True


def process_smb_app_state_sync(value, waba_id=None):
    """Upsert CRM clients from WhatsApp Business app contacts (coexistence)."""
    from integrations.services.whatsapp_client import ensure_client_for_whatsapp_phone
    from integrations.services.whatsapp_coexistence import digits_only

    phone_number_id = (value.get('metadata') or {}).get('phone_number_id')
    wa_account = _resolve_wa_account(phone_number_id=phone_number_id, waba_id=waba_id)
    if not wa_account:
        logger.warning(
            "smb_app_state_sync: no WhatsAppAccount for phone_number_id=%s waba_id=%s",
            phone_number_id,
            waba_id,
        )
        return
    if not _gate_whatsapp_inbound(wa_account):
        return

    account = wa_account.integration_account
    state_sync = value.get('state_sync') or []
    added = 0
    removed = 0
    for item in state_sync:
        if not isinstance(item, dict) or item.get('type') != 'contact':
            continue
        action = (item.get('action') or '').strip().lower()
        contact = item.get('contact') or {}
        phone = digits_only(contact.get('phone_number'))
        if not phone:
            continue
        if action == 'remove':
            removed += 1
            logger.info(
                "smb_app_state_sync remove contact phone=...%s company_id=%s (CRM client kept)",
                phone[-4:] if len(phone) >= 4 else '????',
                wa_account.company_id,
            )
            continue
        # add (or edit)
        full_name = (contact.get('full_name') or contact.get('first_name') or '').strip()
        client = ensure_client_for_whatsapp_phone(
            wa_account.company,
            phone,
            integration_account=account,
        )
        if client and full_name:
            default_name = f"WhatsApp: {phone}"
            if not client.name or client.name == default_name or client.name.startswith('WhatsApp:'):
                client.name = full_name[:255]
                client.save(update_fields=['name', 'updated_at'])
        added += 1

    logger.info(
        "smb_app_state_sync done: company_id=%s added_or_updated=%s removed=%s",
        wa_account.company_id,
        added,
        removed,
    )
    if account:
        IntegrationLog.objects.create(
            account=account,
            action='whatsapp_smb_app_state_sync',
            status='success',
            message=f'Contacts sync: {added} add/edit, {removed} remove',
            response_data={'added': added, 'removed': removed, 'phone_number_id': phone_number_id},
        )


def process_history_sync(value, waba_id=None):
    """Backfill chat history threads, or log when history sharing was declined."""
    from datetime import datetime, timezone as dt_timezone

    from integrations.services.whatsapp_client import (
        ensure_client_for_whatsapp_phone,
        touch_client_last_contacted,
    )
    from integrations.services.whatsapp_coexistence import (
        HISTORY_NOT_SHARED_ERROR_CODE,
        digits_only,
        extract_whatsapp_message_body,
    )
    from integrations.services.whatsapp_media import (
        apply_meta_media_to_message,
        extract_meta_media_info,
    )

    phone_number_id = (value.get('metadata') or {}).get('phone_number_id')
    display_phone = (value.get('metadata') or {}).get('display_phone_number')
    business_digits = digits_only(display_phone)

    # Media asset follow-up webhooks under field=history may use messages[] instead of history[].
    # Same wamid as the earlier media_placeholder row — hydrate that row; do not create a duplicate.
    if 'messages' in value and 'history' not in value:
        from integrations.services.whatsapp_media import (
            extract_meta_media_info,
            hydrate_existing_message_media,
        )

        wa_account = _resolve_wa_account(phone_number_id=phone_number_id, waba_id=waba_id)
        access_token = wa_account.get_access_token() if wa_account else None
        for message in value.get('messages') or []:
            if not isinstance(message, dict):
                continue
            try:
                message_id = message.get('id')
                if (
                    message_id
                    and extract_meta_media_info(message)
                    and access_token
                ):
                    row = LeadWhatsAppMessage.objects.filter(
                        whatsapp_message_id=message_id
                    ).first()
                    if row:
                        if hydrate_existing_message_media(
                            row, message, access_token=access_token
                        ):
                            logger.info(
                                "history media hydrated wamid=%s kind=%s",
                                message_id,
                                row.attachment_kind,
                            )
                        else:
                            logger.warning(
                                "history media hydrate failed wamid=%s",
                                message_id,
                            )
                        continue
                # No existing placeholder (or not a media payload): treat as normal inbound.
                process_whatsapp_message(message, phone_number_id)
            except Exception as e:
                logger.error("history media message error: %s", e, exc_info=True)
        return

    wa_account = _resolve_wa_account(phone_number_id=phone_number_id, waba_id=waba_id)
    if not wa_account:
        logger.warning(
            "history sync: no WhatsAppAccount for phone_number_id=%s waba_id=%s",
            phone_number_id,
            waba_id,
        )
        return
    if not _gate_whatsapp_inbound(wa_account):
        return

    account = wa_account.integration_account
    history_chunks = value.get('history') or []
    for chunk in history_chunks:
        if not isinstance(chunk, dict):
            continue
        errors = chunk.get('errors') or []
        if errors:
            err = errors[0] if isinstance(errors[0], dict) else {}
            code = err.get('code')
            if code == HISTORY_NOT_SHARED_ERROR_CODE or str(code) == str(HISTORY_NOT_SHARED_ERROR_CODE):
                logger.info(
                    "history sync declined by business: phone_number_id=%s company_id=%s",
                    phone_number_id,
                    wa_account.company_id,
                )
                if account:
                    IntegrationLog.objects.create(
                        account=account,
                        action='whatsapp_history_sync_declined',
                        status='success',
                        message='History sync turned off by the business in WhatsApp Business app',
                        response_data={'error': err, 'phone_number_id': phone_number_id},
                    )
                meta = dict((account.metadata if account else {}) or {})
                if account:
                    meta['coexistence_history_shared'] = False
                    account.metadata = meta
                    account.save(update_fields=['metadata', 'updated_at'])
                continue
            logger.warning("history sync error chunk: %s", err)
            continue

        threads = chunk.get('threads') or []
        stored = 0
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            user_phone = digits_only(thread.get('id'))
            if not user_phone:
                continue
            client = ensure_client_for_whatsapp_phone(
                wa_account.company,
                user_phone,
                integration_account=account,
            )
            if not client:
                continue
            for message in thread.get('messages') or []:
                if not isinstance(message, dict):
                    continue
                message_id = message.get('id')
                if message_id and LeadWhatsAppMessage.objects.filter(
                    whatsapp_message_id=message_id
                ).exists():
                    continue
                from_digits = digits_only(message.get('from'))
                # from == user phone → inbound; from == business (or anything else) → outbound
                if from_digits and from_digits == user_phone:
                    direction = LeadWhatsAppMessage.DIRECTION_INBOUND
                elif business_digits and from_digits == business_digits:
                    direction = LeadWhatsAppMessage.DIRECTION_OUTBOUND
                elif message.get('to'):
                    direction = LeadWhatsAppMessage.DIRECTION_OUTBOUND
                else:
                    direction = (
                        LeadWhatsAppMessage.DIRECTION_INBOUND
                        if from_digits == user_phone
                        else LeadWhatsAppMessage.DIRECTION_OUTBOUND
                    )
                body = extract_whatsapp_message_body(message) or f"[{message.get('type') or 'message'}]"
                # History import: treat as already read so reconnect does not flood the badge.
                row = LeadWhatsAppMessage(
                    client=client,
                    phone_number=user_phone,
                    body=body,
                    direction=direction,
                    whatsapp_message_id=message_id,
                    phone_number_id=str(phone_number_id) if phone_number_id else None,
                    delivery_status=(message.get('history_context') or {}).get('status'),
                    is_read=True,
                )
                access_token = wa_account.get_access_token()
                if access_token and extract_meta_media_info(message):
                    apply_meta_media_to_message(row, message, access_token=access_token)
                row.save()
                ts_raw = message.get('timestamp')
                if ts_raw:
                    try:
                        ts = datetime.fromtimestamp(int(ts_raw), tz=dt_timezone.utc)
                        LeadWhatsAppMessage.objects.filter(pk=row.pk).update(created_at=ts)
                    except (TypeError, ValueError, OSError):
                        pass
                stored += 1
            touch_client_last_contacted(client)

        progress = (chunk.get('metadata') or {}).get('progress')
        logger.info(
            "history sync chunk: company_id=%s stored=%s progress=%s",
            wa_account.company_id,
            stored,
            progress,
        )
        if account and stored:
            IntegrationLog.objects.create(
                account=account,
                action='whatsapp_history_sync',
                status='success',
                message=f'History sync stored {stored} messages',
                response_data={
                    'stored': stored,
                    'progress': progress,
                    'phone_number_id': phone_number_id,
                },
            )
            meta = dict(account.metadata or {})
            meta['coexistence_history_shared'] = True
            if progress is not None:
                meta['coexistence_history_progress'] = progress
            account.metadata = meta
            account.save(update_fields=['metadata', 'updated_at'])


def process_smb_message_echoes(value, waba_id=None):
    """Mirror outbound messages sent from the WhatsApp Business app into CRM threads."""
    from integrations.services.whatsapp_client import (
        ensure_client_for_whatsapp_phone,
        touch_client_last_contacted,
    )
    from integrations.services.whatsapp_coexistence import extract_whatsapp_message_body
    from integrations.services.whatsapp_media import (
        apply_meta_media_to_message,
        extract_meta_media_info,
    )

    phone_number_id = (value.get('metadata') or {}).get('phone_number_id')
    wa_account = _resolve_wa_account(phone_number_id=phone_number_id, waba_id=waba_id)
    if not wa_account:
        logger.warning(
            "smb_message_echoes: no WhatsAppAccount for phone_number_id=%s waba_id=%s",
            phone_number_id,
            waba_id,
        )
        return
    if not _gate_whatsapp_inbound(wa_account):
        return

    account = wa_account.integration_account
    echoes = value.get('message_echoes') or []
    for echo in echoes:
        if not isinstance(echo, dict):
            continue
        to_phone = echo.get('to')
        message_id = echo.get('id')
        if not to_phone:
            continue
        if message_id and LeadWhatsAppMessage.objects.filter(whatsapp_message_id=message_id).exists():
            continue
        client = ensure_client_for_whatsapp_phone(
            wa_account.company,
            to_phone,
            integration_account=account,
        )
        if not client:
            continue
        body = extract_whatsapp_message_body(echo) or f"[{echo.get('type') or 'message'}]"
        row = LeadWhatsAppMessage(
            client=client,
            phone_number=to_phone,
            body=body,
            direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
            whatsapp_message_id=message_id,
            phone_number_id=str(phone_number_id) if phone_number_id else None,
            delivery_status='sent',
        )
        access_token = wa_account.get_access_token()
        if access_token and extract_meta_media_info(echo):
            apply_meta_media_to_message(row, echo, access_token=access_token)
        row.save()
        touch_client_last_contacted(client)
        ClientEvent.objects.create(
            client=client,
            event_type='whatsapp_message',
            new_value=body[:255],
            notes='WhatsApp Business app message (coexistence echo)',
        )
        logger.info(
            "smb_message_echoes stored: company_id=%s client_id=%s wam_id=%s",
            wa_account.company_id,
            client.id,
            message_id,
        )


def process_account_update(value, waba_id=None):
    """Handle PARTNER_REMOVED / ACCOUNT_OFFBOARDED for coexistence disconnect."""
    event = (value.get('event') or '').strip().upper()
    if not event:
        return

    phone_number = (value.get('phone_number') or '').strip()
    disconnection_info = value.get('disconnection_info') or {}

    if event in ('PARTNER_REMOVED', 'ACCOUNT_OFFBOARDED'):
        qs = WhatsAppAccount.objects.filter(status='connected')
        if waba_id:
            qs = qs.filter(waba_id=str(waba_id))
        updated = 0
        for wa in qs.select_related('integration_account'):
            wa.status = 'disconnected'
            wa.save(update_fields=['status', 'updated_at'])
            updated += 1
            account = wa.integration_account
            if account:
                meta = dict(account.metadata or {})
                meta['coexistence_disconnected'] = True
                meta['coexistence_disconnect_event'] = event
                meta['coexistence_disconnection_info'] = disconnection_info
                account.metadata = meta
                # If this integration only had this WA number, mark disconnected.
                still_connected = WhatsAppAccount.objects.filter(
                    integration_account=account,
                    status='connected',
                ).exclude(pk=wa.pk).exists()
                if not still_connected:
                    account.status = 'disconnected'
                    account.error_message = f'WhatsApp disconnected ({event})'
                    account.save(update_fields=['status', 'error_message', 'metadata', 'updated_at'])
                else:
                    account.save(update_fields=['metadata', 'updated_at'])
                IntegrationLog.objects.create(
                    account=account,
                    action='whatsapp_partner_removed',
                    status='success',
                    message=f'WhatsApp account disconnected via {event}',
                    response_data={
                        'event': event,
                        'waba_id': waba_id,
                        'phone_number': phone_number,
                        'disconnection_info': disconnection_info,
                        'phone_number_id': wa.phone_number_id,
                    },
                )
        logger.info(
            "account_update %s: disconnected %s WhatsAppAccount(s) waba_id=%s",
            event,
            updated,
            waba_id,
        )
        return

    if event == 'ACCOUNT_RECONNECTED':
        logger.info("account_update ACCOUNT_RECONNECTED waba_id=%s (no auto-reconnect)", waba_id)
        return

    logger.debug("account_update ignored event=%s waba_id=%s", event, waba_id)

