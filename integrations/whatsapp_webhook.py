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
from .decorators import rate_limit_webhook
from crm.models import Client, ClientPhoneNumber, ClientEvent
from crm.signals import get_least_busy_employee
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
@rate_limit_webhook(max_requests=100, window=60)
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
                changes = entry_item.get('changes', [])
                for change in changes:
                    value = change.get('value', {})
                    
                    # التحقق من نوع التغيير
                    if 'messages' in value:
                        # رسالة واردة: استخراج phone_number_id لربط الرسالة بـ tenant
                        messages = value.get('messages', [])
                        phone_number_id = value.get('metadata', {}).get('phone_number_id')
                        if not phone_number_id:
                            logger.warning("WhatsApp webhook: missing phone_number_id in value.metadata")
                            continue
                        logger.info(
                            "WhatsApp webhook inbound: phone_number_id=%s messages_count=%s",
                            phone_number_id,
                            len(messages),
                        )
                        for message in messages:
                            try:
                                process_whatsapp_message(message, phone_number_id)
                            except Exception as e:
                                logger.error(f"Error processing WhatsApp message: {str(e)}", exc_info=True)
                                continue
                    
                    elif 'statuses' in value:
                        # تحديث حالة الرسالة (delivered, read, etc.)
                        # يمكن تجاهله أو تسجيله
                        logger.debug("WhatsApp status update received")
            
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
    """
    platform_pid = getattr(settings, "PLATFORM_WHATSAPP_PHONE_NUMBER_ID", "") or ""
    if platform_pid and str(phone_number_id) == str(platform_pid):
        process_platform_admin_inbound(message)
        return

    from_number = message.get('from')
    message_id = message.get('id')
    message_type = message.get('type')
    timestamp = message.get('timestamp')
    
    if message_type == 'text':
        text_body = message.get('text', {}).get('body', '')
    else:
        text_body = f"[{message_type} message]"
    
    if not from_number:
        logger.warning("No 'from' number in WhatsApp message")
        return
    
    try:
        # البحث عن WhatsAppAccount بالـ phone_number_id (كل عميل له رقم مختلف)
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
        
        # البحث عن Client موجود برقم الهاتف
        client = None
        
        # البحث في ClientPhoneNumber
        phone_number_obj = ClientPhoneNumber.objects.filter(
            phone_number=from_number,
            client__company=company
        ).first()
        
        if phone_number_obj:
            client = phone_number_obj.client
        else:
            client = Client.objects.filter(
                phone_number=from_number,
                company=company
            ).first()
        
        if not client:
            name = f"WhatsApp: {from_number}"
            client = Client.objects.create(
                name=name,
                priority='medium',
                type='fresh',
                company=company,
                source='whatsapp',
                integration_account=account,
                phone_number=from_number,
            )
            
            # إضافة رقم الهاتف
            ClientPhoneNumber.objects.create(
                client=client,
                phone_number=from_number,
                phone_type='mobile',
                is_primary=True,
            )
            
            # Auto-assignment إذا كان مفعّل
            if company.auto_assign_enabled:
                employee = get_least_busy_employee(company)
                if employee:
                    client.assigned_to = employee
                    client.assigned_at = timezone.now()
                    client.save()
                    
                    ClientEvent.objects.create(
                        client=client,
                        event_type='assignment',
                        old_value='Unassigned',
                        new_value=employee.get_full_name() or employee.username,
                        notes=f"Auto-assigned from WhatsApp",
                    )
            
            # تسجيل الحدث
            ClientEvent.objects.create(
                client=client,
                event_type='created',
                new_value='WhatsApp',
                notes=f"Client created from WhatsApp message",
            )
            
            logger.info(f"Created new client from WhatsApp: {client.id} - {client.name}")
        
        # تحديث last_contacted_at
        client.last_contacted_at = timezone.now()
        client.save(update_fields=['last_contacted_at'])
        
        # تخزين الرسالة في LeadWhatsAppMessage للتايملاين ومركز المراسلات
        LeadWhatsAppMessage.objects.create(
            client=client,
            phone_number=from_number,
            body=text_body,
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            whatsapp_message_id=message_id,
        )
        
        # تسجيل الرسالة في ClientEvent (اختياري)
        ClientEvent.objects.create(
            client=client,
            event_type='whatsapp_message',
            new_value=text_body[:255],  # أول 255 حرف
            notes=f"WhatsApp message received: {message_type}",
        )
        
        # تسجيل في IntegrationLog إن وُجد integration_account
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
        
    except Exception as e:
        logger.error("Error processing WhatsApp message: %s", e, exc_info=True)
        raise



