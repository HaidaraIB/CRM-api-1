import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.db.models import Q
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from crm_saas_api.responses import error_response, success_response, validation_error_response

from accounts.permissions import HasActiveSubscription
from ..decorators import rate_limit_webhook
from ..models import (
    IntegrationAccount, IntegrationLog, IntegrationPlatform,
    WhatsAppAccount, OAuthState, TwilioSettings,
    LeadSMSMessage, LeadWhatsAppMessage, MessageTemplate,
)
from ..oauth_utils import get_oauth_handler, MetaOAuth
from ..serializers import (
    IntegrationAccountSerializer,
    IntegrationAccountCreateSerializer,
    IntegrationAccountUpdateSerializer,
    IntegrationAccountDetailSerializer,
    IntegrationLogSerializer,
    OAuthCallbackSerializer,
    TwilioSettingsSerializer,
    LeadSMSMessageSerializer,
    SendLeadSMSSerializer,
    LeadWhatsAppMessageSerializer,
    MessageTemplateSerializer,
)

logger = logging.getLogger(__name__)
@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_send_message(request):
    """
    إرسال رسالة واتساب من رقم الشركة المتصل.
    POST /api/integrations/whatsapp/send/
    Body: { "phone_number_id": "optional", "to": "971501234567", "message": "نص الرسالة", "client_id": "optional" }
    """
    company = request.user.company
    # Plan gating: feature + monthly usage
    from subscriptions.entitlements import require_feature, require_monthly_usage, increment_monthly_usage
    require_feature(
        company,
        "whatsapp_enabled",
        message="WhatsApp is not available in your current plan. Please upgrade your plan.",
        error_key="plan_feature_whatsapp_disabled",
    )
    require_monthly_usage(
        company,
        "monthly_whatsapp_messages",
        requested_delta=1,
        message="You have reached your monthly WhatsApp messages limit. Please upgrade your plan.",
        error_key="plan_usage_monthly_whatsapp_exceeded",
    )
    phone_number_id = request.data.get('phone_number_id')
    to = request.data.get('to')
    message = request.data.get('message') or request.data.get('text')
    client_id = request.data.get('client_id')
    if not to or not message:
        return error_response(
            'to and message are required',
            code='bad_request',
        )
    # تطبيع رقم المستلم (إزالة + وفراغات)
    to = str(to).replace(' ', '').replace('+', '').strip()
    if not to.isdigit():
        return error_response(
            'Invalid "to" phone number',
            code='bad_request',
        )
    qs = WhatsAppAccount.objects.filter(company=company, status='connected')
    if phone_number_id:
        qs = qs.filter(phone_number_id=phone_number_id)
    wa_account = qs.first()
    if not wa_account:
        return error_response(
            'No connected WhatsApp number for this company',
            code='no_connected_whatsapp_number',
            status_code=status.HTTP_404_NOT_FOUND,
        )
    access_token = wa_account.get_access_token()
    if not access_token:
        return error_response(
            'WhatsApp account has no access token',
            code='whatsapp_no_access_token',
        )
    url = f"https://graph.facebook.com/v18.0/{wa_account.phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": message[:4096]},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Increment usage after success only
        increment_monthly_usage(company, "monthly_whatsapp_messages", requested_delta=1)
        if wa_account.integration_account_id:
            IntegrationLog.objects.create(
                account_id=wa_account.integration_account_id,
                action='whatsapp_message_sent',
                status='success',
                message=f'Message sent to {to}',
                response_data=data,
            )
        # تخزين الرسالة في LeadWhatsAppMessage للتايملاين ومركز المراسلات (عند وجود client_id)
        wam_id = (data.get('messages') or [{}])[0].get('id') if isinstance(data.get('messages'), list) else None
        if client_id:
            try:
                client = company.clients.get(id=client_id)
                LeadWhatsAppMessage.objects.create(
                    client=client,
                    phone_number=to,
                    body=(message or '')[:65535],
                    direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
                    whatsapp_message_id=wam_id,
                    created_by=request.user,
                )
            except Exception:
                pass
        return success_response(data=data)
    except requests.RequestException as e:
        err_body = {'error': str(e)}
        if getattr(e, 'response', None) is not None:
            r = e.response
            try:
                err_body = r.json()
            except Exception:
                err_body = {'error': getattr(r, 'text', str(r))}
        logger.warning("WhatsApp send failed: %s", err_body)
        return error_response(
            "WhatsApp API request failed.",
            code="bad_request",
            details=err_body if isinstance(err_body, dict) else {"error": str(err_body)},
        )


