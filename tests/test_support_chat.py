"""Tests for owner ↔ super-admin support chat."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Role
from companies.models import Company
from settings.models import SMTPSettings
from support_chat.models import SupportConversation, SupportMessage
from support_chat.services import conversations as chat_services

User = get_user_model()


@pytest.fixture
def smtp_settings_active(db):
    s = SMTPSettings.get_settings()
    s.is_active = True
    s.from_email = "noreply@example.com"
    s.from_name = "CRM"
    s.host = "unused"
    s.port = 587
    s.username = "unused"
    s.password = "unused"
    s.use_tls = True
    s.use_ssl = False
    s.save()
    return s


def _unwrap_data(resp):
    d = getattr(resp, "data", None) or {}
    if isinstance(d, dict) and d.get("success") is True and isinstance(d.get("data"), dict):
        return d["data"]
    return d


def _company_with_owner(domain_suffix="sc"):
    owner = User.objects.create_user(
        username=f"owner_{domain_suffix}",
        email=f"owner_{domain_suffix}@example.com",
        password="pass12345",
        role=Role.ADMIN.value,
    )
    company = Company.objects.create(
        name=f"Co {domain_suffix}",
        domain=f"{domain_suffix}.support.example.com",
        owner=owner,
    )
    owner.company = company
    owner.email_verified = True
    owner.phone_verified = True
    owner.save(update_fields=["company", "email_verified", "phone_verified"])
    return company, owner


def _employee(company, name="emp"):
    u = User.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="pass12345",
        role=Role.EMPLOYEE.value,
        company=company,
    )
    u.email_verified = True
    u.phone_verified = True
    u.save(update_fields=["email_verified", "phone_verified"])
    return u


def _super_admin():
    return User.objects.create_user(
        username="platform_admin",
        email="platform_admin@example.com",
        password="pass12345",
        is_superuser=True,
        is_staff=True,
    )


def _start_open_chat(conv, owner, admin, body="need help"):
    chat_services.send_message(conv, owner, SupportMessage.Side.TENANT, body=body)
    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.PENDING
    chat_services.approve(conv, admin)
    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.OPEN


@pytest.mark.django_db
def test_open_inbox_excludes_awaiting_reply():
    from support_chat.selectors import admin_inbox_queryset

    waiting_company, waiting_owner = _company_with_owner("awaiting")
    replied_company, replied_owner = _company_with_owner("replied")
    admin = _super_admin()
    waiting, _ = chat_services.get_or_create_for_company(waiting_company)
    replied, _ = chat_services.get_or_create_for_company(replied_company)
    chat_services.send_message(waiting, waiting_owner, SupportMessage.Side.TENANT, body="need help")
    _start_open_chat(replied, replied_owner, admin, body="q")
    chat_services.send_message(replied, admin, SupportMessage.Side.SUPPORT, body="on it")

    open_ids = set(admin_inbox_queryset(status_filter="open").values_list("id", flat=True))
    awaiting_ids = set(admin_inbox_queryset(status_filter="awaiting").values_list("id", flat=True))
    assert replied.id in open_ids
    assert waiting.id not in open_ids
    assert waiting.id in awaiting_ids
    assert replied.id not in awaiting_ids


@pytest.mark.django_db
def test_employee_cannot_access_support_chat():
    company, owner = _company_with_owner("emp403")
    emp = _employee(company)
    client = APIClient()
    client.force_authenticate(user=emp)
    r = client.get("/api/v1/support-chat/conversation/")
    assert r.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_owner_can_send_and_super_admin_replies():
    company, owner = _company_with_owner("flow1")
    admin = _super_admin()
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    admin_client = APIClient()
    admin_client.force_authenticate(user=admin)

    r = owner_client.post(
        "/api/v1/support-chat/messages/",
        {"body": "Hello support"},
        format="json",
    )
    assert r.status_code == status.HTTP_201_CREATED
    data = _unwrap_data(r)
    assert data["side"] == "tenant"
    assert data.get("sender") is None

    conv = SupportConversation.objects.get(company=company)
    assert conv.status == SupportConversation.Status.PENDING

    approve_r = admin_client.post(f"/api/v1/support-chat-admin/conversations/{conv.id}/approve/")
    assert approve_r.status_code == status.HTTP_200_OK

    list_r = admin_client.get("/api/v1/support-chat-admin/conversations/")
    assert list_r.status_code == status.HTTP_200_OK

    msg_r = admin_client.post(
        f"/api/v1/support-chat-admin/conversations/{conv.id}/messages/",
        {"body": "We are here to help"},
        format="json",
    )
    assert msg_r.status_code == status.HTTP_201_CREATED
    admin_data = _unwrap_data(msg_r)
    assert admin_data["display_name"] == "LOOP Support"

    tenant_msgs = owner_client.get("/api/v1/support-chat/messages/")
    results = _unwrap_data(tenant_msgs)["results"]
    support_msgs = [m for m in results if m["side"] == "support"]
    assert len(support_msgs) == 1
    assert support_msgs[0].get("sender") is None
    assert support_msgs[0]["display_name"] == "LOOP Support"


@pytest.mark.django_db
def test_request_cycle_pending_email_approve_resolve(smtp_settings_active):
    company, owner = _company_with_owner("cycle")
    admin = _super_admin()
    conv, _ = chat_services.get_or_create_for_company(company)
    assert conv.status == SupportConversation.Status.RESOLVED

    with patch("accounts.event_emails._send_event_email", return_value=True) as send_mock:
        chat_services.send_message(conv, owner, SupportMessage.Side.TENANT, body="first request")
        assert send_mock.call_count >= 1
        template = send_mock.call_args[0][2]
        assert template == "support_chat_new_request_admin"

    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.PENDING

    with pytest.raises(ValueError, match="awaiting approval"):
        chat_services.send_message(conv, owner, SupportMessage.Side.TENANT, body="second")

    with pytest.raises(ValueError, match="Approve"):
        chat_services.send_message(conv, admin, SupportMessage.Side.SUPPORT, body="hi")

    chat_services.approve(conv, admin)
    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.OPEN

    chat_services.resolve(conv, admin)
    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.RESOLVED
    system_msgs = conv.messages.filter(side=SupportMessage.Side.SYSTEM)
    assert system_msgs.count() == 1

    with patch("accounts.event_emails._send_event_email", return_value=True) as send_mock2:
        chat_services.send_message(conv, owner, SupportMessage.Side.TENANT, body="again")
        new_request_calls = [
            c for c in send_mock2.call_args_list if c[0][2] == "support_chat_new_request_admin"
        ]
        assert len(new_request_calls) >= 1
    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.PENDING


@pytest.mark.django_db
def test_admin_cannot_reply_on_resolved_without_new_request():
    company, owner = _company_with_owner("reopen-admin")
    admin = _super_admin()
    conv, _ = chat_services.get_or_create_for_company(company)
    _start_open_chat(conv, owner, admin, body="q")
    chat_services.resolve(conv, admin)
    conv.refresh_from_db()
    assert conv.status == SupportConversation.Status.RESOLVED

    with pytest.raises(ValueError, match="Approve"):
        chat_services.send_message(conv, admin, SupportMessage.Side.SUPPORT, body="still here")


@pytest.mark.django_db
def test_mark_read_and_unread_digest():
    company, owner = _company_with_owner("unread")
    admin = _super_admin()
    conv, _ = chat_services.get_or_create_for_company(company)
    _start_open_chat(conv, owner, admin, body="q")
    msg = chat_services.send_message(conv, admin, SupportMessage.Side.SUPPORT, body="hi")

    from sync.counts import support_chat_unread_for_user

    assert support_chat_unread_for_user(owner) == 1
    chat_services.mark_read(conv, SupportMessage.Side.TENANT, msg)
    assert support_chat_unread_for_user(owner) == 0


@pytest.mark.django_db
def test_email_command_idempotent():
    company, owner = _company_with_owner("email")
    admin = _super_admin()
    conv, _ = chat_services.get_or_create_for_company(company)
    _start_open_chat(conv, owner, admin, body="q")
    old = timezone.now() - timedelta(minutes=30)
    msg = SupportMessage.objects.create(
        conversation=conv,
        sender=admin,
        side=SupportMessage.Side.SUPPORT,
        body="delayed",
    )
    SupportMessage.objects.filter(pk=msg.pk).update(created_at=old)
    conv.last_message_at = old
    conv.last_message_side = SupportMessage.Side.SUPPORT
    conv.save()

    with patch("accounts.event_emails._send_event_email", return_value=True) as send_mock:
        from django.core.management import call_command

        call_command("send_support_chat_unread_emails")
        assert send_mock.call_count >= 1
        conv.refresh_from_db()
        assert conv.tenant_emailed_up_to_id == msg.id
        before = send_mock.call_count
        call_command("send_support_chat_unread_emails")
        assert send_mock.call_count == before


@pytest.mark.django_db
def test_bump_support_conversation_publishes_realtime():
    from unittest.mock import patch

    from django.test import override_settings

    from sync.version import bump_support_conversation

    settings = dict(
        REALTIME_ENABLED=True,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    )
    with override_settings(**settings):
        with patch("realtime.publish._send_payload") as send:
            bump_support_conversation(42)
    send.assert_called_once()
    payload = send.call_args.args[1]
    assert payload["type"] == "support_conversation.event"
    assert payload["scope"] == "support_conversation"
    assert payload["conversation"] == 42


@pytest.mark.django_db
def test_bump_support_inbox_publishes_realtime():
    from unittest.mock import patch

    from django.test import override_settings

    from sync.version import bump_support_inbox

    settings = dict(
        REALTIME_ENABLED=True,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    )
    with override_settings(**settings):
        with patch("realtime.publish._send") as send:
            bump_support_inbox()
    send.assert_called_once()
    assert send.call_args.args[0] == "support.inbox"
    assert send.call_args.args[1] == "support_inbox"


@pytest.mark.django_db
def test_non_superuser_cannot_access_admin_inbox():
    company, owner = _company_with_owner("admin403")
    client = APIClient()
    client.force_authenticate(user=owner)
    r = client.get("/api/v1/support-chat-admin/conversations/")
    assert r.status_code == status.HTTP_403_FORBIDDEN
