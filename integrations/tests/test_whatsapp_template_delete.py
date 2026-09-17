"""Delete WhatsApp templates locally and on Meta when linked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status

from conftest import api_body
from integrations.models import IntegrationAccount, MessageTemplate, WhatsAppAccount


@pytest.fixture
def connected_whatsapp(company):
    account = IntegrationAccount.objects.create(
        company=company,
        platform='whatsapp',
        name='WA Templates',
        status='connected',
    )
    account.set_access_token('test-wa-token')
    account.save(update_fields=['access_token'])
    wa = WhatsAppAccount.objects.create(
        company=company,
        integration_account=account,
        waba_id='waba_delete_test',
        phone_number_id='pid_delete_1',
        display_phone_number='+15550009999',
        status='connected',
    )
    wa.set_access_token('test-wa-token')
    wa.save(update_fields=['access_token'])
    return wa


@pytest.mark.django_db
def test_delete_sms_template_skips_meta(authenticated_admin, company):
    tpl = MessageTemplate.objects.create(
        company=company,
        name='promo_sms',
        channel_type=MessageTemplate.CHANNEL_SMS,
        content='Hi there',
    )
    with patch('integrations.views.templates_whatsapp.requests.delete') as mock_delete:
        res = authenticated_admin.delete(reverse('message-template-detail', args=[tpl.id]))
    assert res.status_code == status.HTTP_204_NO_CONTENT
    mock_delete.assert_not_called()
    assert not MessageTemplate.objects.filter(pk=tpl.id).exists()


@pytest.mark.django_db
def test_delete_whatsapp_draft_skips_meta(authenticated_admin, company):
    tpl = MessageTemplate.objects.create(
        company=company,
        name='local_draft',
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content='Hello',
    )
    with patch('integrations.views.templates_whatsapp.requests.delete') as mock_delete:
        res = authenticated_admin.delete(reverse('message-template-detail', args=[tpl.id]))
    assert res.status_code == status.HTTP_204_NO_CONTENT
    mock_delete.assert_not_called()
    assert not MessageTemplate.objects.filter(pk=tpl.id).exists()


@pytest.mark.django_db
def test_delete_whatsapp_with_meta_id_calls_meta_and_removes_local(
    authenticated_admin, company, connected_whatsapp,
):
    tpl = MessageTemplate.objects.create(
        company=company,
        name='Appointment Reminder',
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content='Your appointment is tomorrow.',
        meta_template_id='1407680676729941',
        meta_status='APPROVED',
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"success": true}'
    mock_resp.json.return_value = {'success': True}

    with patch('integrations.views.templates_whatsapp.requests.delete', return_value=mock_resp) as mock_delete:
        res = authenticated_admin.delete(reverse('message-template-detail', args=[tpl.id]))

    assert res.status_code == status.HTTP_204_NO_CONTENT
    mock_delete.assert_called_once()
    call_kwargs = mock_delete.call_args.kwargs
    assert call_kwargs['params'] == {
        'hsm_id': '1407680676729941',
        'name': 'appointment_reminder',
    }
    assert connected_whatsapp.waba_id in mock_delete.call_args.args[0]
    assert not MessageTemplate.objects.filter(pk=tpl.id).exists()


@pytest.mark.django_db
def test_delete_whatsapp_with_meta_id_keeps_local_when_meta_fails(
    authenticated_admin, company, connected_whatsapp,
):
    tpl = MessageTemplate.objects.create(
        company=company,
        name='order_update',
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content='Order shipped.',
        meta_template_id='999888777',
        meta_status='APPROVED',
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.content = b'{"error":{"message":"Permission denied","code":200}}'
    mock_resp.text = mock_resp.content.decode()
    mock_resp.json.return_value = {
        'error': {'message': 'Permission denied', 'code': 200},
    }

    with patch('integrations.views.templates_whatsapp.requests.delete', return_value=mock_resp):
        res = authenticated_admin.delete(reverse('message-template-detail', args=[tpl.id]))

    assert res.status_code == status.HTTP_502_BAD_GATEWAY
    body = api_body(res)
    assert body['error']['code'] == 'meta_template_delete_failed'
    assert MessageTemplate.objects.filter(pk=tpl.id).exists()


@pytest.mark.django_db
def test_employee_cannot_delete_template(authenticated_employee, company):
    tpl = MessageTemplate.objects.create(
        company=company,
        name='welcome',
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content='Hello',
        meta_template_id='12345',
        meta_status='APPROVED',
    )
    res = authenticated_employee.delete(reverse('message-template-detail', args=[tpl.id]))
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert MessageTemplate.objects.filter(pk=tpl.id).exists()
