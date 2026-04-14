import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
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
from settings.models import SystemSettings
from ..policy import get_effective_integration_policy, get_plan_integration_access

logger = logging.getLogger(__name__)


def _integration_gate(company, platform: str):
    plan_gate = get_plan_integration_access(company, platform)
    if not plan_gate["enabled"]:
        return error_response(plan_gate["message"], code="plan_integration_not_included", status_code=403)
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform=platform,
    )
    if not effective["enabled"]:
        return error_response(effective["message"], code="integration_disabled", status_code=403)
    return None
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_conversations_list(request):
    """
    قائمة العملاء الذين لديهم محادثات واتساب (لشريط المحادثات في مركز المراسلات).
    GET /api/integrations/whatsapp/conversations/
    """
    from django.db.models import Max
    from crm.models import Client
    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked
    # عملاء لديهم على الأقل رسالة واتساب، مرتبون بآخر رسالة
    sub = LeadWhatsAppMessage.objects.filter(client__company=company).values('client_id').annotate(
        last_at=Max('created_at')
    ).order_by('-last_at')
    client_ids = [s['client_id'] for s in sub[:100]]
    order = {cid: i for i, cid in enumerate(client_ids)}
    clients = list(Client.objects.filter(id__in=client_ids).select_related('company'))
    clients.sort(key=lambda c: order.get(c.id, 999))
    return success_response(
        data=[
            {
                'id': c.id,
                'name': c.name,
                'phone_number': c.phone_number or '',
                'company_name': getattr(c, 'company_name', None) or c.name,
            }
            for c in clients
        ],
    )


def _content_to_meta_body(content):
    """Convert our placeholders [Customer Name], [Company], etc. to Meta {{1}}, {{2}}, ...

    Meta requires positional examples as one row: body_text: [["ex1", "ex2", ...]] in the same
    order as {{1}}, {{2}}, ... (see POST /{waba-id}/message_templates positional parameters).
    """
    if not content:
        return '', []
    patterns = [
        (r'\[\s*Customer Name\s*\]|\[\s*اسم_العميل\s*\]|\[\s*اسم العميل\s*\]', '{{1}}', 'Customer'),
        (r'\[\s*Company\s*\]|\[\s*الشركة\s*\]|\[\s*شركة\s*\]', '{{2}}', 'Company'),
        (r'\[\s*Amount\s*\]|\[\s*المبلغ\s*\]', '{{3}}', '100'),
        (r'\[\s*Invoice Number\s*\]|\[\s*رقم_الفاتورة\s*\]|\[\s*رقم الفاتورة\s*\]', '{{4}}', 'INV-001'),
    ]
    text = content
    ordered_examples = []
    for pattern, repl, sample in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
            ordered_examples.append(sample)
    return text, ordered_examples


def meta_slug_template_name(name: str, template_id=None) -> str:
    """Same slug as submit-to-whatsapp; must match when sending template messages."""
    meta_name = re.sub(r'[^a-z0-9_]', '_', (name or '').lower())[:512]
    if meta_name:
        return meta_name
    return f'template_{template_id}' if template_id is not None else 'template'


def whatsapp_template_body_parameter_values_for_client(content: str, client) -> list:
    """
    Body parameter strings for Cloud API template send, same order as _content_to_meta_body / submit.
    client: crm.Client (or compatible with .name, .lead_company_name, .budget).
    """
    if not content:
        return []
    patterns = [
        (
            r'\[\s*Customer Name\s*\]|\[\s*اسم_العميل\s*\]|\[\s*اسم العميل\s*\]',
            lambda c: (getattr(c, 'name', None) or '').strip(),
        ),
        (
            r'\[\s*Company\s*\]|\[\s*الشركة\s*\]|\[\s*شركة\s*\]',
            lambda c: (getattr(c, 'lead_company_name', None) or '').strip(),
        ),
        (
            r'\[\s*Amount\s*\]|\[\s*المبلغ\s*\]',
            lambda c: ''
            if getattr(c, 'budget', None) is None
            else str(c.budget).strip(),
        ),
        (
            r'\[\s*Invoice Number\s*\]|\[\s*رقم_الفاتورة\s*\]|\[\s*رقم الفاتورة\s*\]',
            lambda c: (getattr(c, 'invoice_number', None) or '').strip(),
        ),
    ]
    values = []
    for pattern, getter in patterns:
        if re.search(pattern, content, re.IGNORECASE):
            raw = getter(client) or ''
            s = str(raw).strip()
            if not s:
                s = '-'
            values.append(s[:1024])
    return values


def count_template_body_placeholders(content: str) -> int:
    _, samples = _content_to_meta_body(content or '')
    return len(samples)


