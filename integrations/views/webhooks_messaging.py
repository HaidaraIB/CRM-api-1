import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.db.models import Q, Max
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
from ..policy import (
    INTEGRATION_POLICY_PLATFORMS,
    get_effective_integration_policy,
    get_plan_integration_access,
)
from settings.models import SystemSettings
from ..oauth_utils import get_oauth_handler, MetaOAuth
from .templates_whatsapp import (
    count_template_body_placeholders,
    meta_slug_template_name,
    whatsapp_template_body_parameter_values_for_client,
)
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


def _integration_gate(company, platform: str):
    plan_gate = get_plan_integration_access(company, platform)
    if not plan_gate["enabled"]:
        return {
            "enabled": False,
            "message": plan_gate["message"],
            "scope": "plan",
        }
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform=platform,
    )
    return effective


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def integration_policy_view(request):
    company = request.user.company
    settings_obj = SystemSettings.get_settings()
    policies = settings_obj.integration_policies or {}
    data = {}
    for platform in INTEGRATION_POLICY_PLATFORMS:
        plan_gate = get_plan_integration_access(company, platform)
        if not plan_gate["enabled"]:
            data[platform] = {
                "enabled": False,
                "message": plan_gate["message"],
                "scope": "plan",
            }
            continue
        data[platform] = get_effective_integration_policy(
            policies,
            company_id=company.id,
            platform=platform,
        )
    return success_response(data=data)


def _redact_phone_e164(phone: str) -> str:
    """Last four digits only for debug logs (E.164 digits)."""
    p = str(phone).replace(' ', '').replace('+', '').strip()
    if len(p) <= 4:
        return '****'
    return f'...{p[-4:]}'


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_send_message(request):
    """
    إرسال رسالة واتساب من رقم الشركة المتصل.
    POST /api/integrations/whatsapp/send/
    Body: { "phone_number_id": "optional", "to": "971501234567", "message": "نص الرسالة", "client_id": "optional" }
    """
    company = request.user.company
    gate = _integration_gate(company, "whatsapp")
    if not gate["enabled"]:
        return error_response(gate["message"], code="integration_disabled", status_code=403)
    # Plan gating: monthly usage only (integration access handled by integration gate).
    from subscriptions.entitlements import require_monthly_usage, increment_monthly_usage
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
    redacted_to = _redact_phone_e164(to)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.warning(
            "WhatsApp send request error: phone_number_id=%s to=%s error=%s",
            wa_account.phone_number_id,
            redacted_to,
            e,
        )
        return error_response(
            "WhatsApp API request failed.",
            code="bad_request",
            details={"error": str(e), "graph_http_status": None},
        )

    graph_status = resp.status_code
    if graph_status >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = {'error': getattr(resp, 'text', '') or str(resp)}
        if isinstance(err_body, dict):
            err_body['graph_http_status'] = graph_status
        else:
            err_body = {'error': str(err_body), 'graph_http_status': graph_status}
        logger.warning(
            "WhatsApp send failed: graph_status=%s phone_number_id=%s to=%s body=%s",
            graph_status,
            wa_account.phone_number_id,
            redacted_to,
            err_body,
        )
        return error_response(
            "WhatsApp API request failed.",
            code="bad_request",
            details=err_body if isinstance(err_body, dict) else {"error": str(err_body)},
        )

    try:
        data = resp.json()
    except ValueError:
        logger.warning(
            "WhatsApp send: invalid JSON from Graph status=%s phone_number_id=%s",
            graph_status,
            wa_account.phone_number_id,
        )
        return error_response(
            "WhatsApp API returned invalid JSON.",
            code="bad_request",
            details={"graph_http_status": graph_status},
        )

    wam_id = (data.get('messages') or [{}])[0].get('id') if isinstance(data.get('messages'), list) else None
    logger.info(
        "WhatsApp send ok: graph_status=%s phone_number_id=%s to=%s wam_id=%s",
        graph_status,
        wa_account.phone_number_id,
        redacted_to,
        wam_id,
    )

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