# ==================== TikTok Lead Gen Webhook ====================
# استقبال ليدز من TikTok Instant Form (TikTok Marketing API / Leads Center).
# يُسجّل في TikTok Ads Manager → Leads Center → CRM integration → Custom API with Webhooks.


def _parse_tiktok_leadgen_payload(body):
    """
    استخراج lead_id, advertiser_id, name, phone, email من payload TikTok Lead Gen.
    يدعم أشكالاً متعددة حسب وثائق TikTok Marketing API / شركاء التكامل.
    """
    if not body:
        return None
    # دعم content كـ JSON string (أحياناً يُرسل هكذا)
    data = body.get('data') or body
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    content_str = body.get('content')
    if content_str and isinstance(content_str, str):
        try:
            data = json.loads(content_str)
        except json.JSONDecodeError:
            pass
    if not isinstance(data, dict):
        return None
    lead_id = data.get('lead_id') or data.get('id') or data.get('leadId')
    form_id = data.get('form_id') or data.get('formId')
    advertiser_id = str(data.get('advertiser_id') or data.get('advertiserId') or '')
    # استخراج الاسم والهاتف والبريد من list من نوع [{"key":"Full name","value":"x"}] أو من dict
    name = None
    phone = None
    email = None
    lead_list = data.get('list') or data.get('answers') or data.get('field_data') or []
    if isinstance(lead_list, list):
        key_map = {}
        for item in lead_list:
            if isinstance(item, dict):
                k = (item.get('key') or item.get('name') or item.get('label') or '').lower()
                v = item.get('value') or item.get('values')
                if isinstance(v, list):
                    v = v[0] if v else ''
                if k and v:
                    key_map[k] = str(v).strip()
        name = (key_map.get('full name') or key_map.get('full_name') or key_map.get('name') or
                key_map.get('contact name') or key_map.get('contact_name') or '')
        phone = (key_map.get('phone') or key_map.get('phone_number') or key_map.get('mobile') or
                 key_map.get('tel') or key_map.get('contact phone') or '')
        email = (key_map.get('email') or key_map.get('email_address') or key_map.get('e-mail') or '')
    if isinstance(data.get('answers'), dict):
        ans = data['answers']
        name = name or ans.get('full_name') or ans.get('name') or ans.get('full name') or ''
        phone = phone or ans.get('phone') or ans.get('phone_number') or ans.get('mobile') or ''
        email = email or ans.get('email') or ans.get('email_address') or ''
    name = name or data.get('full_name') or data.get('name') or data.get('full name') or ''
    phone = phone or data.get('phone') or data.get('phone_number') or data.get('mobile') or ''
    email = email or data.get('email') or data.get('email_address') or ''
    if not lead_id and not name and not phone and not email:
        return None
    return {
        'lead_id': str(lead_id) if lead_id else None,
        'form_id': str(form_id) if form_id else None,
        'advertiser_id': advertiser_id or None,
        'name': (name or 'TikTok Lead').strip(),
        'phone': (phone or '').strip(),
        'email': (email or '').strip(),
        'raw': data,
    }


