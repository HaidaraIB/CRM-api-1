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
from ..oauth_utils import get_oauth_handler, MetaOAuth, META_GRAPH_API_BASE_URL
from ..whatsapp_account_sync import resolve_whatsapp_account_for_api
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


def _whatsapp_thread_messages_qs(company, client_id=None, phone=None):
    """Messages for one chat thread (by client id and/or phone)."""
    from django.db.models import Q
    from integrations.services.phone_match import find_client_by_phone, phone_match_keys

    qs = LeadWhatsAppMessage.objects.filter(client__company=company)
    client_ids: set[int] = set()
    phone_q = None

    if client_id and str(client_id).isdigit():
        client_ids.add(int(client_id))

    if phone:
        client = find_client_by_phone(company, phone)
        if client:
            client_ids.add(client.id)
        keys = phone_match_keys(phone)
        phone_q = Q()
        for k in keys:
            if len(k) >= 7:
                phone_q |= Q(phone_number=k) | Q(phone_number__endswith=k[-10:])

    if client_ids and phone_q is not None:
        qs = qs.filter(Q(client_id__in=client_ids) | phone_q)
    elif client_ids:
        qs = qs.filter(client_id__in=client_ids)
    elif phone_q is not None:
        qs = qs.filter(phone_q)
    else:
        qs = qs.none()
    return qs


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_conversations_list(request):
    """
    قائمة العملاء الذين لديهم محادثات واتساب (لشريط المحادثات في مركز المراسلات).
    GET /api/integrations/whatsapp/conversations/
    DELETE /api/integrations/whatsapp/conversations/?client=:id | ?phone=:digits
    """
    from django.db.models import Max
    from crm.models import Client
    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked

    if request.method == 'DELETE':
        client_id = request.query_params.get('client')
        phone = (request.query_params.get('phone') or '').strip()
        if not (client_id and str(client_id).isdigit()) and not phone:
            return error_response('client or phone query parameter is required', code='bad_request')
        qs = _whatsapp_thread_messages_qs(company, client_id=client_id, phone=phone or None)
        deleted_count, _ = qs.delete()
        return success_response(data={'deleted': deleted_count})

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


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_contact_by_phone(request):
    """
    Resolve a CRM client by phone for WhatsApp chat (manual number → lead link).
    GET /api/integrations/whatsapp/contact-by-phone/?phone=...
    """
    from integrations.services.phone_match import find_client_by_phone
    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked
    phone = (request.query_params.get('phone') or '').strip()
    if not phone:
        return error_response('phone is required', code='bad_request')
    client = find_client_by_phone(company, phone)
    if not client:
        return success_response(data=None)
    return success_response(
        data={
            'id': client.id,
            'name': client.name,
            'phone_number': client.phone_number or '',
            'company_name': getattr(client, 'lead_company_name', None) or client.name,
        },
    )


# Placeholder definitions: regex, Meta sample value, client value getter


def _tenant_company_name(client) -> str:
    """CRM tenant (your business) name — used for [Company] in outbound WhatsApp."""
    company = getattr(client, 'company', None)
    if company is not None:
        return (getattr(company, 'name', None) or '').strip()
    return ''


def _client_customer_name(client) -> str:
    name = (getattr(client, 'name', None) or '').strip()
    if not name:
        return ''
    if name.lower().startswith('whatsapp:'):
        return name.split(':', 1)[-1].strip()
    return name


def _client_lead_company_name(client) -> str:
    return (getattr(client, 'lead_company_name', None) or '').strip()


def _company_placeholder_value(client) -> str:
    return _tenant_company_name(client) or _client_lead_company_name(client)


def _format_template_parameter_value(client, getter, sample: str) -> str:
    raw = getter(client) if getter else ''
    s = str(raw or '').strip()
    if not s:
        if sample == 'Company':
            s = _company_placeholder_value(client)
        elif sample == 'Customer':
            s = _client_customer_name(client)
    if not s:
        s = '-'
    return s[:1024]