# WhatsApp customer service window: ~24h after the user's last inbound message (session messages).
_WHATSAPP_SESSION_HOURS = 24


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_session_window(request):
    """
    Whether the CRM considers the customer service window open for free-form WhatsApp messages.
    GET /api/integrations/whatsapp/session-window/?client_id=
    """
    company = request.user.company
    gate = _integration_gate(company, "whatsapp")
    if not gate["enabled"]:
        return error_response(gate["message"], code="integration_disabled", status_code=403)
    client_id = request.query_params.get('client_id')
    if not client_id or not str(client_id).isdigit():
        return error_response(
            'client_id query parameter is required',
            code='bad_request',
        )
    last_inbound = (
        LeadWhatsAppMessage.objects.filter(
            client_id=int(client_id),
            client__company=company,
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        ).aggregate(m=Max('created_at'))['m']
    )
    now = timezone.now()
    if not last_inbound:
        return success_response(
            data={
                'in_session': False,
                'last_inbound_at': None,
                'session_expires_at': None,
                'hours_remaining': None,
            },
        )
    expires_at = last_inbound + timedelta(hours=_WHATSAPP_SESSION_HOURS)
    in_session = now < expires_at
    hours_remaining = max(0.0, (expires_at - now).total_seconds() / 3600.0) if in_session else 0.0
    return success_response(
        data={
            'in_session': in_session,
            'last_inbound_at': last_inbound.isoformat(),
            'session_expires_at': expires_at.isoformat(),
            'hours_remaining': round(hours_remaining, 2),
        },
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_send_template(request):
    """
    Send an approved WhatsApp message template (business-initiated / outside session).
    POST /api/integrations/whatsapp/send-template/
    Body: {
      "to": "971501234567",
      "template_id": 1,
      "client_id": optional (for DB log + placeholder fill),
      "phone_number_id": optional,
      "body_parameters": optional list of strings (override auto-filled placeholders)
    }
    """
    company = request.user.company
    gate = _integration_gate(company, "whatsapp")
    if not gate["enabled"]:
        return error_response(gate["message"], code="integration_disabled", status_code=403)
    from subscriptions.entitlements import require_monthly_usage, increment_monthly_usage

    require_monthly_usage(
        company,
        "monthly_whatsapp_messages",
        requested_delta=1,
        message="You have reached your monthly WhatsApp messages limit. Please upgrade your plan.",
        error_key="plan_usage_monthly_whatsapp_exceeded",
    )
    to = request.data.get('to')
    template_id = request.data.get('template_id')
    client_id = request.data.get('client_id')
    phone_number_id = request.data.get('phone_number_id')
    if not to or template_id is None:
        return error_response(
            'to and template_id are required',
            code='bad_request',
        )
    try:
        template_id = int(template_id)
    except (TypeError, ValueError):
        return error_response('template_id must be an integer', code='bad_request')
    to = str(to).replace(' ', '').replace('+', '').strip()
    if not to.isdigit():
        return error_response('Invalid "to" phone number', code='bad_request')

    template = MessageTemplate.objects.filter(
        id=template_id,
        company=company,
    ).first()
    if not template:
        return error_response('Template not found', code='not_found', status_code=status.HTTP_404_NOT_FOUND)
    if (template.channel_type or '').lower() not in ('whatsapp', 'whatsapp_api'):
        return error_response(
            'Only WhatsApp templates can be sent via this endpoint.',
            code='bad_request',
        )
    meta_st = (template.meta_status or '').upper()
    if meta_st and meta_st != 'APPROVED':
        return error_response(
            'Template must be APPROVED in Meta before sending. Sync status in Template Management.',
            code='whatsapp_template_not_approved',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    n_placeholders = count_template_body_placeholders(template.content or '')
    body_parameters = request.data.get('body_parameters')
    if body_parameters is not None:
        if not isinstance(body_parameters, list) or not all(
            isinstance(x, (str, int, float)) or x is None for x in body_parameters
        ):
            return error_response(
                'body_parameters must be a list of strings',
                code='bad_request',
            )
        param_values = [str(x).strip() if x is not None else '' for x in body_parameters]
        param_values = [p if p else '-' for p in param_values]
        if n_placeholders > 0 and len(param_values) != n_placeholders:
            return error_response(
                f'body_parameters must have {n_placeholders} value(s) for this template.',
                code='whatsapp_template_parameter_count',
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    else:
        if n_placeholders > 0:
            if not client_id:
                return error_response(
                    'client_id is required for templates with placeholders (or pass body_parameters).',
                    code='bad_request',
                )
            try:
                client = company.clients.get(id=int(client_id))
            except Exception:
                return error_response('Client not found', code='not_found', status_code=status.HTTP_404_NOT_FOUND)
            param_values = whatsapp_template_body_parameter_values_for_client(template.content or '', client)
            if len(param_values) != n_placeholders:
                return error_response(
                    'Could not resolve template placeholders for this client.',
                    code='whatsapp_template_parameter_mismatch',
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        else:
            param_values = []

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

    language = (getattr(template, 'language', None) or 'en_US').strip() or 'en_US'
    meta_name = meta_slug_template_name(template.name, template.id)
    template_block = {
        'name': meta_name,
        'language': {'code': language},
    }
    if param_values:
        template_block['components'] = [
            {
                'type': 'body',
                'parameters': [{'type': 'text', 'text': p[:1024]} for p in param_values],
            }
        ]

    url = f"https://graph.facebook.com/v18.0/{wa_account.phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to,
        'type': 'template',
        'template': template_block,
    }
    redacted_to = _redact_phone_e164(to)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.warning(
            "WhatsApp template send request error: phone_number_id=%s to=%s error=%s",
            wa_account.phone_number_id,
            redacted_to,
            e,
        )
        return error_response(
            "WhatsApp API request failed.",
            code="bad_request",
            details={"error": str(e), "graph_http_status": None},
        )

    graph_status = resp.status_code
    if graph_status >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = {'error': getattr(resp, 'text', '') or str(resp)}
        if isinstance(err_body, dict):
            err_body['graph_http_status'] = graph_status
        else:
            err_body = {'error': str(err_body), 'graph_http_status': graph_status}
        logger.warning(
            "WhatsApp template send failed: graph_status=%s phone_number_id=%s to=%s body=%s",
            graph_status,
            wa_account.phone_number_id,
            redacted_to,
            err_body,
        )
        return error_response(
            "WhatsApp API request failed.",
            code="bad_request",
            details=err_body if isinstance(err_body, dict) else {"error": str(err_body)},
        )

    try:
        data = resp.json()
    except ValueError:
        return error_response(
            "WhatsApp API returned invalid JSON.",
            code="bad_request",
            details={"graph_http_status": graph_status},
        )

    wam_id = (data.get('messages') or [{}])[0].get('id') if isinstance(data.get('messages'), list) else None
    logger.info(
        "WhatsApp template send ok: graph_status=%s phone_number_id=%s to=%s wam_id=%s template=%s",
        graph_status,
        wa_account.phone_number_id,
        redacted_to,
        wam_id,
        meta_name,
    )

    increment_monthly_usage(company, "monthly_whatsapp_messages", requested_delta=1)
    if wa_account.integration_account_id:
        IntegrationLog.objects.create(
            account_id=wa_account.integration_account_id,
            action='whatsapp_template_sent',
            status='success',
            message=f'Template {meta_name} sent to {to}',
            response_data=data,
        )

    preview = (template.content or '')[:500]
    log_body = f'[Template: {meta_name}] {preview}'
    if client_id:
        try:
            client = company.clients.get(id=int(client_id))
            LeadWhatsAppMessage.objects.create(
                client=client,
                phone_number=to,
                body=log_body[:65535],
                direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
                whatsapp_message_id=wam_id,
                created_by=request.user,
            )
        except Exception:
            pass

    return success_response(data=data)


# ==================== TikTok Lead Gen Webhook ====================
# استقبال ليدز من TikTok Instant Form (TikTok Marketing API / Leads Center).
# يُسجّل في TikTok Ads Manager → Leads Center → CRM integration → Custom API with Webhooks.


def _parse_tiktok_leadgen_payload(body):
    """
    استخراج وتطبيع lead payload من TikTok Lead Gen.
    الناتج القياسي:
    {
      source: "tiktok",
      external_id: lead_id,
      name: str | None,
      phone: str | None,
      email: str | None,
      custom_fields: object,
      company_id: str | None,
      form_id: str | None,
      advertiser_id: str | None,
      raw: object
    }
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
    lead_id = data.get('lead_id') or data.get('id') or data.get('leadId') or data.get('external_id')
    form_id = data.get('form_id') or data.get('formId')
    advertiser_id = str(data.get('advertiser_id') or data.get('advertiserId') or '')
    # TikTok fields can be inconsistent; normalize by mapper.
    name = None
    phone = None
    email = None
    custom_fields = {}

    def _normalize_key(k):
        return str(k or '').strip().lower().replace('-', '_').replace(' ', '_')

    def _extract_val(v):
        if isinstance(v, list):
            return str(v[0]).strip() if v else ''
        return str(v).strip() if v is not None else ''

    lead_list = data.get('list') or data.get('answers') or data.get('field_data') or []
    if isinstance(lead_list, list):
        key_map = {}
        for item in lead_list:
            if isinstance(item, dict):
                raw_key = item.get('key') or item.get('name') or item.get('label') or ''
                nk = _normalize_key(raw_key)
                val = _extract_val(item.get('value') if 'value' in item else item.get('values'))
                if nk and val:
                    key_map[nk] = val
        name = key_map.get('full_name') or key_map.get('name')
        phone = key_map.get('phone_number') or key_map.get('phone')
        email = key_map.get('email')
        custom_fields.update(key_map)
    if isinstance(data.get('answers'), dict):
        ans = data['answers']
        normalized_ans = {_normalize_key(k): _extract_val(v) for k, v in ans.items()}
        name = name or normalized_ans.get('full_name') or normalized_ans.get('name')
        phone = phone or normalized_ans.get('phone_number') or normalized_ans.get('phone')
        email = email or normalized_ans.get('email')
        custom_fields.update({k: v for k, v in normalized_ans.items() if v})
    flat_map = {_normalize_key(k): _extract_val(v) for k, v in data.items()}
    name = name or flat_map.get('full_name') or flat_map.get('name')
    phone = phone or flat_map.get('phone_number') or flat_map.get('phone')
    email = email or flat_map.get('email')
    # Keep unknown fields for troubleshooting and future mapping.
    known_top = {
        'lead_id', 'leadid', 'id', 'external_id',
        'form_id', 'formid',
        'advertiser_id', 'advertiserid',
        'list', 'answers', 'field_data', 'content', 'data',
        'full_name', 'name', 'phone_number', 'phone', 'email'
    }
    for k, v in flat_map.items():
        if k not in known_top and v:
            custom_fields[k] = v
    if not lead_id and not name and not phone and not email:
        return None
    return {
        'source': 'tiktok',
        'external_id': str(lead_id) if lead_id else None,
        'lead_id': str(lead_id) if lead_id else None,
        'form_id': str(form_id) if form_id else None,
        'advertiser_id': advertiser_id or None,
        'name': (name or '').strip() or None,
        'phone': (phone or '').strip() or None,
        'email': (email or '').strip() or None,
        'custom_fields': custom_fields,
        'company_id': str(data.get('company_id') or '') or None,
        'raw': data,
    }


def _get_company_id_for_tiktok_leadgen(advertiser_id, request):
    """
    تحديد company_id بطريقة آمنة:
    1) advertiser mapping
    2) default company id
    3) query param فقط إذا تم تفعيله صراحة عبر الإعدادات
    """
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
    allow_query = str(
        getattr(settings, 'TIKTOK_LEADGEN_ALLOW_COMPANY_QUERY_PARAM', 'true')
    ).lower() in {'1', 'true', 'yes', 'on'}
    if allow_query:
        company_id = request.GET.get('company_id')
        if company_id:
            try:
                cid = int(company_id)
                query_sig = str(request.GET.get('sig') or '').strip()
                require_sig = str(
                    getattr(settings, 'TIKTOK_LEADGEN_REQUIRE_SIGNED_COMPANY_QUERY', 'false')
                ).lower() in {'1', 'true', 'yes', 'on'}
                expected_sig = _build_tiktok_company_sig(cid)
                if query_sig:
                    if expected_sig and hmac.compare_digest(query_sig, expected_sig):
                        return cid
                    logger.warning("TikTok Lead Gen webhook: invalid company query signature")
                    return None
                if require_sig:
                    logger.warning("TikTok Lead Gen webhook: missing required company query signature")
                    return None
                return cid
            except (ValueError, TypeError):
                pass
    return None


def _build_tiktok_company_sig(company_id: int) -> str:
    """
    HMAC signature for company_id included in webhook URL query.
    Works with manual CRM integration in TikTok Ads Manager.
    """
    raw_secret = (
        getattr(settings, 'TIKTOK_LEADGEN_URL_SIGNING_SECRET', '')
        or getattr(settings, 'TIKTOK_LEADGEN_WEBHOOK_SECRET', '')
        or getattr(settings, 'SECRET_KEY', '')
    )
    secret = str(raw_secret or '').strip()
    if not secret:
        return ''
    msg = str(company_id).encode('utf-8')
    return hmac.new(secret.encode('utf-8'), msg, hashlib.sha256).hexdigest()


def _extract_tiktok_signature(request):
    """
    Try common signature header names used by webhook providers / proxies.
    Returns raw header value, possibly prefixed with 'sha256='.
    """
    candidates = [
        'X-Tt-Signature',
        'X-Tiktok-Signature',
        'X-TikTok-Signature',
        'X-Signature',
    ]
    for header in candidates:
        v = request.headers.get(header)
        if v:
            return str(v).strip()
    return ''


def _is_tiktok_signature_valid(request):
    """
    Optional HMAC verification for TikTok webhook payload.
    If secret is unset => verification is skipped (backward-compatible).
    """
    secret = getattr(settings, 'TIKTOK_LEADGEN_WEBHOOK_SECRET', '') or ''
    if not secret:
        return True
    incoming = _extract_tiktok_signature(request)
    if not incoming:
        return False
    if incoming.startswith('sha256='):
        incoming = incoming[7:]
    expected = hmac.new(
        secret.encode('utf-8'),
        request.body or b'',
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(incoming, expected)


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
    if not _is_tiktok_signature_valid(request):
        logger.warning("TikTok Lead Gen webhook: invalid or missing signature")
        return HttpResponse('Invalid signature', status=403)
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
    if not _integration_gate(company, "tiktok")["enabled"]:
        logger.info("TikTok Lead Gen webhook ignored (integration disabled) company_id=%s", company_id)
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
    # Successful webhook reception: keep integration healthy and store last receive timestamp.
    metadata = account.metadata if isinstance(account.metadata, dict) else {}
    metadata['last_received_at'] = timezone.now().isoformat()
    account.metadata = metadata
    account.status = 'connected'
    account.error_message = None
    account.last_sync_at = timezone.now()
    account.save(update_fields=['metadata', 'status', 'error_message', 'last_sync_at'])
    lead_id = payload.get('lead_id')
    payload_fingerprint = hashlib.sha256(
        json.dumps(
            {
                'advertiser_id': payload.get('advertiser_id'),
                'form_id': payload.get('form_id'),
                'name': payload.get('name'),
                'phone': payload.get('phone'),
                'email': payload.get('email'),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode('utf-8')
    ).hexdigest()
    if lead_id:
        if IntegrationLog.objects.filter(
            account=account,
            action='tiktok_lead_received',
            response_data__lead_id=lead_id,
        ).exists():
            logger.info("TikTok Lead Gen: duplicate lead_id=%s ignored", lead_id)
            return HttpResponse('OK', status=200)
    else:
        if IntegrationLog.objects.filter(
            account=account,
            action='tiktok_lead_received',
            response_data__payload_fingerprint=payload_fingerprint,
        ).exists():
            logger.info("TikTok Lead Gen: duplicate payload fingerprint ignored")
            return HttpResponse('OK', status=200)
    # Enforce plan quota for NEW leads only (after idempotency check).
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
    try:
        client = Client.objects.create(
            name=payload.get('name') or 'TikTok Lead',
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
            message='Lead created: %s' % (payload.get('name') or 'TikTok Lead'),
            response_data={
                'normalized_lead': {
                    'source': payload.get('source'),
                    'external_id': payload.get('external_id'),
                    'name': payload.get('name'),
                    'phone': payload.get('phone'),
                    'email': payload.get('email'),
                    'custom_fields': payload.get('custom_fields') or {},
                    'company_id': str(company_id),
                },
                'lead_id': lead_id,
                'form_id': payload.get('form_id'),
                'advertiser_id': payload.get('advertiser_id'),
                'client_id': client.id,
                'payload_fingerprint': payload_fingerprint,
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
            response_data={
                'lead_id': lead_id,
                'payload_fingerprint': payload_fingerprint,
                'payload_keys': list(payload.keys()),
            },
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
                            gate = _integration_gate(account.company, "meta")
                            if not gate["enabled"]:
                                logger.info(
                                    "META_WEBHOOK ignored (integration disabled) company_id=%s form_id=%s",
                                    account.company_id,
                                    form_id,
                                )
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