def _get_company_id_for_tiktok_leadgen(advertiser_id, request):
    """تحديد company_id من إعدادات التطبيق أو من query param."""
    company_id = request.GET.get('company_id')
    if company_id:
        try:
            return int(company_id)
        except (ValueError, TypeError):
            pass
    mapping = getattr(settings, 'TIKTOK_LEADGEN_ADVERTISER_MAPPING', '{}')
    if isinstance(mapping, str):
        try:
            mapping = json.loads(mapping) if mapping else {}
        except json.JSONDecodeError:
            mapping = {}
    if advertiser_id and mapping:
        cid = mapping.get(str(advertiser_id)) or mapping.get(advertiser_id)
        if cid is not None:
            try:
                return int(cid)
            except (ValueError, TypeError):
                pass
    default = getattr(settings, 'TIKTOK_LEADGEN_COMPANY_ID', '')
    if default:
        try:
            return int(default)
        except (ValueError, TypeError):
            pass
    return None


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit_webhook(max_requests=300, window=60)
def tiktok_leadgen_webhook(request):
    """
    Webhook لاستقبال ليدز TikTok Lead Generation (Instant Form).
    URL للتسجيل في TikTok Ads Manager: {API_BASE_URL}/api/integrations/webhooks/tiktok-leadgen/
    أو مع company_id: .../tiktok-leadgen/?company_id=1
    يرد 200 دائماً. يُنشئ Client في الـ CRM ويسجّل في IntegrationLog.
    """
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    # الرد 200 فوراً حتى لا تعيد TikTok المحاولة
    payload = _parse_tiktok_leadgen_payload(body)
    if not payload:
        logger.warning("TikTok Lead Gen webhook: could not parse payload: %s", json.dumps(body)[:300])
        return HttpResponse('OK', status=200)
    company_id = _get_company_id_for_tiktok_leadgen(payload.get('advertiser_id'), request)
    if not company_id:
        logger.warning("TikTok Lead Gen webhook: no company_id (set TIKTOK_LEADGEN_COMPANY_ID or ?company_id= or ADVERTISER_MAPPING)")
        return HttpResponse('OK', status=200)
    from companies.models import Company
    try:
        company = Company.objects.get(id=company_id)
    except Company.DoesNotExist:
        logger.warning("TikTok Lead Gen webhook: company_id=%s not found", company_id)
        return HttpResponse('OK', status=200)
    # Enforce plan quota for leads created via webhook
    try:
        from subscriptions.entitlements import require_quota
        from crm.models import Client as CRMClient
        current_clients = CRMClient.objects.filter(company=company).count()
        require_quota(
            company,
            "max_clients",
            current_count=current_clients,
            requested_delta=1,
            message="Lead limit reached for this company plan.",
            error_key="plan_quota_max_clients_exceeded",
        )
    except Exception as e:
        logger.warning("TikTok Lead Gen webhook: plan quota blocked lead creation for company_id=%s err=%s", company_id, str(e)[:200])
        return HttpResponse('OK', status=200)
    from ..models import IntegrationAccount, IntegrationLog
    from crm.models import Client, ClientPhoneNumber, ClientEvent
    from crm.signals import get_least_busy_employee
    account, _ = IntegrationAccount.objects.get_or_create(
        company=company,
        platform='tiktok',
        external_account_id='leadgen_%s' % company_id,
        defaults={
            'name': 'TikTok Lead Gen',
            'status': 'connected',
        },
    )
    lead_id = payload.get('lead_id')
    if lead_id:
        if IntegrationLog.objects.filter(
            account=account,
            action='tiktok_lead_received',
            response_data__lead_id=lead_id,
        ).exists():
            logger.info("TikTok Lead Gen: duplicate lead_id=%s ignored", lead_id)
            return HttpResponse('OK', status=200)
    try:
        client = Client.objects.create(
            name=payload['name'],
            priority='medium',
            type='fresh',
            company=company,
            source='tiktok',
            integration_account=account,
            phone_number=payload.get('phone') or None,
        )
        if payload.get('phone'):
            ClientPhoneNumber.objects.create(
                client=client,
                phone_number=payload['phone'],
                phone_type='mobile',
                is_primary=True,
            )
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
                    notes='Auto-assigned from TikTok Lead Gen',
                )
        created_notes = 'Lead from TikTok Instant Form (form_id=%s)' % (payload.get('form_id') or '')
        if payload.get('email'):
            created_notes += '. Email: %s' % payload['email']
        ClientEvent.objects.create(
            client=client,
            event_type='created',
            new_value='TikTok Lead Gen',
            notes=created_notes,
        )
        IntegrationLog.objects.create(
            account=account,
            action='tiktok_lead_received',
            status='success',
            message='Lead created: %s' % payload['name'],
            response_data={
                'lead_id': lead_id,
                'form_id': payload.get('form_id'),
                'advertiser_id': payload.get('advertiser_id'),
                'client_id': client.id,
            },
        )
        logger.info("TikTok Lead Gen: created client id=%s for company_id=%s", client.id, company_id)
    except Exception as e:
        logger.exception("TikTok Lead Gen webhook: failed to create client: %s", e)
        IntegrationLog.objects.create(
            account=account,
            action='tiktok_lead_received',
            status='error',
            message='Failed to create client',
            error_details=str(e),
            response_data={'lead_id': lead_id, 'payload_keys': list(payload.keys())},
        )
    return HttpResponse('OK', status=200)


