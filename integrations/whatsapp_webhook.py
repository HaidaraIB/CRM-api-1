"""
WhatsApp Business API Webhook Handler
"""
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from .models import IntegrationAccount, IntegrationLog
from .decorators import rate_limit_webhook
from crm.models import Client, ClientPhoneNumber, ClientEvent
from crm.signals import get_least_busy_employee
import json
import hmac
import hashlib
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def verify_whatsapp_webhook_signature(request):
    """
    التحقق من توقيع WhatsApp Webhook
    
    WhatsApp يستخدم X-Hub-Signature-256 (نفس Meta)
    """
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature:
        return False
    
    if not signature.startswith('sha256='):
        return False
    
    received_signature = signature[7:]
    
    # WhatsApp يستخدم نفس App Secret مثل Meta
    app_secret = getattr(settings, 'META_CLIENT_SECRET', '')
    if not app_secret:
        logger.warning("META_CLIENT_SECRET not set in settings")
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
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        verify_token = getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')
        
        if mode == 'subscribe' and token == verify_token:
            logger.info("WhatsApp webhook verified successfully")
            return HttpResponse(challenge, content_type='text/plain')
        else:
            logger.warning(f"WhatsApp webhook verification failed: mode={mode}, token_match={token == verify_token}")
            return HttpResponse('Forbidden', status=403)
    
    # POST: استقبال الرسائل
    if request.method == 'POST':
        # التحقق من التوقيع
        if not verify_whatsapp_webhook_signature(request):
            logger.warning("WhatsApp webhook signature verification failed")
            return HttpResponse('Unauthorized', status=401)
        
        try:
            payload = json.loads(request.body)
            logger.info(f"WhatsApp webhook received: {json.dumps(payload, indent=2)}")
            
            # WhatsApp يرسل البيانات في entry[0].changes[0].value
            entry = payload.get('entry', [])
            if not entry:
                logger.warning("No entry in WhatsApp webhook payload")
                return JsonResponse({'status': 'ok'}, status=200)
            
            for entry_item in entry:
                changes = entry_item.get('changes', [])
                for change in changes:
                    value = change.get('value', {})
                    
                    # التحقق من نوع التغيير
                    if 'messages' in value:
                        # رسالة واردة
                        messages = value.get('messages', [])
                        phone_number_id = value.get('metadata', {}).get('phone_number_id')
                        
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


def process_whatsapp_message(message, phone_number_id):
    """
    معالجة رسالة WhatsApp واردة
    
    Args:
        message: بيانات الرسالة من WhatsApp
        phone_number_id: Phone Number ID المرسل منه
    """
    from_number = message.get('from')  # رقم المرسل
    message_id = message.get('id')
    message_type = message.get('type')  # text, image, etc.
    timestamp = message.get('timestamp')
    
    # استخراج محتوى الرسالة
    if message_type == 'text':
        text_body = message.get('text', {}).get('body', '')
    else:
        # للأنواع الأخرى (image, video, etc.) يمكن إضافة معالجة لاحقاً
        text_body = f"[{message_type} message]"
    
    if not from_number:
        logger.warning("No 'from' number in WhatsApp message")
        return
    
    # البحث عن IntegrationAccount المرتبط بهذا Phone Number ID
    try:
        account = IntegrationAccount.objects.filter(
            platform='whatsapp',
            status='connected',
            metadata__contains={'phone_number_id': phone_number_id}
        ).first()
        
        if not account:
            logger.warning(f"No WhatsApp integration account found for phone_number_id={phone_number_id}")
            return
        
        # البحث عن Client موجود برقم الهاتف
        client = None
        
        # البحث في ClientPhoneNumber
        phone_number_obj = ClientPhoneNumber.objects.filter(
            phone_number=from_number,
            client__company=account.company
        ).first()
        
        if phone_number_obj:
            client = phone_number_obj.client
        else:
            # البحث في phone_number القديم (للتوافق)
            client = Client.objects.filter(
                phone_number=from_number,
                company=account.company
            ).first()
        
        # إذا لم نجد Client، ننشئ واحد جديد
        if not client:
            # استخراج الاسم من الرسالة (إن أمكن) أو استخدام رقم الهاتف
            name = f"WhatsApp: {from_number}"
            
            client = Client.objects.create(
                name=name,
                priority='medium',
                type='fresh',
                company=account.company,
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
            if account.company.auto_assign_enabled:
                employee = get_least_busy_employee(account.company)
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
        
        # تسجيل الرسالة في ClientEvent (اختياري)
        ClientEvent.objects.create(
            client=client,
            event_type='whatsapp_message',
            new_value=text_body[:255],  # أول 255 حرف
            notes=f"WhatsApp message received: {message_type}",
        )
        
        # تسجيل في IntegrationLog
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
        
        logger.info(f"Processed WhatsApp message from {from_number} for client {client.id}")
        
    except Exception as e:
        logger.error(f"Error processing WhatsApp message: {str(e)}", exc_info=True)
        raise



