"""IntegrationAccount create: CRM WhatsApp + inbox WhatsApp can coexist."""

import pytest

from integrations.models import IntegrationAccount, IntegrationPlatform

pytestmark = pytest.mark.django_db


def test_create_whatsapp_inbox_when_crm_whatsapp_exists(authenticated_admin, company):
    IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP,
        name='CRM',
        status='connected',
    )
    response = authenticated_admin.post(
        '/api/v1/integrations/accounts/',
        {'platform': 'whatsapp_inbox', 'name': 'Inbox'},
        format='json',
    )
    assert response.status_code == 201
    assert IntegrationAccount.objects.filter(
        company=company,
        platform=IntegrationPlatform.WHATSAPP_INBOX,
    ).exists()


def test_legacy_whatsapp_post_maps_to_inbox_when_crm_exists(authenticated_admin, company):
    """Clients that POST platform=whatsapp from the Inbox tab get whatsapp_inbox."""
    IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP,
        name='CRM',
        status='connected',
    )
    response = authenticated_admin.post(
        '/api/v1/integrations/accounts/',
        {'platform': 'whatsapp', 'name': 'Inbox number'},
        format='json',
    )
    assert response.status_code == 201
    data = response.data.get('data') or response.data
    platform = data.get('platform') if isinstance(data, dict) else None
    assert platform == IntegrationPlatform.WHATSAPP_INBOX
    assert IntegrationAccount.objects.filter(company=company, platform=IntegrationPlatform.WHATSAPP).count() == 1