# ==================== Webhook Views ====================

def verify_meta_webhook_signature(request):
    """التحقق من توقيع Meta Webhook"""
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature:
        return False
    
    # استخراج التوقيع من Header
    # Format: sha256=<signature>
    if not signature.startswith('sha256='):
        return False
    
    received_signature = signature[7:]  # Remove 'sha256=' prefix
    
    # حساب التوقيع المتوقع
    app_secret = getattr(settings, 'META_CLIENT_SECRET', '')
    if not app_secret:
        logger.warning("META_CLIENT_SECRET not set in settings")
        return False
    
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        request.body,
        hashlib.sha256
    ).hexdigest()
    
    # مقارنة التوقيعات بشكل آمن
    return hmac.compare_digest(received_signature, expected_signature)


def _q_json_id(field_name, raw_id):
    """
    Match JSONField key against str or int (Meta may send numeric ids as either).
    Avoids metadata__contains, which SQLite does not support for JSONField.
    """
    q = Q(**{f"metadata__{field_name}": str(raw_id)})
    try:
        if str(raw_id).strip().isdigit():
            q |= Q(**{f"metadata__{field_name}": int(str(raw_id).strip())})
    except (ValueError, TypeError):
        pass
    return q


@csrf_exempt
@require_http_methods(["GET", "POST"])
@rate_limit_webhook(max_requests=100, window=60)  # 100 requests per minute
def meta_webhook(request):
    """
    Webhook endpoint لاستقبال الليدز من Meta Lead Forms
    
    GET: للتحقق من Webhook (Meta Challenge)
    POST: لاستقبال الليدز الجديدة
    """
    # Log every Meta webhook call (use this to confirm Meta is hitting our URL)
    logger.info("META_WEBHOOK_CALLED method=%s", request.method)

    if request.method == 'GET':
        # Meta Webhook Verification
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        verify_token = getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')

        logger.info("META_WEBHOOK GET verification: hub.mode=%s, token_match=%s", mode, (token == verify_token))

        if mode == 'subscribe' and token == verify_token:
            logger.info("META_WEBHOOK verified successfully (challenge returned)")
            return HttpResponse(challenge, content_type='text/plain')
        else:
            logger.warning("META_WEBHOOK verification failed: mode=%s, token_match=%s", mode, (token == verify_token))
            return HttpResponse('Forbidden', status=403)

    # POST: استقبال الليدز
    if request.method == 'POST':
        if not verify_meta_webhook_signature(request):
            logger.warning("META_WEBHOOK POST signature verification failed")
            return HttpResponse('Unauthorized', status=401)

        logger.info("META_WEBHOOK POST signature valid, parsing payload")

        try:
            payload = json.loads(request.body)
            entry = payload.get('entry', [])
            n_entries = len(entry)
            n_changes = sum(len(e.get('changes', [])) for e in entry)
            logger.info(
                "META_WEBHOOK POST payload: object_id=%s, entries=%s, total_changes=%s",
                payload.get('object'),
                n_entries,
                n_changes,
            )
            if entry and entry[0].get('changes'):
                for i, ch in enumerate(entry[0]['changes']):
                    logger.info(
                        "META_WEBHOOK change[%s]: field=%s value_keys=%s",
                        i,
                        ch.get('field'),
                        list(ch.get('value', {}).keys()) if isinstance(ch.get('value'), dict) else None,
                    )
            
            # Meta يرسل البيانات في entry[0].changes[0].value
            entry = payload.get('entry', [])
            if not entry:
                logger.warning("META_WEBHOOK POST no entry in payload, returning 200")
                return JsonResponse({'status': 'ok'}, status=200)
            
            for entry_item in entry:
                changes = entry_item.get('changes', [])
                for change in changes:
                    if change.get('field') == 'leadgen':
                        value = change.get('value', {})
                        leadgen_id = value.get('leadgen_id')
                        form_id = value.get('form_id')
                        page_id = value.get('page_id')

                        logger.info(
                            "META_WEBHOOK processing leadgen: leadgen_id=%s form_id=%s page_id=%s",
                            leadgen_id,
                            form_id,
                            page_id,
                        )

                        if not leadgen_id or not form_id or not page_id:
                            logger.warning(f"Missing required fields in webhook: leadgen_id={leadgen_id}, form_id={form_id}, page_id={page_id}")
                            continue
                        
                        # البحث عن IntegrationAccount المرتبط بهذا form_id أو page_id
                        try:
                            account = None
                            
                            # الطريقة 1: البحث عن form_id في metadata (الأكثر دقة)
                            accounts_with_form = IntegrationAccount.objects.filter(
                                platform='meta',
                                status='connected',
                            ).filter(_q_json_id('selected_form_id', form_id))
                            
                            if accounts_with_form.exists():
                                # إذا وجدنا أكثر من حساب (نادر جداً)، نأخذ الأول
                                account = accounts_with_form.first()
                                logger.info(f"Found account by form_id: {form_id} -> Company: {account.company.name}")
                            
                            # الطريقة 2: إذا لم نجد، نبحث عن page_id في metadata
                            if not account:
                                accounts_with_page = IntegrationAccount.objects.filter(
                                    platform='meta',
                                    status='connected',
                                ).filter(_q_json_id('selected_page_id', page_id))
                                
                                if accounts_with_page.exists():
                                    account = accounts_with_page.first()
                                    logger.info(f"Found account by page_id: {page_id} -> Company: {account.company.name}")
                            
                            # الطريقة 3: البحث في pages array داخل metadata
                            if not account:
                                # البحث في جميع IntegrationAccounts المرتبطة بـ Meta
                                all_meta_accounts = IntegrationAccount.objects.filter(
                                    platform='meta',
                                    status='connected'
                                )
                                
                                page_id_str = str(page_id).strip()
                                for acc in all_meta_accounts:
                                    pages = acc.metadata.get('pages', [])
                                    for page in pages:
                                        pid = page.get('id')
                                        if pid is not None and str(pid).strip() == page_id_str:
                                            account = acc
                                            logger.info(f"Found account by page_id in pages array: {page_id} -> Company: {account.company.name}")
                                            break
                                    if account:
                                        break
                            
                            if not account:
                                logger.warning(
                                    f"No integration account found for form_id={form_id}, page_id={page_id}. "
                                    f"Make sure the company has selected a lead form using select_lead_form endpoint."
                                )
                                # لا يمكننا إنشاء IntegrationLog بدون account
                                # لكن يمكننا تسجيل الخطأ في Django logs
                                continue
                            
                            # جلب بيانات الليد من Meta API
                            meta_oauth = MetaOAuth()
                            
                            # الحصول على Page Access Token
                            pages = account.metadata.get('pages', [])
                            page_access_token = None
                            page_id_str = str(page_id).strip()
                            for page in pages:
                                pid = page.get('id')
                                if pid is not None and str(pid).strip() == page_id_str:
                                    page_access_token = page.get('access_token')
                                    break
                            
                            if not page_access_token:
                                # محاولة الحصول على Page Access Token من API
                                try:
                                    access_token = account.get_access_token()
                                    if not access_token:
                                        logger.error("No access token available for account")
                                        continue
                                    page_access_token = meta_oauth.get_page_access_token(
                                        page_id,
                                        access_token
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to get page access token: {str(e)}")
                                    continue
                            
                            # جلب بيانات الليد
                            try:
                                lead_data = meta_oauth.get_lead_data(leadgen_id, page_access_token)
                            except Exception as e:
                                logger.error(f"Failed to get lead data: {str(e)}")
                                continue
                            
                            # استخراج البيانات من field_data
                            field_data = lead_data.get('field_data', [])
                            lead_info = {}
                            for field in field_data:
                                lead_info[field.get('name', '').lower()] = field.get('values', [''])[0]
                            
                            # إنشاء Client جديد
                            from crm.models import Client, ClientPhoneNumber, ClientEvent
                            from crm.signals import get_least_busy_employee
                            # Enforce plan quota for leads created via webhook
                            try:
                                from subscriptions.entitlements import require_quota
                                current_clients = Client.objects.filter(company=account.company).count()
                                require_quota(
                                    account.company,
                                    "max_clients",
                                    current_count=current_clients,
                                    requested_delta=1,
                                    message="Lead limit reached for this company plan.",
                                    error_key="plan_quota_max_clients_exceeded",
                                )
                            except Exception as e:
                                logger.warning(
                                    "META_WEBHOOK: plan quota blocked lead creation company_id=%s err=%s",
                                    getattr(account.company, "id", None),
                                    str(e)[:200],
                                )
                                continue
                            
                            # استخراج البيانات
                            name = lead_info.get('full_name') or lead_info.get('name') or lead_info.get('first_name', '') + ' ' + lead_info.get('last_name', '')
                            name = name.strip() or 'Unknown'
                            phone = lead_info.get('phone_number') or lead_info.get('phone') or lead_info.get('mobile')
                            email = lead_info.get('email')
                            
                            # البحث عن كامبين مرتبط بهذا الفورم
                            campaign = None
                            form_campaign_mapping = account.metadata.get('form_campaign_mapping', {})
                            if form_id in form_campaign_mapping:
                                from crm.models import Campaign
                                try:
                                    campaign = Campaign.objects.get(
                                        id=form_campaign_mapping[form_id],
                                        company=account.company
                                    )
                                except Campaign.DoesNotExist:
                                    pass
                            
                            # إنشاء Client
                            client = Client.objects.create(
                                name=name,
                                priority='medium',
                                type='fresh',
                                company=account.company,
                                campaign=campaign,
                                source='meta_lead_form',
                                integration_account=account,
                                phone_number=phone,  # للتوافق مع الإصدارات القديمة
                            )
                            
                            # إضافة رقم الهاتف إذا كان موجوداً
                            if phone:
                                ClientPhoneNumber.objects.create(
                                    client=client,
                                    phone_number=phone,
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
                                    
                                    # تسجيل الحدث
                                    ClientEvent.objects.create(
                                        client=client,
                                        event_type='assignment',
                                        old_value='Unassigned',
                                        new_value=employee.get_full_name() or employee.username,
                                        notes=f"Auto-assigned from Meta Lead Form",
                                    )
                            
                            # تسجيل الحدث
                            ClientEvent.objects.create(
                                client=client,
                                event_type='created',
                                new_value='Meta Lead Form',
                                notes=f"Lead created from Meta Lead Form (Form ID: {form_id})",
                            )
                            
                            # تسجيل العملية في IntegrationLog
                            IntegrationLog.objects.create(
                                account=account,
                                action='lead_received',
                                status='success',
                                message=f'Lead received from Meta: {name}',
                                response_data={'leadgen_id': leadgen_id, 'form_id': form_id},
                            )
                            
                            logger.info(f"Successfully created client from Meta lead: {client.id} - {client.name}")
                            
                        except Exception as e:
                            logger.error(f"Error processing Meta webhook: {str(e)}", exc_info=True)
                            if account is not None:
                                IntegrationLog.objects.create(
                                    account=account,
                                    action='lead_received',
                                    status='error',
                                    message='Failed to process lead from Meta',
                                    error_details=str(e),
                                )
                            continue

            logger.info("META_WEBHOOK POST completed, returning 200")
            return JsonResponse({'status': 'ok'}, status=200)

        except json.JSONDecodeError:
            logger.error("META_WEBHOOK POST invalid JSON in payload")
            return HttpResponse('Bad Request', status=400)
        except Exception as e:
            logger.error("META_WEBHOOK POST error: %s", str(e), exc_info=True)
            return HttpResponse('Internal Server Error', status=500)


# --------------- Twilio SMS (نقبل Twilio فقط لخدمة SMS) ---------------

