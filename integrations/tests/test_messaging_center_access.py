"""Messaging Center is owner-only; staff keep read access to templates for Chats.

The UI hides the page from employees, but the endpoints behind it are what actually
protect company-wide message logs, campaign batches, and template authoring.
"""

import pytest
from django.urls import reverse

from integrations.models import MessageTemplate


@pytest.fixture
def template(company, db):
    return MessageTemplate.objects.create(
        company=company,
        name="welcome",
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content="Hello { اسم العميل }",
    )


@pytest.mark.django_db
def test_employee_can_list_templates_for_chats(authenticated_employee, template):
    """Chats (web + mobile) lists approved templates inside a conversation."""
    res = authenticated_employee.get(reverse('message-template-list'))
    assert res.status_code == 200


@pytest.mark.django_db
def test_employee_cannot_create_template(authenticated_employee):
    res = authenticated_employee.post(
        reverse('message-template-list'),
        {'name': 'promo', 'channel_type': 'whatsapp_api', 'content': 'Hi'},
        format='json',
    )
    assert res.status_code == 403


@pytest.mark.django_db
def test_employee_cannot_edit_or_delete_template(authenticated_employee, template):
    detail = reverse('message-template-detail', args=[template.id])
    assert authenticated_employee.patch(detail, {'content': 'x'}, format='json').status_code == 403
    assert authenticated_employee.delete(detail).status_code == 403


@pytest.mark.django_db
def test_employee_cannot_submit_template_to_meta(authenticated_employee, template):
    res = authenticated_employee.post(
        reverse('message-template-submit-to-whatsapp', args=[template.id]), {}, format='json'
    )
    assert res.status_code == 403


@pytest.mark.django_db
def test_employee_cannot_sync_templates_from_meta(authenticated_employee):
    res = authenticated_employee.post(reverse('message-template-sync-whatsapp'), {}, format='json')
    assert res.status_code == 403


@pytest.mark.django_db
def test_employee_cannot_read_company_message_logs(authenticated_employee):
    res = authenticated_employee.get(reverse('message_logs'))
    assert res.status_code == 403


@pytest.mark.django_db
def test_owner_can_read_company_message_logs(authenticated_admin):
    res = authenticated_admin.get(reverse('message_logs'))
    assert res.status_code == 200


@pytest.mark.django_db
def test_employee_cannot_start_a_campaign_batch(authenticated_employee):
    res = authenticated_employee.post(
        reverse('campaign_batches_create'), {'channel': 'whatsapp'}, format='json'
    )
    assert res.status_code == 403


@pytest.mark.django_db
def test_owner_can_start_a_campaign_batch(authenticated_admin):
    res = authenticated_admin.post(
        reverse('campaign_batches_create'), {'channel': 'whatsapp'}, format='json'
    )
    assert res.status_code in (200, 201)