def _positional_parameter_values_for_client(content: str, client) -> list:
    """Fill {{1}}..{{n}} for Meta-imported bodies that have no [Bracket] markers."""
    n = _positional_variable_count(content)
    if n <= 0:
        return []
    pool = [
        _tenant_company_name(client),
        _client_customer_name(client),
        '' if getattr(client, 'budget', None) is None else str(client.budget).strip(),
        (getattr(client, 'invoice_number', None) or '').strip(),
    ]
    values = []
    for i in range(n):
        v = pool[i] if i < len(pool) else ''
        if not v and i == 0:
            v = _company_placeholder_value(client) or _client_customer_name(client)
        elif not v:
            v = _client_customer_name(client) or _tenant_company_name(client)
        values.append((v or '-')[:1024])
    return values


_PLACEHOLDER_DEFS = [
    (
        r'\[\s*Customer Name\s*\]|\[\s*اسم_العميل\s*\]|\[\s*اسم العميل\s*\]',
        'Customer',
        _client_customer_name,
    ),
    (
        r'\[\s*Company\s*\]|\[\s*الشركة\s*\]|\[\s*شركة\s*\]',
        'Company',
        _company_placeholder_value,
    ),
    (
        r'\[\s*Amount\s*\]|\[\s*المبلغ\s*\]',
        '100',
        lambda c: '' if getattr(c, 'budget', None) is None else str(c.budget).strip(),
    ),
    (
        r'\[\s*Invoice Number\s*\]|\[\s*رقم_الفاتورة\s*\]|\[\s*رقم الفاتورة\s*\]',
        'INV-001',
        lambda c: (getattr(c, 'invoice_number', None) or '').strip(),
    ),
]


def _find_placeholders_in_order(content: str):
    """Bracket placeholders in left-to-right order (Meta requires {{1}}, {{2}}, ... by appearance)."""
    matches = []
    for pattern, sample, getter in _PLACEHOLDER_DEFS:
        for m in re.finditer(pattern, content or '', re.IGNORECASE):
            matches.append((m.start(), m.end(), sample, getter))
    matches.sort(key=lambda x: x[0])
    return matches


def _positional_variable_count(text: str) -> int:
    """Number of {{n}} placeholders in text (must match example row length)."""
    return len(re.findall(r'\{\{\s*\d+\s*\}\}', text or ''))


def _default_example_values(count: int, existing=None):
    """Pad sample values for Meta body_text examples (one row, positional order)."""
    samples = list(existing or [])
    fillers = ['Customer', 'Company', '100', 'INV-001', 'Sample']
    while len(samples) < count:
        samples.append(fillers[len(samples) % len(fillers)])
    return samples[:count]


def _attach_body_example(body_comp: dict, body_text: str, example_values: list) -> bool:
    """Attach Meta BODY example when the text contains {{1}}, {{2}}, ... Returns True if positional."""
    var_count = _positional_variable_count(body_text)
    if var_count <= 0:
        return False
    samples = _default_example_values(var_count, example_values)
    body_comp['example'] = {'body_text': [samples]}
    return True


def _content_to_meta_body(content):
    """Convert [Customer Name], [Company], ... to Meta {{1}}, {{2}}, ... in appearance order.

    Meta requires variables numbered sequentially from {{1}} with no gaps. Using fixed {{2}} for
    Company when Customer Name is absent caused rejections (e.g. only [Company] in the body).
    """
    if not content:
        return '', []
    matches = _find_placeholders_in_order(content)
    if not matches:
        return content, []
    parts = []
    last = 0
    ordered_examples = []
    for i, (start, end, sample, _getter) in enumerate(matches):
        parts.append(content[last:start])
        parts.append(f'{{{{{i + 1}}}}}')
        ordered_examples.append(sample)
        last = end
    parts.append(content[last:])
    return ''.join(parts), ordered_examples


def meta_slug_template_name(name: str, template_id=None) -> str:
    """Same slug as submit-to-whatsapp; must match when sending template messages."""
    meta_name = re.sub(r'[^a-z0-9_]', '_', (name or '').lower())[:512]
    if meta_name:
        return meta_name
    return f'template_{template_id}' if template_id is not None else 'template'


def whatsapp_template_body_parameter_values_for_client(content: str, client) -> list:
    """
    Body parameter strings for Cloud API template send, same order as _content_to_meta_body / submit.
    client: crm.Client (or compatible with .name, .lead_company_name, .budget, .company).
    """
    if not content:
        return []
    matches = _find_placeholders_in_order(content)
    if matches:
        return [
            _format_template_parameter_value(client, getter, sample)
            for _start, _end, sample, getter in matches
        ]
    return _positional_parameter_values_for_client(content, client)


