"""Owner NEW_LEAD notifications for Meta / TikTok integration leads."""
from unittest.mock import patch

import pytest

from crm.models import Client
from integrations.models import IntegrationAccount, IntegrationPlatform
from integrations.services.inbound_lead import notify_owner_new_lead
from notifications.models import NotificationType


@pytest.mark.django_db
@pytest.mark.parametrize(
    "platform,source",
    [
        (IntegrationPlatform.META, "meta_lead_form"),
        (IntegrationPlatform.TIKTOK, "tiktok"),
    ],
)
def test_notify_owner_new_lead_for_meta_and_tiktok(company, platform, source):
    account = IntegrationAccount.objects.create(
        company=company,
        platform=platform,
        external_account_id=f"test_{platform}_{company.id}",
        name=f"{platform} account",
        status="connected",
    )
    client = Client.objects.create(
        name="Integration Lead",
        company=company,
        priority="medium",
        type="fresh",
        source=source,
        integration_account=account,
    )

    with patch(
        "notifications.services.NotificationService.send_notification",
        return_value=True,
    ) as send_mock:
        notify_owner_new_lead(company, client)

    send_mock.assert_called_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["user"] == company.owner
    assert kwargs["notification_type"] == NotificationType.NEW_LEAD
    assert kwargs["data"]["lead_id"] == client.id
    assert kwargs["data"]["lead_name"] == "Integration Lead"
    assert kwargs["data"]["added_by"] == account.get_platform_display()
