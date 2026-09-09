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

    def test_if_none_match_304_costs_no_queries(
        self, authenticated_admin, django_assert_num_queries
    ):
        """
        The whole point of the version token: an unchanged digest is answered from
        the cache alone.

        Neither tier is rebuilt, and the request does not reach the database at
        all. This is the case that runs on every poll of every open tab, so a
        regression here is the difference between a free request and ~9 queries.
        """
        first = authenticated_admin.get("/api/v1/sync/digest/")
        assert first.status_code == status.HTTP_200_OK
        etag = first["ETag"]
        assert api_body(first)["version"]
        assert etag

        with (
            patch("sync.views.build_badges") as badges,
            patch("sync.views.build_live") as live,
            django_assert_num_queries(0),
        ):
            second = authenticated_admin.get(
                "/api/v1/sync/digest/",
                HTTP_IF_NONE_MATCH=etag,
            )
            badges.assert_not_called()
            live.assert_not_called()

        assert second.status_code == status.HTTP_304_NOT_MODIFIED
        assert second.content == b""

    def test_stale_etag_rebuilds(self, authenticated_admin):
        """A token from an earlier state must not be honoured as a 304."""
        response = authenticated_admin.get(
            "/api/v1/sync/digest/", HTTP_IF_NONE_MATCH='"0.0.0.0"'
        )
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["version"]

    def test_new_notification_breaks_etag(
        self, authenticated_admin, admin_user
    ):
        """
        An arriving notification must reach a client holding a valid ETag.

        Covers the user-scoped bump: nothing in the request path knows the count
        moved, so only the signal on Notification keeps this correct.
        """
        from notifications.models import Notification, NotificationType

        first = authenticated_admin.get("/api/v1/sync/digest/")
        assert api_body(first)["notifications_unread"] == 0
        etag = first["ETag"]

        Notification.objects.create(
            user=admin_user,
            type=NotificationType.NEW_LEAD,
            title="n",
            body="b",
            read=False,
        )

        second = authenticated_admin.get(
            "/api/v1/sync/digest/", HTTP_IF_NONE_MATCH=etag
        )
        assert second.status_code == status.HTTP_200_OK
        assert api_body(second)["notifications_unread"] == 1

    def test_live_change_breaks_etag_despite_cached_badges(
        self, authenticated_admin, admin_user, company
    ):
        """A new walk-in must reach a client holding a valid ETag, badge cache or not."""
        from crm.models import Client, LeadArrival, LeadArrivalRouting

        first = authenticated_admin.get("/api/v1/sync/digest/")
        etag = first["ETag"]
        assert api_body(first)["arrivals_pending"] == 0

        client = Client.objects.create(
            name="Walk-in", company=company, priority="low", type="cold"
        )
        arrival = LeadArrival.objects.create(
            company=company,
            client=client,
            routing=LeadArrivalRouting.EXISTING_ASSIGNEE.value,
        )
        arrival.notified_users.add(admin_user)

        second = authenticated_admin.get(
            "/api/v1/sync/digest/", HTTP_IF_NONE_MATCH=etag
        )
        assert second.status_code == status.HTTP_200_OK
        assert api_body(second)["arrivals_pending"] == 1

    def test_marking_read_invalidates_badge_cache(self, authenticated_admin, admin_user):
        """Acting on a badge clears it now, not when the cache TTL happens to lapse."""
        from notifications.models import Notification, NotificationType

        Notification.objects.create(
            user=admin_user,
            type=NotificationType.NEW_LEAD,
            title="n",
            body="b",
            read=False,
        )
        assert api_body(authenticated_admin.get("/api/v1/sync/digest/"))[
            "notifications_unread"
        ] == 1

        authenticated_admin.post("/api/v1/notifications/mark_all_read/")

        assert api_body(authenticated_admin.get("/api/v1/sync/digest/"))[
            "notifications_unread"
        ] == 0

    def test_delete_all_invalidates_badge_cache(self, authenticated_admin, admin_user):
        """
        The bulk soft-delete path has to bump explicitly.

        ``delete_all`` is a ``queryset.update()``, so no ``post_save`` fires and
        the receiver in ``sync/signals.py`` never runs — and it removes *unread*
        rows, so the count really does change. Without the explicit
        ``invalidate_badges`` the digest keeps serving the cached count under an
        unchanged token, and the bell badge stays lit for up to a safety bucket
        after the user emptied the inbox.
        """
        from notifications.models import Notification, NotificationType

        Notification.objects.create(
            user=admin_user,
            type=NotificationType.NEW_LEAD,
            title="n",
            body="b",
            read=False,
        )
        assert api_body(authenticated_admin.get("/api/v1/sync/digest/"))[
            "notifications_unread"
        ] == 1

        authenticated_admin.delete("/api/v1/notifications/delete_all/")

        assert api_body(authenticated_admin.get("/api/v1/sync/digest/"))[
            "notifications_unread"
        ] == 0

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


