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


def _whatsapp_preview_label(kind: str | None, body: str | None) -> str:
    """Conversation list snippet for text or media WhatsApp messages."""
    labels = {
        'image': 'Photo',
        'video': 'Video',
        'audio': 'Voice message',
        'document': 'Document',
    }
    base = labels.get(kind or '', '')
    cap = (body or '').strip().replace('\n', ' ')
    if base and cap:
        return f'{base}: {cap[:120]}'
    if cap:
        return cap[:160]
    return base or ''


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
    from integrations.whatsapp_access import (
        filter_clients_queryset_for_whatsapp,
        user_can_access_client,
    )

    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked

    if request.method == 'DELETE':
        client_id = request.query_params.get('client')
        phone = (request.query_params.get('phone') or '').strip()
        if not (client_id and str(client_id).isdigit()) and not phone:
            return error_response('client or phone query parameter is required', code='bad_request')
        if client_id and str(client_id).isdigit():
            try:
                client = company.clients.get(id=int(client_id))
            except Client.DoesNotExist:
                return error_response('Contact not found', code='whatsapp_contact_not_found', status_code=404)
            if not user_can_access_client(request.user, client):
                return error_response('Contact not found', code='whatsapp_contact_not_found', status_code=404)
        qs = _whatsapp_thread_messages_qs(company, client_id=client_id, phone=phone or None)
        deleted_count, _ = qs.delete()
        return success_response(data={'deleted': deleted_count})

    # عملاء لديهم على الأقل رسالة واتساب، مرتبون بآخر رسالة
    sub = (
        LeadWhatsAppMessage.objects.filter(client__company=company)
        .values('client_id')
        .annotate(last_at=Max('created_at'))
        .order_by('-last_at')
    )
    client_ids = [s['client_id'] for s in sub[:200]]
    last_at_by_id = {s['client_id']: s['last_at'] for s in sub if s['client_id'] in client_ids}
    order = {cid: i for i, cid in enumerate(client_ids)}
    clients_qs = filter_clients_queryset_for_whatsapp(
        request.user,
        Client.objects.filter(id__in=client_ids),
    )
    clients = list(clients_qs.select_related('company', 'assigned_to'))
    clients.sort(key=lambda c: order.get(c.id, 999))
    clients = clients[:100]

    # Last message preview per client (one query)
    last_bodies: dict[int, str] = {}
    unread_by_id: dict[int, int] = {}
    if clients:
        from django.db.models import Count, OuterRef, Subquery

        latest_body = (
            LeadWhatsAppMessage.objects.filter(client_id=OuterRef('pk'))
            .order_by('-created_at')
            .values('body')[:1]
        )
        latest_kind = (
            LeadWhatsAppMessage.objects.filter(client_id=OuterRef('pk'))
            .order_by('-created_at')
            .values('attachment_kind')[:1]
        )
        client_id_list = [c.id for c in clients]
        for row in Client.objects.filter(id__in=client_id_list).annotate(
            last_body=Subquery(latest_body),
            last_kind=Subquery(latest_kind),
        ).values('id', 'last_body', 'last_kind'):
            last_bodies[row['id']] = _whatsapp_preview_label(
                row.get('last_kind'), row.get('last_body')
            )

        for row in (
            LeadWhatsAppMessage.objects.filter(
                client_id__in=client_id_list,
                direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
                is_read=False,
            )
            .values('client_id')
            .annotate(n=Count('id'))
        ):
            unread_by_id[row['client_id']] = row['n']

    return success_response(
        data=[
            {
                'id': c.id,
                'name': c.name,
                'phone_number': c.phone_number or '',
                'lead_company_name': getattr(c, 'lead_company_name', None) or '',
                'last_message_at': last_at_by_id.get(c.id).isoformat() if last_at_by_id.get(c.id) else None,
                'last_message_preview': last_bodies.get(c.id, ''),
                'assigned_to_id': c.assigned_to_id,
                'unread_count': unread_by_id.get(c.id, 0),
            }
            for c in clients
        ],
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_unread_count(request):
    """
    Total unread inbound WhatsApp messages in the caller's scope.
    GET /api/integrations/whatsapp/unread-count/

    Owners/admins (and other company-wide lead viewers): all company unread.
    Employees/Doctors: only threads for leads assigned to them.
    """
    from integrations.whatsapp_access import filter_whatsapp_messages_queryset

    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked

    qs = LeadWhatsAppMessage.objects.filter(
        client__company=company,
        direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        is_read=False,
    )
    qs = filter_whatsapp_messages_queryset(request.user, qs)
    return success_response(data={'unread_count': qs.count()})


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_mark_conversation_read(request):
    """
    Mark all inbound WhatsApp messages in a thread as read (agent opened the chat).
    POST /api/integrations/whatsapp/conversations/mark-read/
    Body: { "client": <id> } or { "phone": "<digits>" }
    """
    from crm.models import Client
    from integrations.whatsapp_access import (
        resolve_accessible_client_by_phone,
        user_can_access_client,
    )

    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked

    client_id = request.data.get('client') or request.data.get('client_id')
    phone = (request.data.get('phone') or '').strip()
    client = None

    if client_id is not None and str(client_id).isdigit():
        try:
            client = company.clients.get(id=int(client_id))
        except Client.DoesNotExist:
            return error_response(
                'Contact not found',
                code='whatsapp_contact_not_found',
                status_code=404,
            )
        if not user_can_access_client(request.user, client):
            return error_response(
                'Contact not found',
                code='whatsapp_contact_not_found',
                status_code=404,
            )
    elif phone:
        client, err = resolve_accessible_client_by_phone(request.user, phone)
        if err:
            return error_response(
                'Contact not found',
                code='whatsapp_contact_not_found',
                status_code=404,
            )
        if not client:
            return success_response(data={'marked': 0})
    else:
        return error_response('client or phone is required', code='bad_request')

    updated = LeadWhatsAppMessage.objects.filter(
        client=client,
        direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        is_read=False,
    ).update(is_read=True)
    return success_response(data={'marked': updated, 'client_id': client.id})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_contact_by_phone(request):
    """
    Resolve a CRM client by phone for WhatsApp chat (manual number → lead link).
    GET /api/integrations/whatsapp/contact-by-phone/?phone=...

    Staff: returns not_found if the lead is missing or not assigned to them
    (same response whether missing or owned by someone else).
    """
    from integrations.whatsapp_access import resolve_accessible_client_by_phone

    company = request.user.company
    blocked = _integration_gate(company, "whatsapp")
    if blocked is not None:
        return blocked
    phone = (request.query_params.get('phone') or '').strip()
    if not phone:
        return error_response('phone is required', code='bad_request')
    client, err = resolve_accessible_client_by_phone(request.user, phone)
    if err:
        return error_response(
            'Contact not found',
            code='whatsapp_contact_not_found',
            status_code=404,
        )
    if not client:
        return success_response(data=None)
    return success_response(
        data={
            'id': client.id,
            'name': client.name,
            'phone_number': client.phone_number or '',
            'lead_company_name': getattr(client, 'lead_company_name', None) or '',
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
    """Fill {{1}}..{{n}} for Meta-imported bodies that have no [Bracket] markers.

    Order matches Messaging Center preview: customer name, company, phone, lead company.
    """
    n = _positional_variable_count(content)
    if n <= 0:
        return []
    phone = (getattr(client, 'phone_number', None) or '').strip()
    pool = [
        _client_customer_name(client),
        _tenant_company_name(client) or _client_lead_company_name(client),
        phone,
        _client_lead_company_name(client),
        '' if getattr(client, 'budget', None) is None else str(client.budget).strip(),
        (getattr(client, 'invoice_number', None) or '').strip(),
    ]
    values = []
    for i in range(n):
        v = pool[i] if i < len(pool) else ''
        if not v and i == 0:
            v = _client_customer_name(client) or _company_placeholder_value(client)
        elif not v:
            v = _client_customer_name(client) or _tenant_company_name(client)
        values.append((v or '-')[:1024])
    return values


def whatsapp_template_header_parameter_values_for_client(header_text: str, client) -> list:
    """Text header {{n}} / bracket values for Cloud API template send."""
    return whatsapp_template_body_parameter_values_for_client(header_text or '', client)


def whatsapp_template_button_url_parameter_values(buttons, client) -> list:
    """
    Dynamic URL button suffix values (one text param per URL button that contains {{n}}).
    Returns list of (button_index, values) for Meta BUTTON components.
    """
    out = []
    if not isinstance(buttons, list):
        return out
    for idx, btn in enumerate(buttons):
        if not isinstance(btn, dict):
            continue
        if (btn.get('type') or '').lower() != 'url':
            continue
        url = btn.get('url') or ''
        n = _positional_variable_count(url)
        if n <= 0:
            continue
        values = whatsapp_template_body_parameter_values_for_client(url, client)
        # Meta expects a single suffix param for dynamic URL buttons typically
        if values:
            out.append((idx, values[:n]))
    return out


def build_whatsapp_template_components_for_client(template, client, body_param_values=None) -> list:
    """
    Build Meta template `components` array: header (text vars), body, dynamic URL buttons.
    """
    components = []
    header_type = (getattr(template, 'header_type', None) or '').strip().lower()
    header_text = (getattr(template, 'header_text', None) or '').strip()
    if header_type == 'text' and header_text:
        header_vals = whatsapp_template_header_parameter_values_for_client(header_text, client)
        if header_vals:
            components.append(
                {
                    'type': 'header',
                    'parameters': [{'type': 'text', 'text': p[:1024]} for p in header_vals],
                }
            )

    body_vals = body_param_values
    if body_vals is None:
        body_vals = whatsapp_template_body_parameter_values_for_client(
            getattr(template, 'content', None) or '', client
        )
    if body_vals:
        components.append(
            {
                'type': 'body',
                'parameters': [{'type': 'text', 'text': p[:1024]} for p in body_vals],
            }
        )

    for btn_index, vals in whatsapp_template_button_url_parameter_values(
        getattr(template, 'buttons', None) or [], client
    ):
        components.append(
            {
                'type': 'button',
                'sub_type': 'url',
                'index': str(btn_index),
                'parameters': [{'type': 'text', 'text': p[:1024]} for p in vals],
            }
        )
    return components


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


_BRACKET_PLACEHOLDERS_BY_INDEX = [
    '[Customer Name]',
    '[Company]',
    '[Amount]',
    '[Invoice Number]',
]


def _is_whatsapp_channel(channel_type: str | None) -> bool:
    return (channel_type or '').lower() in ('whatsapp', 'whatsapp_api')


def _meta_positional_to_bracket_content(content: str) -> str:
    """Map Meta {{1}}..{{n}} placeholders to CRM bracket placeholders for SMS."""

    def repl(match):
        n = int(match.group(1))
        idx = n - 1
        if idx < len(_BRACKET_PLACEHOLDERS_BY_INDEX):
            return _BRACKET_PLACEHOLDERS_BY_INDEX[idx]
        return f'[Value {n}]'

    return re.sub(r'\{\{\s*(\d+)\s*\}\}', repl, content or '')


def _sms_body_from_template(template: MessageTemplate) -> str:
    """Build SMS-friendly body from a WhatsApp template."""
    parts: list[str] = []
    header_type = (template.header_type or '').strip().lower()
    if header_type == 'text' and (template.header_text or '').strip():
        parts.append(_meta_positional_to_bracket_content(template.header_text.strip()))
    body = template.content or ''
    if _positional_variable_count(body):
        body = _meta_positional_to_bracket_content(body)
    elif body.lower().startswith('(imported from meta:'):
        body = body.split(')', 1)[-1].strip() or body
    if body.strip():
        parts.append(body.strip())
    if (template.footer or '').strip():
        parts.append((template.footer or '').strip())
    return '\n\n'.join(parts).strip()


def _clone_counterpart_suffix(target_channel: str) -> str:
    return '_sms' if target_channel == MessageTemplate.CHANNEL_SMS else '_wa'


def _is_converted_clone_name(name: str) -> bool:
    """Names ending with _sms or _wa were created via channel conversion."""
    n = (name or '').strip().lower()
    return n.endswith('_sms') or n.endswith('_wa')


def _counterpart_name_for_source(name: str, source_is_wa: bool) -> str:
    suffix = '_sms' if source_is_wa else '_wa'
    stem = (name or '').strip()
    if stem.lower().endswith(suffix):
        return stem
    return f'{stem}{suffix}'[:255]


def _template_has_counterpart_link(company, template: MessageTemplate) -> bool:
    """True when conversion is locked (source already converted or target is a conversion)."""
    stem = (template.name or '').strip()
    if _is_converted_clone_name(stem):
        return True

    is_wa = _is_whatsapp_channel(template.channel_type)
    expected = _counterpart_name_for_source(stem, is_wa)
    opposite = MessageTemplate.CHANNEL_SMS if is_wa else MessageTemplate.CHANNEL_WHATSAPP_API
    if MessageTemplate.objects.filter(company=company, channel_type=opposite, name=expected).exists():
        return True

    for other in MessageTemplate.objects.filter(company=company).exclude(pk=template.pk).only(
        'id', 'name', 'channel_type'
    ):
        other_is_wa = _is_whatsapp_channel(other.channel_type)
        if other_is_wa == is_wa:
            continue
        if _counterpart_name_for_source(other.name, other_is_wa) == stem:
            return True

    return False


def _unique_clone_name(company, base_name: str, target_channel: str) -> str:
    suffix = _clone_counterpart_suffix(target_channel)
    stem = (base_name or 'template').strip()
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)].rstrip('_') or 'template'
    candidate = f'{stem}{suffix}'[:255]
    if not MessageTemplate.objects.filter(company=company, name=candidate, channel_type=target_channel).exists():
        return candidate
    n = 2
    while n < 1000:
        candidate = f'{stem}{suffix}_{n}'[:255]
        if not MessageTemplate.objects.filter(company=company, name=candidate, channel_type=target_channel).exists():
            return candidate
        n += 1
    return f'{stem}{suffix}_{n}'[:255]


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

    @action(detail=True, methods=['post'], url_path='clone-to-channel')
    def clone_to_channel(self, request, pk=None):
        """
        Create a counterpart template on the other channel (WhatsApp ↔ SMS).
        POST /api/integrations/templates/:id/clone-to-channel/
        """
        template = self.get_object()
        company = request.user.company
        blocked = _integration_gate(company, 'whatsapp')
        if blocked is not None:
            return blocked

        if _template_has_counterpart_link(company, template):
            return error_response(
                'This template is already linked to a channel counterpart and cannot be converted again.',
                code='template_conversion_locked',
            )

        is_wa = _is_whatsapp_channel(template.channel_type)
        target_channel = MessageTemplate.CHANNEL_SMS if is_wa else MessageTemplate.CHANNEL_WHATSAPP_API
        suffix = _clone_counterpart_suffix(target_channel)
        stem = (template.name or '').strip()
        expected_name = f'{stem}{suffix}'[:255]
        if MessageTemplate.objects.filter(company=company, channel_type=target_channel, name=expected_name).exists():
            return error_response(
                'A counterpart template already exists for this channel.',
                code='template_counterpart_exists',
                details={'name': expected_name},
            )

        new_name = _unique_clone_name(company, stem, target_channel)
        if target_channel == MessageTemplate.CHANNEL_SMS:
            content = _sms_body_from_template(template)
            if not content:
                return error_response(
                    'Template has no content to copy to SMS.',
                    code='template_content_empty',
                )
            new_tpl = MessageTemplate.objects.create(
                company=company,
                name=new_name,
                channel_type=MessageTemplate.CHANNEL_SMS,
                content=content,
                category=template.category or MessageTemplate.CATEGORY_UTILITY,
                language=template.language or 'en_US',
            )
        else:
            content = (template.content or '').strip()
            if not content:
                return error_response(
                    'Template has no content to copy to WhatsApp.',
                    code='template_content_empty',
                )
            new_tpl = MessageTemplate.objects.create(
                company=company,
                name=new_name,
                channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
                content=content,
                category=template.category or MessageTemplate.CATEGORY_UTILITY,
                language=template.language or 'en_US',
                header_type='none',
                header_text='',
                footer='',
                buttons=[],
            )

        return success_response(
            data=MessageTemplateSerializer(new_tpl).data,
            status_code=status.HTTP_201_CREATED,
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
        seen_meta_ids: set[str] = set()
        seen_names: set[str] = set()

        for meta_tpl in meta_list:
            mid = str(meta_tpl.get('id') or '').strip()
            if not mid:
                continue
            mname = (meta_tpl.get('name') or '').strip()
            new_status = _meta_status_normalize(meta_tpl.get('status'))
            seen_meta_ids.add(mid)
            if mname:
                seen_names.add(mname.lower())

            existing = by_meta_id.get(mid)
            if not existing and mname:
                existing = by_slug.get(mname)

            if existing:
                changed = False
                update_fields = []
                prev_mid = str(existing.meta_template_id or '').strip()
                if prev_mid != mid:
                    # Re-link when matching by name after switching WABA (old Meta id ≠ current).
                    existing.meta_template_id = mid
                    by_meta_id[mid] = existing
                    changed = True
                    if not prev_mid:
                        linked += 1
                    update_fields.append('meta_template_id')
                if (existing.meta_status or '') != new_status:
                    existing.meta_status = new_status
                    changed = True
                    updated += 1
                    update_fields.append('meta_status')
                # Keep CRM copy aligned with Meta so send language/params match Graph.
                body, header_type, header_text, footer, buttons = _parse_meta_template_components(
                    meta_tpl.get('components')
                )
                new_lang = (meta_tpl.get('language') or 'en_US').strip() or 'en_US'
                if body and (existing.content or '') != body:
                    existing.content = body
                    update_fields.append('content')
                    changed = True
                if (existing.language or '') != new_lang:
                    existing.language = new_lang
                    update_fields.append('language')
                    changed = True
                if (existing.header_type or '') != (header_type or 'none'):
                    existing.header_type = header_type or 'none'
                    update_fields.append('header_type')
                    changed = True
                if (existing.header_text or '') != (header_text or ''):
                    existing.header_text = header_text or ''
                    update_fields.append('header_text')
                    changed = True
                if (existing.footer or '') != (footer or ''):
                    existing.footer = footer or ''
                    update_fields.append('footer')
                    changed = True
                if buttons is not None and list(existing.buttons or []) != list(buttons):
                    existing.buttons = buttons
                    update_fields.append('buttons')
                    changed = True
                if mname and (existing.name or '') != mname:
                    # Keep Meta template name exact (slug already lowercase from Meta).
                    existing.name = mname
                    update_fields.append('name')
                    changed = True
                if changed and update_fields:
                    existing.save(update_fields=list(dict.fromkeys(update_fields)))
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

        # Templates that lived on a previous WABA (e.g. Meta 555 test) cannot be
        # sent from the current phone — remove them from CRM so lists stay clean.
        removed = 0
        for tpl in wa_templates:
            mid = str(tpl.meta_template_id or '').strip()
            slug = meta_slug_template_name(tpl.name, tpl.id)
            on_current = (mid and mid in seen_meta_ids) or (slug and slug in seen_names)
            if on_current:
                continue
            # Keep local drafts (never submitted / no Meta id).
            if not mid and (tpl.meta_status or '').upper() in ('', 'PENDING'):
                continue
            if not mid and not (tpl.meta_status or '').strip():
                continue
            tpl.delete()
            removed += 1

        return success_response(
            data={
                'message': 'Templates synced.',
                'updated': updated,
                'linked': linked,
                'imported': imported,
                'removed': removed,
                'waba_id': wa.waba_id,
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