class MessageTemplateViewSet(viewsets.ModelViewSet):
    """
    قوالب الرسائل لمركز المراسلات (واتساب و SMS).
    CRUD: GET/POST /api/integrations/templates/ , GET/PUT/PATCH/DELETE /api/integrations/templates/:id/
    """
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    serializer_class = MessageTemplateSerializer

    def get_queryset(self):
        blocked = _integration_gate(self.request.user.company, "whatsapp")
        if blocked is not None:
            return MessageTemplate.objects.none()
        return MessageTemplate.objects.filter(company=self.request.user.company).order_by('-updated_at')

    def perform_create(self, serializer):
        blocked = _integration_gate(self.request.user.company, "whatsapp")
        if blocked is not None:
            raise PermissionDenied(detail={"error": "Integration is not available for your current plan.", "error_key": "plan_integration_not_included"})
        serializer.save(company=self.request.user.company)

    @action(detail=True, methods=['post'], url_path='submit-to-whatsapp')
    def submit_to_whatsapp(self, request, pk=None):
        """
        إرسال قالب واتساب إلى Meta للمراجعة حتى يظهر في حساب واتساب.
        POST /api/integrations/templates/:id/submit-to-whatsapp/
        Body (optional): { "language": "en_US" }
        """
        template = self.get_object()
        blocked = _integration_gate(request.user.company, "whatsapp")
        if blocked is not None:
            return blocked
        if (template.channel_type or '').lower() not in ('whatsapp', 'whatsapp_api'):
            return error_response(
                'Only WhatsApp templates can be submitted to Meta.',
                code='bad_request',
            )
        company = request.user.company
        wa = WhatsAppAccount.objects.filter(company=company, status='connected').first()
        if not wa:
            return error_response(
                'No connected WhatsApp account for this company.',
                code='no_connected_whatsapp_account',
                status_code=status.HTTP_404_NOT_FOUND,
            )
        token = wa.get_access_token()
        if not token:
            return error_response(
                'WhatsApp account has no access token.',
                code='whatsapp_no_access_token',
            )
        meta_name = meta_slug_template_name(template.name, template.id)
        language = (getattr(template, 'language', None) or request.data.get('language') or 'en_US').strip() or 'en_US'
        category_map = {
            'auth': 'AUTHENTICATION',
            'marketing': 'MARKETING',
            'utility': 'UTILITY',
        }
        category = category_map.get((template.category or '').lower(), 'UTILITY')
        body_text, example_values = _content_to_meta_body(template.content or '')
        if not body_text or not body_text.strip():
            return error_response(
                'Template content is empty.',
                code='template_content_empty',
            )
        components = []
        # HEADER (optional): TEXT only for simplicity; media requires upload
        header_type = (getattr(template, 'header_type', None) or '').strip().lower()
        header_text = (getattr(template, 'header_text', None) or '').strip()
        if header_type == 'text' and header_text:
            components.append({'type': 'HEADER', 'format': 'TEXT', 'text': header_text[:60]})
        # BODY
        body_comp = {'type': 'BODY', 'text': body_text[:1024]}
        if example_values:
            # One sample row: all positional {{n}} values in order (not one array per variable).
            body_comp['example'] = {'body_text': [example_values]}
        components.append(body_comp)
        # FOOTER (optional)
        footer = (getattr(template, 'footer', None) or '').strip()
        if footer:
            components.append({'type': 'FOOTER', 'text': footer[:60]})
        # BUTTONS (optional): phone -> CALL, url -> URL, reply -> QUICK_REPLY
        buttons_data = getattr(template, 'buttons', None) or []
        if isinstance(buttons_data, list) and buttons_data:
            meta_buttons = []
            for b in buttons_data[:10]:  # Meta allows up to 10 buttons
                if not isinstance(b, dict):
                    continue
                btn_type = (b.get('type') or '').lower()
                text = (b.get('buttonText') or b.get('button_text') or '')[:25].strip() or 'Button'
                if btn_type == 'phone':
                    phone = (b.get('phone') or '').strip() or '+1234567890'
                    meta_buttons.append({'type': 'CALL', 'text': text, 'phone_number': phone[:20]})
                elif btn_type == 'url':
                    url = (b.get('url') or '').strip() or 'https://example.com'
                    meta_buttons.append({'type': 'URL', 'text': text, 'url': url[:2000]})
                elif btn_type == 'reply':
                    meta_buttons.append({'type': 'QUICK_REPLY', 'text': text})
            if meta_buttons:
                components.append({'type': 'BUTTONS', 'buttons': meta_buttons})
        payload = {
            'name': meta_name,
            'language': language,
            'category': category,
            'components': components,
        }
        if example_values:
            payload['parameter_format'] = 'positional'
        url = f'https://graph.facebook.com/v18.0/{wa.waba_id}/message_templates'
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            data = resp.json() if resp.content else {}
            if resp.status_code not in (200, 201):
                return error_response(
                    'Meta API rejected the template submission.',
                    code='bad_request',
                    details=data if isinstance(data, dict) else {'raw': resp.text},
                )
            meta_id = (data.get('id') or '').strip() or None
            meta_status = (data.get('status') or 'PENDING').upper()
            template.meta_template_id = meta_id
            template.meta_status = meta_status
            template.save(update_fields=['meta_template_id', 'meta_status'])
            return success_response(
                data={
                    'meta_template_id': meta_id,
                    'status': meta_status,
                    'message': 'Template submitted to WhatsApp for review.',
                },
            )
        except requests.RequestException as e:
            return error_response(
                str(e),
                code='bad_gateway',
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

    @action(detail=False, methods=['post'], url_path='sync-whatsapp')
    def sync_whatsapp(self, request):
        """
        مزامنة حالات قوالب واتساب من Meta (PENDING, APPROVED, REJECTED, ...).
        POST /api/integrations/templates/sync-whatsapp/
        """
        company = request.user.company
        blocked = _integration_gate(company, "whatsapp")
        if blocked is not None:
            return blocked
        wa = WhatsAppAccount.objects.filter(company=company, status='connected').first()
        if not wa:
            return error_response(
                'No connected WhatsApp account for this company.',
                code='no_connected_whatsapp_account',
                status_code=status.HTTP_404_NOT_FOUND,
            )
        token = wa.get_access_token()
        if not token:
            return error_response(
                'WhatsApp account has no access token.',
                code='whatsapp_no_access_token',
            )
        url = f'https://graph.facebook.com/v18.0/{wa.waba_id}/message_templates?fields=id,name,status'
        headers = {'Authorization': f'Bearer {token}'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            data = resp.json() if resp.content else {}
            if resp.status_code != 200:
                err_payload = data.get('error', data) if isinstance(data, dict) else {'error': resp.text}
                return error_response(
                    'Failed to fetch templates from Meta.',
                    code='bad_request',
                    details=err_payload if isinstance(err_payload, dict) else {'error': str(err_payload)},
                )
            meta_list = data.get('data') or []
            meta_by_id = {str(t.get('id')).strip(): (t.get('status') or 'PENDING').upper() for t in meta_list if t.get('id')}
            updated = 0
            for tpl in MessageTemplate.objects.filter(company=company, meta_template_id__isnull=False).exclude(meta_template_id=''):
                mid = str(tpl.meta_template_id).strip()
                if mid in meta_by_id:
                    new_status = meta_by_id[mid]
                    if (tpl.meta_status or '') != new_status:
                        tpl.meta_status = new_status
                        tpl.save(update_fields=['meta_status'])
                        updated += 1
            return success_response(
                data={
                    'message': 'Templates synced.',
                    'updated': updated,
                    'total_meta': len(meta_list),
                },
            )
        except requests.RequestException as e:
            return error_response(
                str(e),
                code='bad_gateway',
                status_code=status.HTTP_502_BAD_GATEWAY,
            )


# ==================== WhatsApp Messaging Limits (Tier) ====================
# حد الرسائل الجماعية (٢٥٠ يومياً ثم يزيد حسب الجودة إلى ١٠٠٠ وغيرها)

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_limits(request):
    """
    جلب حد الرسائل (التير) وجودة الحساب من Meta.
    GET /api/integrations/whatsapp/limits/
    Returns: { messaging_limit_tier, quality_rating, ... }
    """
    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked
    wa = WhatsAppAccount.objects.filter(company=company, status='connected').first()
    if not wa:
        return error_response(
            'No connected WhatsApp account for this company.',
            code='no_connected_whatsapp_account',
            status_code=status.HTTP_404_NOT_FOUND,
        )
    token = wa.get_access_token()
    if not token:
        return error_response(
            'WhatsApp account has no access token.',
            code='whatsapp_no_access_token',
        )
    url = f'https://graph.facebook.com/v18.0/{wa.phone_number_id}?fields=messaging_limit_tier,quality_rating'
    headers = {'Authorization': f'Bearer {token}'}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err_payload = data.get('error', data) if isinstance(data, dict) else {'error': resp.text}
            return error_response(
                'Failed to fetch WhatsApp limits from Meta.',
                code='bad_request',
                details=err_payload if isinstance(err_payload, dict) else {'error': str(err_payload)},
            )
        return success_response(data=data)
    except requests.RequestException as e:
        return error_response(
            str(e),
            code='bad_gateway',
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