def count_template_body_placeholders(content: str) -> int:
    _, samples = _content_to_meta_body(content or '')
    if samples:
        return len(samples)
    return _positional_variable_count(content or '')


def _meta_category_to_crm(category: str) -> str:
    cat = (category or '').upper()
    if cat == 'AUTHENTICATION':
        return MessageTemplate.CATEGORY_AUTH
    if cat == 'MARKETING':
        return MessageTemplate.CATEGORY_MARKETING
    return MessageTemplate.CATEGORY_UTILITY


def _meta_status_normalize(status: str) -> str:
    return (status or 'PENDING').upper()


def _meta_button_to_crm(btn: dict):
    btype = (btn.get('type') or '').upper()
    text = (btn.get('text') or 'Button')[:25]
    if btype == 'QUICK_REPLY':
        return {'type': 'reply', 'button_text': text}
    if btype == 'URL':
        return {'type': 'url', 'button_text': text, 'url': (btn.get('url') or '')[:2000]}
    if btype in ('PHONE_NUMBER', 'CALL'):
        return {'type': 'phone', 'button_text': text, 'phone': (btn.get('phone_number') or '')[:20]}
    return None


def _parse_meta_template_components(components):
    """Extract CRM fields from Meta message template components."""
    body = ''
    header_type = 'none'
    header_text = ''
    footer = ''
    buttons = []
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        ctype = (comp.get('type') or '').upper()
        if ctype == 'BODY':
            body = comp.get('text') or ''
        elif ctype == 'HEADER':
            fmt = (comp.get('format') or 'TEXT').upper()
            if fmt == 'TEXT':
                header_type = 'text'
                header_text = comp.get('text') or ''
            elif fmt == 'IMAGE':
                header_type = 'image'
            elif fmt == 'VIDEO':
                header_type = 'video'
            elif fmt == 'DOCUMENT':
                header_type = 'document'
            elif fmt == 'LOCATION':
                header_type = 'location'
        elif ctype == 'FOOTER':
            footer = comp.get('text') or ''
        elif ctype == 'BUTTONS':
            for btn in comp.get('buttons') or []:
                mapped = _meta_button_to_crm(btn) if isinstance(btn, dict) else None
                if mapped:
                    buttons.append(mapped)
    return body, header_type, header_text, footer, buttons


def _fetch_all_meta_message_templates(waba_id: str, token: str):
    """List all WhatsApp message templates from Meta (handles paging). Returns (items, error_payload)."""
    fields = 'id,name,status,language,category,components'
    url = f'{META_GRAPH_API_BASE_URL}/{waba_id}/message_templates?fields={fields}&limit=100'
    headers = {'Authorization': f'Bearer {token}'}
    all_items = []
    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err_payload = data.get('error', data) if isinstance(data, dict) else {'error': resp.text}
            return None, err_payload if isinstance(err_payload, dict) else {'error': str(err_payload)}
        all_items.extend(data.get('data') or [])
        url = (data.get('paging') or {}).get('next')
    return all_items, None


