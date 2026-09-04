"""WhatsApp conversation status / filter / snooze / inbound reopen tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from conftest import api_body


CONV_URL = "/api/v1/integrations/whatsapp/conversations/"
STATE_URL = "/api/v1/integrations/whatsapp/conversations/state/"


@pytest.fixture
def wa_client(company, db):
    from crm.models import Client

    return Client.objects.create(
        name="WA Lead",
        company=company,
        priority="low",
        type="cold",
        phone_number="9647701111111",
    )


@pytest.fixture
def other_wa_client(company, employee_user, db):
    from crm.models import Client

    return Client.objects.create(
        name="Other Lead",
        company=company,
        priority="low",
        type="cold",
        phone_number="9647702222222",
        assigned_to=employee_user,
    )


def _inbound(client, body="hi", is_read=False):
    from integrations.models import LeadWhatsAppMessage

    return LeadWhatsAppMessage.objects.create(
        client=client,
        phone_number=client.phone_number or "9647700000000",
        body=body,
        direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        is_read=is_read,
    )


def _outbound(client, body="bye"):
    from integrations.models import LeadWhatsAppMessage

    return LeadWhatsAppMessage.objects.create(
        client=client,
        phone_number=client.phone_number or "9647700000000",
        body=body,
        direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
        is_read=True,
    )


def _set_state(api, client_id, **payload):
    body = {"client": client_id, **payload}
    return api.post(STATE_URL, body, format="json")


def _results(response):
    data = api_body(response)
    assert isinstance(data, dict), data
    return data["results"]


def _err_code(response):
    body = api_body(response)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("code")
        return body.get("code")
    return None


@pytest.mark.django_db
class TestWhatsAppConversationStatusDefaults:
    def test_default_status_is_open_with_no_row(self, authenticated_admin, wa_client):
        _inbound(wa_client)
        resp = authenticated_admin.get(CONV_URL)
        assert resp.status_code == status.HTTP_200_OK
        rows = _results(resp)
        assert len(rows) == 1
        assert rows[0]["status"] == "open"
        assert rows[0]["is_starred"] is False
        assert rows[0]["is_unsubscribed"] is False
        assert rows[0]["snoozed_until"] is None


@pytest.mark.django_db
class TestWhatsAppConversationStateMutation:
    def test_set_and_clear_each_status(self, authenticated_admin, wa_client):
        _inbound(wa_client)
        for st in ("pending", "spam", "invalid", "done", "open"):
            resp = _set_state(authenticated_admin, wa_client.id, status=st)
            assert resp.status_code == status.HTTP_200_OK, api_body(resp)
            assert api_body(resp)["status"] == st
            row = _results(authenticated_admin.get(f"{CONV_URL}?status={st}"))[0]
            assert row["status"] == st
            assert row["snoozed_until"] is None

    def test_star_and_unsubscribe(self, authenticated_admin, wa_client):
        _inbound(wa_client)
        resp = _set_state(
            authenticated_admin, wa_client.id, is_starred=True, is_unsubscribed=True
        )
        assert resp.status_code == status.HTTP_200_OK
        data = api_body(resp)
        assert data["is_starred"] is True
        assert data["is_unsubscribed"] is True

        starred = _results(authenticated_admin.get(f"{CONV_URL}?starred=1"))
        assert len(starred) == 1
        unsub = _results(authenticated_admin.get(f"{CONV_URL}?status=unsubscribed"))
        assert len(unsub) == 1

        _set_state(authenticated_admin, wa_client.id, is_starred=False, is_unsubscribed=False)
        assert _results(authenticated_admin.get(f"{CONV_URL}?starred=1")) == []

    def test_snooze_requires_future_time(self, authenticated_admin, wa_client):
        _inbound(wa_client)
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        bad = _set_state(
            authenticated_admin, wa_client.id, status="snoozed", snoozed_until=past
        )
        assert bad.status_code == status.HTTP_400_BAD_REQUEST

        missing = _set_state(authenticated_admin, wa_client.id, status="snoozed")
        assert missing.status_code == status.HTTP_400_BAD_REQUEST

        future = (timezone.now() + timedelta(hours=2)).isoformat()
        ok = _set_state(
            authenticated_admin, wa_client.id, status="snoozed", snoozed_until=future
        )
        assert ok.status_code == status.HTTP_200_OK
        assert api_body(ok)["status"] == "snoozed"
        assert api_body(ok)["snoozed_until"] is not None

    def test_snooze_expires_back_to_open(self, authenticated_admin, wa_client, company):
        from integrations.models import WhatsAppConversationState, WhatsAppConversationStatus
        from integrations.whatsapp_conversation_state import (
            ensure_conversation_state,
            sweep_expired_snoozes,
        )

        _inbound(wa_client)
        state = ensure_conversation_state(wa_client)
        state.status = WhatsAppConversationStatus.SNOOZED
        state.snoozed_until = timezone.now() - timedelta(minutes=1)
        state.save(update_fields=["status", "snoozed_until", "updated_at"])

        n = sweep_expired_snoozes(company)
        assert n == 1
        state.refresh_from_db()
        assert state.status == WhatsAppConversationStatus.OPEN
        assert state.snoozed_until is None

        # List endpoint also sweeps before answering
        state.status = WhatsAppConversationStatus.SNOOZED
        state.snoozed_until = timezone.now() - timedelta(minutes=1)
        state.save(update_fields=["status", "snoozed_until", "updated_at"])
        row = _results(authenticated_admin.get(CONV_URL))[0]
        assert row["status"] == "open"


@pytest.mark.django_db
class TestInboundReopenRules:
    def test_inbound_reopens_done_and_snoozed(self, wa_client):
        from integrations.models import WhatsAppConversationStatus
        from integrations.whatsapp_conversation_state import ensure_conversation_state

        for st in (WhatsAppConversationStatus.DONE, WhatsAppConversationStatus.SNOOZED):
            state = ensure_conversation_state(wa_client)
            state.status = st
            state.snoozed_until = (
                timezone.now() + timedelta(hours=1)
                if st == WhatsAppConversationStatus.SNOOZED
                else None
            )
            state.save()
            _inbound(wa_client, body=f"reopen-{st}")
            state.refresh_from_db()
            assert state.status == WhatsAppConversationStatus.OPEN
            assert state.snoozed_until is None

    def test_inbound_does_not_reopen_spam_invalid_pending(self, wa_client):
        from integrations.models import WhatsAppConversationStatus
        from integrations.whatsapp_conversation_state import ensure_conversation_state

        for st in (
            WhatsAppConversationStatus.SPAM,
            WhatsAppConversationStatus.INVALID,
            WhatsAppConversationStatus.PENDING,
        ):
            state = ensure_conversation_state(wa_client)
            state.status = st
            state.save(update_fields=["status", "updated_at"])
            _inbound(wa_client, body=f"keep-{st}")
            state.refresh_from_db()
            assert state.status == st


@pytest.mark.django_db
class TestConversationFiltersAndCounts:
    def test_status_filters_and_counts(self, authenticated_admin, company, employee_user):
        from crm.models import Client
        from integrations.whatsapp_conversation_state import ensure_conversation_state
        from integrations.models import WhatsAppConversationStatus

        clients = {}
        for key in ("open", "pending", "spam", "done"):
            c = Client.objects.create(
                name=f"Lead {key}",
                company=company,
                priority="low",
                type="cold",
                phone_number=f"964770{key[:3]}0000",
            )
            _inbound(c)
            if key != "open":
                st = ensure_conversation_state(c)
                st.status = key
                st.save(update_fields=["status", "updated_at"])
            clients[key] = c

        # unread
        unread_c = Client.objects.create(
            name="Unread",
            company=company,
            priority="low",
            type="cold",
            phone_number="9647709999999",
        )
        _inbound(unread_c, is_read=False)

        resp = authenticated_admin.get(CONV_URL)
        data = api_body(resp)
        assert data["status_counts"]["open"] >= 2  # default open + unread (no row)
        assert data["status_counts"]["pending"] == 1
        assert data["status_counts"]["spam"] == 1
        assert data["status_counts"]["done"] == 1
        assert data["status_counts"]["unread"] >= 1
        assert data["assignment_counts"]["all"] == data["status_counts"]["all"]

        pending = _results(authenticated_admin.get(f"{CONV_URL}?status=pending"))
        assert len(pending) == 1
        assert pending[0]["id"] == clients["pending"].id

        unreplied = _results(authenticated_admin.get(f"{CONV_URL}?unreplied=1"))
        assert all(r["last_message_direction"] == "inbound" for r in unreplied)

    def test_assignment_mine_and_unassigned(
        self, authenticated_admin, wa_client, other_wa_client, employee_user, admin_user
    ):
        wa_client.assigned_to = None
        wa_client.save(update_fields=["assigned_to"])
        _inbound(wa_client)
        _inbound(other_wa_client)

        unassigned = _results(authenticated_admin.get(f"{CONV_URL}?assignment=unassigned"))
        assert {r["id"] for r in unassigned} == {wa_client.id}

        # Assign one lead to admin and filter mine
        wa_client.assigned_to = admin_user
        wa_client.save(update_fields=["assigned_to"])
        mine = _results(authenticated_admin.get(f"{CONV_URL}?assignment=mine"))
        assert {r["id"] for r in mine} == {wa_client.id}

        # employee sees only their assigned
        client = APIClient()
        client.force_authenticate(user=employee_user)
        emp_rows = _results(client.get(CONV_URL))
        assert {r["id"] for r in emp_rows} == {other_wa_client.id}

    def test_agent_filter_403_for_employee(
        self, authenticated_employee, employee_user, other_wa_client, admin_user
    ):
        _inbound(other_wa_client)
        resp = authenticated_employee.get(f"{CONV_URL}?agent={admin_user.id}")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_only_sees_assigned_under_every_filter(
        self, employee_user, other_wa_client, wa_client, company, subscription
    ):
        from integrations.whatsapp_conversation_state import ensure_conversation_state
        from integrations.models import WhatsAppConversationStatus

        wa_client.assigned_to = None
        wa_client.save(update_fields=["assigned_to"])
        _inbound(wa_client)
        _inbound(other_wa_client)
        st = ensure_conversation_state(other_wa_client)
        st.status = WhatsAppConversationStatus.PENDING
        st.is_starred = True
        st.save()

        api = APIClient()
        api.force_authenticate(user=employee_user)
        for qs in (
            "",
            "?status=all",
            "?status=pending",
            "?status=open",
            "?starred=1",
            "?unreplied=1",
            "?assignment=all",
            "?assignment=mine",
            "?assignment=unassigned",
        ):
            rows = _results(api.get(f"{CONV_URL}{qs}"))
            assert all(r["id"] == other_wa_client.id for r in rows), qs
            if qs == "?assignment=unassigned":
                assert rows == []

    def test_doctor_only_sees_assigned(
        self, company, subscription, wa_client, other_wa_client
    ):
        from accounts.models import User

        doctor = User.objects.create_user(
            username="doc_wa",
            email="doc_wa@test.com",
            password="testpass123",
            company=company,
            role="doctor",
        )
        other_wa_client.assigned_to = doctor
        other_wa_client.save(update_fields=["assigned_to"])
        _inbound(wa_client)
        _inbound(other_wa_client)

        api = APIClient()
        api.force_authenticate(user=doctor)
        rows = _results(api.get(CONV_URL))
        assert {r["id"] for r in rows} == {other_wa_client.id}


@pytest.mark.django_db
class TestEmployeeCanUpdateOwnThreadStatus:
    def test_employee_can_set_status_on_assigned(
        self, authenticated_employee, employee_user, other_wa_client
    ):
        _inbound(other_wa_client)
        resp = _set_state(authenticated_employee, other_wa_client.id, status="pending")
        assert resp.status_code == status.HTTP_200_OK
        assert api_body(resp)["status"] == "pending"