@pytest.mark.django_db
class TestSyncDigestSliceVersions:
    """
    The digest as a change feed.

    Clients use these counters instead of a timer per query: hold the last value
    seen, refetch only the slice that moved. Two things have to hold for that to
    be safe — a slice must move when its data changes (or the client never
    refetches), and it must *not* move when unrelated data changes (or the feed
    degrades back into "refetch everything", which is what it replaced).
    """

    ALL_SLICES = {
        "global",
        "user",
        "company",
        "chat",
        "calls",
        "arrivals",
        "tenant_chat",
        "inbox",
    }

    def _versions(self, client):
        return api_body(client.get("/api/v1/sync/digest/"))["versions"]

    def _make_call(self, company, status=None):
        from integrations.models import WhatsAppAccount, WhatsAppCall, WhatsAppCallStatus

        account, _ = WhatsAppAccount.objects.get_or_create(
            company=company,
            phone_number_id="pn-slice-test",
            defaults={"waba_id": "waba-slice-test"},
        )
        return WhatsAppCall.objects.create(
            company=company,
            whatsapp_account=account,
            meta_call_id=f"call-{timezone.now().timestamp()}",
            status=status or WhatsAppCallStatus.RINGING,
        )

    def _make_whatsapp_message(self, company):
        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )
        return LeadWhatsAppMessage.objects.create(
            client=client,
            phone_number="111",
            body="hi",
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            is_read=False,
        )

    def test_digest_reports_every_slice(self, authenticated_admin):
        assert set(self._versions(authenticated_admin)) == self.ALL_SLICES

    def test_whatsapp_message_moves_only_the_chat_slice(
        self, authenticated_admin, company
    ):
        """
        The isolation guarantee, stated as a test.

        Before the split, this single write moved the one company counter, so the
        calls list, the arrivals board and team chat all refetched for a message
        that concerns none of them.
        """
        before = self._versions(authenticated_admin)
        self._make_whatsapp_message(company)
        after = self._versions(authenticated_admin)

        assert after["chat"] > before["chat"]
        for untouched in ("calls", "arrivals", "tenant_chat", "inbox"):
            assert after[untouched] == before[untouched], untouched

    def test_call_moves_only_the_calls_slice(self, authenticated_admin, company):
        before = self._versions(authenticated_admin)
        self._make_call(company)
        after = self._versions(authenticated_admin)

        assert after["calls"] > before["calls"]
        for untouched in ("chat", "arrivals", "tenant_chat", "inbox"):
            assert after[untouched] == before[untouched], untouched

    def test_social_message_moves_only_the_inbox_slice(
        self, authenticated_admin, company, meta_inbox_connection
    ):
        """An Instagram DM must not make the WhatsApp list or calls list refetch."""
        from integrations.models import SocialContact, SocialConversation, SocialMessage

        contact = SocialContact.objects.create(
            company=company,
            connection=meta_inbox_connection,
            channel="instagram",
            external_id="IGSID-digest",
        )
        conversation = SocialConversation.objects.create(
            company=company,
            connection=meta_inbox_connection,
            contact=contact,
            channel="instagram",
        )

        before = self._versions(authenticated_admin)
        SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_INBOUND,
            external_message_id="digest-mid",
            body="hi",
            is_read=False,
        )
        after = self._versions(authenticated_admin)

        assert after["inbox"] > before["inbox"]
        for untouched in ("chat", "calls", "arrivals", "tenant_chat"):
            assert after[untouched] == before[untouched], untouched

    def test_arrival_moves_only_the_arrivals_slice(
        self, authenticated_admin, admin_user, company
    ):
        from crm.models import Client, LeadArrival, LeadArrivalRouting

        before = self._versions(authenticated_admin)
        client = Client.objects.create(
            name="Walk-in", company=company, priority="low", type="cold"
        )
        arrival = LeadArrival.objects.create(
            company=company,
            client=client,
            routing=LeadArrivalRouting.EXISTING_ASSIGNEE.value,
        )
        arrival.notified_users.add(admin_user)
        after = self._versions(authenticated_admin)

        assert after["arrivals"] > before["arrivals"]
        for untouched in ("chat", "calls", "tenant_chat"):
            assert after[untouched] == before[untouched], untouched

    def test_slice_bump_also_rotates_the_etag(self, authenticated_admin, company):
        """
        A slice must never move without the ETag moving too.

        If it could, the digest would answer 304 while a slice the client watches
        had already advanced — the client would hold the old value forever and
        never refetch. bump_company_slice moves both for exactly this reason.
        """
        first = authenticated_admin.get("/api/v1/sync/digest/")
        etag = first["ETag"]
        before = api_body(first)["versions"]

        self._make_call(company)

        second = authenticated_admin.get(
            "/api/v1/sync/digest/", HTTP_IF_NONE_MATCH=etag
        )
        assert second.status_code == status.HTTP_200_OK
        assert api_body(second)["versions"]["calls"] > before["calls"]

    def test_versions_do_not_leak_across_companies(
        self, authenticated_admin, company, other_company
    ):
        """A tenant's counters must not move because another tenant had traffic."""
        before = self._versions(authenticated_admin)
        self._make_whatsapp_message(other_company)
        after = self._versions(authenticated_admin)

        assert after == before

    def test_304_still_costs_no_queries_with_versions(
        self, authenticated_admin, django_assert_num_queries
    ):
        """
        slice_versions() must stay on the 200 path only.

        A 304 means nothing moved, so there is nothing to report — reading the
        counters there would add a round trip to the hottest request in the
        system for a body that is never sent.
        """
        first = authenticated_admin.get("/api/v1/sync/digest/")
        etag = first["ETag"]

        with django_assert_num_queries(0):
            second = authenticated_admin.get(
                "/api/v1/sync/digest/", HTTP_IF_NONE_MATCH=etag
            )

        assert second.status_code == status.HTTP_304_NOT_MODIFIED
