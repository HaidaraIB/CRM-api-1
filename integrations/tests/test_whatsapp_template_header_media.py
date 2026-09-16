"""Tests for WhatsApp template header media components."""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from integrations.services.whatsapp_template_media import (
    build_meta_send_header_component,
    build_meta_submit_header_component,
    template_has_header_media,
    template_requires_header_media,
    upload_template_header_handle,
)
from integrations.views.templates_whatsapp import build_whatsapp_template_components_for_client


class FakeClient:
    name = 'Hassan'
    phone_number = '9647812113063'
    lead_company_name = ''
    budget = None
    invoice_number = None
    company = type('C', (), {'name': 'TenantCo'})()


def _image_template(**overrides):
    base = {
        'header_type': 'image',
        'header_text': '',
        'content': 'Body text',
        'buttons': [],
        'header_media_mime': 'image/jpeg',
    }
    base.update(overrides)
    tpl = type('T', (), base)()
    tpl.header_media = MagicMock()
    tpl.header_media.name = 'whatsapp_templates/headers/test.jpg'
    tpl.header_media.open = MagicMock(
        return_value=BytesIO(b'\xff\xd8\xff fake jpeg')
    )
    return tpl


def test_template_requires_header_media():
    tpl = type('T', (), {'header_type': 'image'})()
    assert template_requires_header_media(tpl) is True
    tpl_none = type('T', (), {'header_type': 'none'})()
    assert template_requires_header_media(tpl_none) is False


def test_template_has_header_media():
    tpl = _image_template()
    assert template_has_header_media(tpl) is True
    empty = type('T', (), {'header_media': None})()
    assert template_has_header_media(empty) is False


@patch('integrations.services.whatsapp_template_media.upload_media_to_meta', return_value='media-123')
def test_build_meta_send_header_component(mock_upload):
    tpl = _image_template()
    comp = build_meta_send_header_component(
        tpl,
        phone_number_id='123456',
        access_token='token',
    )
    assert comp == {
        'type': 'header',
        'parameters': [{'type': 'image', 'image': {'id': 'media-123'}}],
    }
    mock_upload.assert_called_once()


@patch('integrations.services.whatsapp_template_media.upload_template_header_handle', return_value='handle-abc')
@patch('integrations.services.whatsapp_template_media.read_template_header_bytes', return_value=(b'bytes', 'image/jpeg', 'h.jpg'))
def test_build_meta_submit_header_component(mock_read, mock_handle):
    tpl = _image_template()
    comp = build_meta_submit_header_component(
        tpl,
        app_id='app-id',
        access_token='token',
    )
    assert comp['type'] == 'HEADER'
    assert comp['format'] == 'IMAGE'
    assert comp['example']['header_handle'] == ['handle-abc']
    mock_handle.assert_called_once()


@patch('integrations.services.whatsapp_template_media.build_meta_send_header_component')
def test_build_template_components_includes_image_header(mock_header):
    mock_header.return_value = {
        'type': 'header',
        'parameters': [{'type': 'image', 'image': {'id': 'media-123'}}],
    }
    tpl = _image_template()
    comps = build_whatsapp_template_components_for_client(
        tpl,
        FakeClient(),
        phone_number_id='123',
        access_token='token',
    )
    assert any(c['type'] == 'header' for c in comps)
    mock_header.assert_called_once()


@patch('integrations.services.whatsapp_template_media.requests.post')
def test_upload_template_header_handle(mock_post):
    mock_post.side_effect = [
        MagicMock(status_code=200, json=lambda: {'id': 'upload:session1'}),
        MagicMock(status_code=200, json=lambda: {'h': '4::abc'}),
    ]
    handle = upload_template_header_handle(
        app_id='999',
        access_token='token',
        data=b'hello',
        mime='image/jpeg',
    )
    assert handle == '4::abc'
    assert mock_post.call_count == 2
