"""WhatsApp send error mapping, template params, and recipient normalization."""

import pytest

from integrations.views.webhooks_messaging import (
    _api_code_from_graph_error,
    _normalize_whatsapp_to_digits,
)
from integrations.views.templates_whatsapp import (
    build_whatsapp_template_components_for_client,
    whatsapp_template_body_parameter_values_for_client,
)
from integrations.whatsapp_account_sync import _apply_display_name_metadata


def test_api_code_132001_is_template_language_not_parameter_mismatch():
    body = {'error': {'code': 132001, 'message': 'Template name does not exist in the translation'}}
    assert _api_code_from_graph_error(body) == 'whatsapp_template_not_found_or_language'


def test_api_code_132000_is_parameter_count():
    body = {'error': {'code': 132000, 'message': 'Number of parameters does not match'}}
    assert _api_code_from_graph_error(body) == 'whatsapp_template_parameter_count'


def test_api_code_accepts_string_graph_codes():
    body = {'error': {'code': '131047', 'message': 'Re-engagement message'}}
    assert _api_code_from_graph_error(body) == 'whatsapp_outside_session_use_template'


def test_normalize_iraq_local_to_e164_digits():
    assert _normalize_whatsapp_to_digits('07812113063') == '9647812113063'
    assert _normalize_whatsapp_to_digits('+9647812113063') == '9647812113063'
    assert _normalize_whatsapp_to_digits('964 781 211 3063') == '9647812113063'


def test_positional_fill_puts_customer_first():
    class FakeClient:
        name = 'Hassan'
        phone_number = '9647812113063'
        lead_company_name = 'LeadCo'
        budget = None
        invoice_number = None
        company = type('C', (), {'name': 'TenantCo'})()

    vals = whatsapp_template_body_parameter_values_for_client('مرحبا {{1}} كيف حالك؟', FakeClient())
    assert vals == ['Hassan']


def test_build_template_components_includes_header_and_body():
    class FakeClient:
        name = 'Hassan'
        phone_number = '9647812113063'
        lead_company_name = ''
        budget = None
        invoice_number = None
        company = type('C', (), {'name': 'TenantCo'})()

    template = type(
        'T',
        (),
        {
            'header_type': 'text',
            'header_text': 'Hello {{1}}',
            'content': 'Body for {{1}}',
            'buttons': [],
        },
    )()
    comps = build_whatsapp_template_components_for_client(template, FakeClient())
    types = [c['type'] for c in comps]
    assert 'header' in types
    assert 'body' in types
    header = next(c for c in comps if c['type'] == 'header')
    assert header['parameters'][0]['text'] == 'Hassan'


def test_display_name_metadata_pending_not_approved():
    meta = _apply_display_name_metadata({}, name_status='PENDING_REVIEW', verified_name='Loop')
    assert meta['display_name_status'] == 'PENDING_REVIEW'
    assert meta['display_name_approved'] is False
    assert meta['verified_name'] == 'Loop'


def test_display_name_metadata_approved():
    meta = _apply_display_name_metadata({}, name_status='APPROVED')
    assert meta['display_name_approved'] is True
