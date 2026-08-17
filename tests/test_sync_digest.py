"""Tests for GET /api/v1/sync/digest/."""

from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
class TestSyncDigest:
    def test_tenant_isolation(
        self, authenticated_admin, admin_user, company, other_company, other_admin_user, plan
    ):
        from datetime import timedelta

        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage
        from subscriptions.models import BillingCycle, Subscription

        now = timezone.now()
        Subscription.objects.create(
            company=other_company,
            plan=plan,
            is_active=True,
            start_date=now,
            end_date=now + timedelta(days=30),
            current_period_start=now,
            billing_cycle=BillingCycle.MONTHLY,
        )
        ours = Client.objects.create(name="Ours", company=company, priority="low", type="cold")
        theirs = Client.objects.create(
            name="Theirs", company=other_company, priority="low", type="cold"
        )
        LeadWhatsAppMessage.objects.create(
            client=ours,
            phone_number="111",
            body="hi",
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            is_read=False,
        )
        LeadWhatsAppMessage.objects.create(
            client=theirs,
            phone_number="222",
            body="secret",
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            is_read=False,
        )

        mine = api_body(authenticated_admin.get("/api/v1/sync/digest/"))
        assert mine["whatsapp_unread"] == 1

        from rest_framework.test import APIClient

        other = APIClient()
        other.force_authenticate(user=other_admin_user)
        other_body = api_body(other.get("/api/v1/sync/digest/"))
        assert other_body["whatsapp_unread"] == 1
        assert mine["whatsapp_unread"] == 1

    def test_if_none_match_304_skips_recompute(self, authenticated_admin):
        first = authenticated_admin.get("/api/v1/sync/digest/")
        assert first.status_code == status.HTTP_200_OK
        data = api_body(first)
        etag = first["ETag"]
        assert data["version"]
        assert etag

        with patch("sync.views.build_digest") as mocked:
            second = authenticated_admin.get(
                "/api/v1/sync/digest/",
                HTTP_IF_NONE_MATCH=etag,
            )
            mocked.assert_not_called()
        assert second.status_code == status.HTTP_304_NOT_MODIFIED
        assert second.content == b""

    def test_whatsapp_gated_omits_count(self, authenticated_employee, employee_user):
        employee_user.whatsapp_chat_enabled = False
        employee_user.save(update_fields=["whatsapp_chat_enabled"])
        body = api_body(authenticated_employee.get("/api/v1/sync/digest/"))
        assert body["whatsapp_unread"] is None
        assert authenticated_employee.get("/api/v1/sync/digest/").status_code == 200

    def test_counts_match_original_endpoints(
        self, authenticated_admin, admin_user, company
    ):
        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage
        from notifications.models import Notification, NotificationType
        from platform_content.models import NewsPost

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )
        LeadWhatsAppMessage.objects.create(
            client=client,
            phone_number="111",
            body="hi",
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            is_read=False,
        )
        Notification.objects.create(
            user=admin_user,
            type=NotificationType.NEW_LEAD,
            title="n",
            body="b",
            read=False,
        )
        NewsPost.objects.create(
            title_en="News",
            title_ar="خبر",
            body_en="x",
            body_ar="x",
            is_published=True,
            published_at=timezone.now(),
        )

        digest = api_body(authenticated_admin.get("/api/v1/sync/digest/"))
        wa = api_body(authenticated_admin.get("/api/v1/integrations/whatsapp/unread-count/"))
        notif = api_body(authenticated_admin.get("/api/v1/notifications/unread_count/"))
        news = api_body(
            authenticated_admin.get("/api/v1/public-content/news-posts/unread-count/")
        )
        assert digest["whatsapp_unread"] == wa["unread_count"]
        assert digest["notifications_unread"] == notif["unread_count"]
        assert digest["news_unread"] == news["unread_count"]
        assert digest["pbx_screen_pop"] is None
        assert "tenant_chat_unread" in digest
        assert "whatsapp_calls_pending" in digest