def _connected_wa_or_response(company):
    """Return (WhatsAppAccount, None) or (None, error Response)."""
    wa, err = resolve_whatsapp_account_for_api(company)
    if wa:
        return wa, None
    if err == 'whatsapp_phone_numbers_not_synced':
        return None, error_response(
            'WhatsApp is connected but your phone number could not be loaded from Meta. '
            'Disconnect and reconnect the account, or check Meta permissions.',
            code=err,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return None, error_response(
        'No connected WhatsApp account for this company.',
        code='no_connected_whatsapp_account',
        status_code=status.HTTP_404_NOT_FOUND,
    )


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
        wa, err_resp = _connected_wa_or_response(company)
        if err_resp is not None:
            return err_resp
        token = wa.get_access_token()
        if not token:
            return error_response(
                'WhatsApp account has no access token.',
                code='whatsapp_no_access_token',
            )
        existing_status = (template.meta_status or '').upper()
        if existing_status in ('PENDING', 'APPROVED'):
            return error_response(
                'This template is already submitted to WhatsApp and is awaiting review or approved.',
                code='template_already_submitted',
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
            header_comp = {'type': 'HEADER', 'format': 'TEXT', 'text': header_text[:60]}
            if _positional_variable_count(header_text) > 0:
                header_comp['example'] = {'header_text': ['Sample']}
            components.append(header_comp)
        # BODY
        body_comp = {'type': 'BODY', 'text': body_text[:1024]}
        has_positional = _attach_body_example(body_comp, body_text, example_values)
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
        if has_positional:
            payload['parameter_format'] = 'positional'
        url = f'{META_GRAPH_API_BASE_URL}/{wa.waba_id}/message_templates'
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            data = resp.json() if resp.content else {}
            if resp.status_code not in (200, 201):
                meta_msg = None
                if isinstance(data, dict):
                    err = data.get('error')
                    if isinstance(err, dict):
                        meta_msg = err.get('error_user_msg') or err.get('message')
                return error_response(
                    meta_msg or 'Meta API rejected the template submission.',
                    code='meta_template_submit_failed',
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
        مزامنة قوالب واتساب من Meta: تحديث الحالات واستيراد القوالب التي أُنشئت خارج الـ CRM.
        POST /api/integrations/templates/sync-whatsapp/
        """
        company = request.user.company
        blocked = _integration_gate(company, "whatsapp")
        if blocked is not None:
            return blocked
        wa, err_resp = _connected_wa_or_response(company)
        if err_resp is not None:
            return err_resp
        token = wa.get_access_token()
        if not token:
            return error_response(
                'WhatsApp account has no access token.',
                code='whatsapp_no_access_token',
            )
        try:
            meta_list, fetch_err = _fetch_all_meta_message_templates(wa.waba_id, token)
            if fetch_err is not None:
                return error_response(
                    'Failed to fetch templates from Meta.',
                    code='bad_request',
                    details=fetch_err,
                )
        except requests.RequestException as e:
            return error_response(
                str(e),
                code='bad_gateway',
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        wa_templates = MessageTemplate.objects.filter(
            company=company,
            channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        )
        by_meta_id = {}
        by_slug = {}
        for tpl in wa_templates:
            mid = str(tpl.meta_template_id or '').strip()
            if mid:
                by_meta_id[mid] = tpl
            slug = meta_slug_template_name(tpl.name, tpl.id)
            if slug:
                by_slug[slug] = tpl

        updated = 0
        linked = 0
        imported = 0

        for meta_tpl in meta_list:
            mid = str(meta_tpl.get('id') or '').strip()
            if not mid:
                continue
            mname = (meta_tpl.get('name') or '').strip()
            new_status = _meta_status_normalize(meta_tpl.get('status'))

            existing = by_meta_id.get(mid)
            if not existing and mname:
                existing = by_slug.get(mname)

            if existing:
                changed = False
                if not str(existing.meta_template_id or '').strip():
                    existing.meta_template_id = mid
                    by_meta_id[mid] = existing
                    changed = True
                    linked += 1
                if (existing.meta_status or '') != new_status:
                    existing.meta_status = new_status
                    changed = True
                    updated += 1
                if changed:
                    existing.save(update_fields=['meta_template_id', 'meta_status'])
                continue

            body, header_type, header_text, footer, buttons = _parse_meta_template_components(
                meta_tpl.get('components')
            )
            MessageTemplate.objects.create(
                company=company,
                name=mname or f'template_{mid}',
                channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
                content=body or f'(Imported from Meta: {mname or mid})',
                category=_meta_category_to_crm(meta_tpl.get('category')),
                language=(meta_tpl.get('language') or 'en_US').strip() or 'en_US',
                header_type=header_type,
                header_text=header_text,
                footer=footer,
                buttons=buttons,
                meta_template_id=mid,
                meta_status=new_status,
            )
            imported += 1

        return success_response(
            data={
                'message': 'Templates synced.',
                'updated': updated,
                'linked': linked,
                'imported': imported,
                'total_meta': len(meta_list),
            },
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
    wa, err_resp = _connected_wa_or_response(company)
    if err_resp is not None:
        return err_resp
    token = wa.get_access_token()
    if not token:
        return error_response(
            'WhatsApp account has no access token.',
            code='whatsapp_no_access_token',
        )
    url = f'{META_GRAPH_API_BASE_URL}/{wa.phone_number_id}?fields=messaging_limit_tier,quality_rating'
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

