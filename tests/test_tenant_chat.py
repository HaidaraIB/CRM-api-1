"""Tests for tenant internal DM authorization and flows."""

import base64
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from django.test import override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Role, SupervisorPermission
from companies.models import Company
from subscriptions.models import BillingCycle, Plan, Subscription
from tenant_chat.authorization import can_chat, chat_role_bucket, supervisor_chat_is_active
from tenant_chat.models import ChatConversation, ChatConversationReadState, ChatMessage, ChatPinnedMessage

User = get_user_model()


def _unwrap_data(resp):
    d = getattr(resp, "data", None) or {}
    if isinstance(d, dict) and d.get("success") is True and isinstance(d.get("data"), dict):
        return d["data"]
    return d


def _company_with_subscription(domain_suffix="chat"):
    owner = User.objects.create_user(
        username=f"owner_{domain_suffix}",
        email=f"owner_{domain_suffix}@example.com",
        password="pass12345",
        role=Role.ADMIN.value,
    )
    company = Company.objects.create(
        name=f"Co {domain_suffix}",
        domain=f"{domain_suffix}.chat.example.com",
        owner=owner,
    )
    owner.company = company
    owner.email_verified = True
    owner.phone_verified = True
    owner.save(update_fields=["company", "email_verified", "phone_verified"])

    plan = Plan.objects.create(
        name=f"Plan {domain_suffix}",
        description="t",
        price_monthly=10,
        price_yearly=100,
    )
    now = timezone.now()
    Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )
    return company, owner


def _user(company, username, role, **extra):
    u = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pass12345",
        role=role,
        company=company,
        **extra,
    )
    u.email_verified = True
    u.phone_verified = True
    u.save(update_fields=["email_verified", "phone_verified"])
    return u


@pytest.mark.django_db
def test_can_chat_owner_to_employee():
    company, owner = _company_with_subscription("t1")
    emp = _user(company, "emp1", Role.EMPLOYEE.value)
    assert can_chat(owner, emp) is True
    assert can_chat(emp, owner) is True


@pytest.mark.django_db
def test_can_chat_employee_peers_denied():
    company, _owner = _company_with_subscription("t2")
    e1 = _user(company, "e1", Role.EMPLOYEE.value)
    e2 = _user(company, "e2", Role.EMPLOYEE.value)
    assert can_chat(e1, e2) is False


@pytest.mark.django_db
def test_can_chat_data_entry_peer_denied_same_as_employee():
    company, _owner = _company_with_subscription("t3")
    d1 = _user(company, "d1", Role.DATA_ENTRY.value)
    d2 = _user(company, "d2", Role.DATA_ENTRY.value)
    assert can_chat(d1, d2) is False


@pytest.mark.django_db
def test_can_chat_employee_and_data_entry_peer_denied():
    company, _owner = _company_with_subscription("t4")
    e = _user(company, "e3", Role.EMPLOYEE.value)
    d = _user(company, "d3", Role.DATA_ENTRY.value)
    assert can_chat(e, d) is False


@pytest.mark.django_db
def test_can_chat_supervisors():
    company, owner = _company_with_subscription("t5")
    s1 = _user(company, "s1", Role.SUPERVISOR.value)
    s2 = _user(company, "s2", Role.SUPERVISOR.value)
    SupervisorPermission.objects.create(user=s1, is_active=True)
    SupervisorPermission.objects.create(user=s2, is_active=True)
    assert can_chat(s1, s2) is True
    assert can_chat(owner, s1) is True


@pytest.mark.django_db
def test_inactive_supervisor_ineligible():
    company, _owner = _company_with_subscription("t6")
    sup = _user(company, "s_inactive", Role.SUPERVISOR.value)
    SupervisorPermission.objects.create(user=sup, is_active=False)
    assert chat_role_bucket(sup) == "ineligible"
    assert supervisor_chat_is_active(sup) is False


@pytest.mark.django_db
def test_api_create_conversation_and_message():
    company, owner = _company_with_subscription("t7")
    emp = _user(company, "emp_api", Role.EMPLOYEE.value)

    api_client = APIClient()
    api_client.force_authenticate(user=owner)
    url = reverse("tenant_chat_conversation-list")
    r = api_client.post(url, {"with_user_id": emp.id}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    conv_id = r.data["id"]

    msg_url = reverse(
        "tenant_chat_conversation-messages",
        kwargs={"pk": conv_id},
    )
    r2 = api_client.post(msg_url, {"body": "Hello team"}, format="json")
    assert r2.status_code == status.HTTP_201_CREATED
    assert r2.data["body"] == "Hello team"

    r3 = api_client.get(msg_url)
    assert r3.status_code == status.HTTP_200_OK
    assert len(r3.data["results"]) >= 1

    conv = ChatConversation.objects.get(pk=conv_id)
    assert ChatMessage.objects.filter(conversation=conv).count() == 1


@pytest.mark.django_db
def test_descending_first_page_is_the_thread_tail():
    """
    How a client opens a long thread in one request.

    Unanchored ascending paging starts at the *beginning* of the conversation, so
    a client that asked for one page of a 250-message thread got its oldest 100
    and never saw the messages being sent. Descending order makes page 1 the tail,
    and `next` is what tells the client history exists behind it.
    """
    company, owner = _company_with_subscription("t_tail")
    emp = _user(company, "emp_tail", Role.EMPLOYEE.value)

    api_client = APIClient()
    api_client.force_authenticate(user=owner)
    conv_id = api_client.post(
        reverse("tenant_chat_conversation-list"),
        {"with_user_id": emp.id},
        format="json",
    ).data["id"]

    conversation = ChatConversation.objects.get(pk=conv_id)
    created = [
        ChatMessage.objects.create(conversation=conversation, sender=owner, body=f"m{i}")
        for i in range(25)
    ]
    newest_ten = [m.id for m in created[-10:]]

    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    r = api_client.get(msg_url, {"ordering": "-created_at", "page_size": 10})
    assert r.status_code == status.HTTP_200_OK
    returned = [row["id"] for row in r.data["results"]]

    assert sorted(returned) == sorted(newest_ten)
    # Descending, so the client sorts back into reading order itself.
    assert returned == sorted(newest_ten, reverse=True)
    # There is more behind the tail, which is how the client knows to offer it.
    assert r.data["next"]

    # And the older window the client asks for next, anchored on what it holds.
    older = api_client.get(
        msg_url,
        {"ordering": "created_at", "page_size": 10, "before_id": min(newest_ten)},
    )
    assert older.status_code == status.HTTP_200_OK
    older_ids = [row["id"] for row in older.data["results"]]
    assert older_ids == [m.id for m in created[5:15]]
    assert older.data["has_older"] is True


@pytest.mark.django_db
def test_employee_cannot_start_chat_with_peer_via_api():
    company, _owner = _company_with_subscription("t8")
    e1 = _user(company, "ea", Role.EMPLOYEE.value)
    e2 = _user(company, "eb", Role.EMPLOYEE.value)

    api_client = APIClient()
    api_client.force_authenticate(user=e1)
    url = reverse("tenant_chat_conversation-list")
    r = api_client.post(url, {"with_user_id": e2.id}, format="json")
    assert r.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_unread_count_and_mark_read():
    company, owner = _company_with_subscription("t_unread")
    emp = _user(company, "emp_unread", Role.EMPLOYEE.value)

    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)

    url = reverse("tenant_chat_conversation-list")
    r = owner_client.post(url, {"with_user_id": emp.id}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    conv_id = r.data["id"]

    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    r_msg = owner_client.post(msg_url, {"body": "Hello emp"}, format="json")
    assert r_msg.status_code == status.HTTP_201_CREATED
    mid = r_msg.data["id"]

    r_list = owner_client.get(url)
    assert r_list.status_code == status.HTTP_200_OK
    row = next(x for x in r_list.data["results"] if x["id"] == conv_id)
    assert row["unread_count"] == 0

    r_list_emp = emp_client.get(url)
    row_emp = next(x for x in r_list_emp.data["results"] if x["id"] == conv_id)
    assert row_emp["unread_count"] == 1
    assert row_emp.get("last_read_message_id") in (None, 0)

    read_url = reverse("tenant_chat_conversation-mark-read", kwargs={"pk": conv_id})
    r_read = emp_client.post(read_url, {"message_id": mid}, format="json")
    assert r_read.status_code == status.HTTP_200_OK
    assert r_read.data["last_read_message_id"] == mid

    r_list_emp2 = emp_client.get(url)
    row_emp2 = next(x for x in r_list_emp2.data["results"] if x["id"] == conv_id)
    assert row_emp2["unread_count"] == 0
    assert row_emp2.get("last_read_message_id") == mid

    assert ChatConversationReadState.objects.filter(conversation_id=conv_id, user=emp).exists()


@pytest.mark.django_db
def test_message_read_by_peer_flag():
    company, owner = _company_with_subscription("t_peer_read")
    emp = _user(company, "emp_read_flag", Role.EMPLOYEE.value)

    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)

    url = reverse("tenant_chat_conversation-list")
    r = owner_client.post(url, {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    r_msg = owner_client.post(msg_url, {"body": "Seen?"}, format="json")
    mid = r_msg.data["id"]
    assert r_msg.data.get("read_by_peer") is False

    read_url = reverse("tenant_chat_conversation-mark-read", kwargs={"pk": conv_id})
    emp_client.post(read_url, {"message_id": mid}, format="json")

    r_get = owner_client.get(msg_url + "?ordering=created_at")
    owner_rows = r_get.data["results"]
    found = next(x for x in owner_rows if x["id"] == mid)
    assert found["read_by_peer"] is True


@pytest.mark.django_db
def test_reply_forward_and_pin_messages():
    company, owner = _company_with_subscription("t_rfp")
    sup = _user(company, "sup_rfp", Role.SUPERVISOR.value)
    SupervisorPermission.objects.create(user=sup, is_active=True)

    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    sup_client = APIClient()
    sup_client.force_authenticate(user=sup)

    url = reverse("tenant_chat_conversation-list")
    r1 = owner_client.post(url, {"with_user_id": sup.id}, format="json")
    conv_a_id = r1.data["id"]
    r2 = owner_client.post(url, {"with_user_id": sup.id}, format="json")
    assert r2.data["id"] == conv_a_id

    r_sup_conv = sup_client.post(url, {"with_user_id": owner.id}, format="json")
    conv_b_id = r_sup_conv.data["id"]
    assert conv_b_id == conv_a_id

    msg_url_owner = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_a_id})
    origin = owner_client.post(msg_url_owner, {"body": "Original line"}, format="json")
    assert origin.status_code == status.HTTP_201_CREATED
    oid = origin.data["id"]

    reply = owner_client.post(
        msg_url_owner,
        {"body": "Reply text", "reply_to_message_id": oid},
        format="json",
    )
    assert reply.status_code == status.HTTP_201_CREATED
    assert reply.data["reply_to"]["id"] == oid

    forward = owner_client.post(
        msg_url_owner,
        {"body": "See this", "forward_from_message_id": oid},
        format="json",
    )
    assert forward.status_code == status.HTTP_201_CREATED
    assert forward.data["forwarded_from"]["id"] == oid

    solo = _user(company, "solo_emp_rfp", Role.EMPLOYEE.value)
    other_conv = owner_client.post(url, {"with_user_id": solo.id}, format="json")
    conv_other_id = other_conv.data["id"]
    fwd_out = owner_client.post(
        reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_other_id}),
        {"body": "", "forward_from_message_id": oid},
        format="json",
    )
    assert fwd_out.status_code == status.HTTP_201_CREATED
    assert fwd_out.data["forwarded_from"]["id"] == oid

    pin_url = reverse("tenant_chat_conversation-pin-message", kwargs={"pk": conv_a_id})
    r_pin = owner_client.post(pin_url, {"message_id": oid}, format="json")
    assert r_pin.status_code == status.HTTP_200_OK
    assert ChatPinnedMessage.objects.filter(conversation_id=conv_a_id, message_id=oid).exists()

    r_list = owner_client.get(url)
    row = next(x for x in r_list.data["results"] if x["id"] == conv_a_id)
    assert len(row["pinned_messages"]) >= 1

    unpin_url = reverse("tenant_chat_conversation-unpin-message", kwargs={"pk": conv_a_id})
    r_unpin = owner_client.post(unpin_url, {"message_id": oid}, format="json")
    assert r_unpin.status_code == status.HTTP_200_OK
    assert not ChatPinnedMessage.objects.filter(conversation_id=conv_a_id, message_id=oid).exists()


MINI_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.django_db
def test_multipart_image_message_attachment_url_and_download():
    company, owner = _company_with_subscription("t_img")
    emp = _user(company, "emp_img", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)
    url = reverse("tenant_chat_conversation-list")
    r = owner_client.post(url, {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    r2 = owner_client.post(msg_url, {"body": "hello pic", "file": up}, format="multipart")
    assert r2.status_code == status.HTTP_201_CREATED
    assert r2.data.get("attachment_kind") == "image"
    assert r2.data.get("attachment_url")
    assert r2.data.get("body") == "hello pic"
    att_path = urlparse(r2.data["attachment_url"]).path
    dl = emp_client.get(att_path)
    assert dl.status_code == status.HTTP_200_OK
    assert "image" in (dl.get("Content-Type") or "")


@pytest.mark.django_db
def test_attachment_download_forbidden_non_participant():
    company, owner = _company_with_subscription("t_att403")
    emp = _user(company, "emp_att403", Role.EMPLOYEE.value)
    other = _user(company, "other_admin_att403", Role.ADMIN.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    r2 = owner_client.post(msg_url, {"body": "x", "file": up}, format="multipart")
    mid = r2.data["id"]
    att_path = urlparse(r2.data["attachment_url"]).path
    other_client = APIClient()
    other_client.force_authenticate(user=other)
    assert other_client.get(att_path).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_multipart_file_too_large_rejected():
    company, owner = _company_with_subscription("t_big")
    emp = _user(company, "emp_big", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    big = b"\x00" + (b"x" * (9 * 1024 * 1024))
    up = SimpleUploadedFile("big.png", big, content_type="image/png")
    r2 = owner_client.post(msg_url, {"body": "nope", "file": up}, format="multipart")
    assert r2.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_multipart_invalid_type_rejected():
    company, owner = _company_with_subscription("t_bad")
    emp = _user(company, "emp_bad", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    up = SimpleUploadedFile("evil.exe", b"MZ\x00\x00", content_type="application/octet-stream")
    r2 = owner_client.post(msg_url, {"body": "nope", "file": up}, format="multipart")
    assert r2.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_cannot_combine_forward_with_file_upload():
    company, owner = _company_with_subscription("t_ffile")
    emp = _user(company, "emp_ffile", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    origin = owner_client.post(msg_url, {"body": "orig"}, format="json")
    oid = origin.data["id"]
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    r2 = owner_client.post(
        msg_url,
        {"body": "", "forward_from_message_id": oid, "file": up},
        format="multipart",
    )
    assert r2.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_reply_with_attachment_only_empty_body():
    company, owner = _company_with_subscription("t_reply_att")
    emp = _user(company, "emp_reply_att", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    first = owner_client.post(msg_url, {"body": "thread start"}, format="json")
    rid = first.data["id"]
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    r2 = owner_client.post(
        msg_url,
        {"reply_to_message_id": rid, "file": up},
        format="multipart",
    )
    assert r2.status_code == status.HTTP_201_CREATED
    assert r2.data.get("reply_to", {}).get("id") == rid
    assert r2.data.get("attachment_kind") == "image"


@pytest.mark.django_db
def test_forward_copies_attachment_to_new_message():
    company, owner = _company_with_subscription("t_fwdcopy")
    emp = _user(company, "emp_fwdcopy", Role.EMPLOYEE.value)
    solo = _user(company, "solo_fwdcopy", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    url = reverse("tenant_chat_conversation-list")
    r1 = owner_client.post(url, {"with_user_id": emp.id}, format="json")
    conv_a = r1.data["id"]
    msg_url_a = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_a})
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    origin = owner_client.post(msg_url_a, {"body": "pic", "file": up}, format="multipart")
    assert origin.status_code == status.HTTP_201_CREATED
    oid = origin.data["id"]
    conv_b = owner_client.post(url, {"with_user_id": solo.id}, format="json").data["id"]
    msg_url_b = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_b})
    fwd = owner_client.post(
        msg_url_b,
        {"body": "look", "forward_from_message_id": oid},
        format="json",
    )
    assert fwd.status_code == status.HTTP_201_CREATED
    assert fwd.data.get("attachment_kind") == "image"
    assert fwd.data.get("attachment_url")
    assert fwd.data["forwarded_from"]["id"] == oid


@pytest.mark.django_db
@override_settings(
    TENANT_CHAT_STORAGE="supabase",
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY="test-service-role",
    SUPABASE_CHAT_BUCKET="tenant-chat",
)
@patch("tenant_chat.supabase_storage.upload_bytes")
def test_supabase_mode_multipart_sets_object_key(mock_upload):
    company, owner = _company_with_subscription("t_sb_up")
    emp = _user(company, "emp_sb_up", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv_id = r.data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    r2 = owner_client.post(msg_url, {"body": "sb", "file": up}, format="multipart")
    assert r2.status_code == status.HTTP_201_CREATED
    mock_upload.assert_called_once()
    args, _kwargs = mock_upload.call_args
    assert args[0].startswith(f"company_{company.id}/")
    assert r2.data.get("attachment_url")
    msg = ChatMessage.objects.get(pk=r2.data["id"])
    assert msg.attachment_object_key == args[0]
    assert not (msg.attachment and getattr(msg.attachment, "name", None))


@pytest.mark.django_db
@override_settings(
    TENANT_CHAT_STORAGE="supabase",
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY="test-service-role",
    SUPABASE_CHAT_BUCKET="tenant-chat",
)
@patch("tenant_chat.supabase_storage.upload_bytes")
@patch("tenant_chat.supabase_storage.download_bytes", return_value=MINI_PNG_BYTES)
def test_supabase_mode_forward_copies_via_storage(mock_download, mock_upload):
    company, owner = _company_with_subscription("t_sb_fwd")
    emp = _user(company, "emp_sb_fwd", Role.EMPLOYEE.value)
    solo = _user(company, "solo_sb_fwd", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    url = reverse("tenant_chat_conversation-list")
    r1 = owner_client.post(url, {"with_user_id": emp.id}, format="json")
    conv_a = r1.data["id"]
    msg_url_a = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_a})
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    origin = owner_client.post(msg_url_a, {"body": "pic", "file": up}, format="multipart")
    assert origin.status_code == status.HTTP_201_CREATED
    oid = origin.data["id"]
    conv_b = owner_client.post(url, {"with_user_id": solo.id}, format="json").data["id"]
    msg_url_b = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_b})
    fwd = owner_client.post(
        msg_url_b,
        {"body": "look", "forward_from_message_id": oid},
        format="json",
    )
    assert fwd.status_code == status.HTTP_201_CREATED
    assert mock_download.call_count == 1
    assert mock_upload.call_count == 2
    assert fwd.data.get("attachment_url")


@pytest.mark.django_db
@override_settings(
    TENANT_CHAT_STORAGE="supabase",
    SUPABASE_URL="",
    SUPABASE_SERVICE_ROLE_KEY="",
    SUPABASE_CHAT_BUCKET="tenant-chat",
)
def test_supabase_misconfigured_rejects_file_upload():
    company, owner = _company_with_subscription("t_sb_bad")
    emp = _user(company, "emp_sb_bad", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": r.data["id"]})
    up = SimpleUploadedFile("tiny.png", MINI_PNG_BYTES, content_type="image/png")
    r2 = owner_client.post(msg_url, {"body": "x", "file": up}, format="multipart")
    assert r2.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.django_db
@override_settings(
    TENANT_CHAT_STORAGE="supabase",
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY="test-service-role",
    SUPABASE_CHAT_BUCKET="tenant-chat",
)
@patch("tenant_chat.supabase_storage.create_signed_url", return_value="https://signed.example/file?token=1")
def test_supabase_attachment_get_redirects(mock_sign):
    company, owner = _company_with_subscription("t_sb_redir")
    emp = _user(company, "emp_sb_redir", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)
    r = owner_client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json")
    conv = ChatConversation.objects.get(pk=r.data["id"])
    msg = ChatMessage.objects.create(
        conversation=conv,
        sender=owner,
        body="x",
        attachment_kind="image",
        attachment_mime="image/png",
        attachment_size=len(MINI_PNG_BYTES),
        attachment_object_key=f"company_{company.id}/m0_test.png",
    )
    att_url = reverse("tenant_chat_message_attachment", kwargs={"pk": msg.id})
    dl = emp_client.get(att_url)
    assert dl.status_code == status.HTTP_302_FOUND
    assert dl["Location"] == "https://signed.example/file?token=1"
    mock_sign.assert_called_once()


@pytest.mark.django_db
def test_peer_presence_post_and_get_between_participants():
    company, owner = _company_with_subscription("t_pres")
    emp = _user(company, "emp_pres", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)
    conv_id = owner_client.post(
        reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json"
    ).data["id"]
    pres = reverse("tenant_chat_conversation-peer-presence", kwargs={"pk": conv_id})
    assert owner_client.post(pres, {"action": "typing"}, format="json").status_code == status.HTTP_200_OK
    got = _unwrap_data(emp_client.get(pres))
    assert got.get("peer_user_id") == owner.id
    assert got.get("activity") == "typing"
    assert owner_client.post(pres, {"action": "idle"}, format="json").status_code == status.HTTP_200_OK
    cleared = _unwrap_data(emp_client.get(pres))
    assert cleared.get("activity") is None


@pytest.mark.django_db
def test_peer_presence_invalid_action():
    company, owner = _company_with_subscription("t_pres_bad")
    emp = _user(company, "emp_pres_bad", Role.EMPLOYEE.value)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    conv_id = owner_client.post(
        reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json"
    ).data["id"]
    pres = reverse("tenant_chat_conversation-peer-presence", kwargs={"pk": conv_id})
    r = owner_client.post(pres, {"action": "nope"}, format="json")
    assert r.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_company_group_in_list_and_metadata():
    company, owner = _company_with_subscription("t_cg_meta")
    _user(company, "emp_cg_meta", Role.EMPLOYEE.value)
    cg = ChatConversation.objects.get(company=company, kind=ChatConversation.Kind.COMPANY_GROUP)
    client = APIClient()
    client.force_authenticate(user=owner)
    r = client.get(reverse("tenant_chat_conversation-list"))
    assert r.status_code == status.HTTP_200_OK
    row = next(x for x in r.data["results"] if x["id"] == cg.id)
    assert row["kind"] == "company_group"
    assert row["other_user"] is None
    assert row.get("group_title") == company.name
    assert (row.get("member_count") or 0) >= 2


@pytest.mark.django_db
def test_employee_can_post_to_company_group():
    company, owner = _company_with_subscription("t_cg_post")
    emp = _user(company, "emp_cg_post", Role.EMPLOYEE.value)
    cg = ChatConversation.objects.get(company=company, kind=ChatConversation.Kind.COMPANY_GROUP)
    api = APIClient()
    api.force_authenticate(user=emp)
    url = reverse("tenant_chat_conversation-messages", kwargs={"pk": cg.id})
    r = api.post(url, {"body": "hello room"}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["body"] == "hello room"
    assert r.data.get("read_by_peer") is False


@pytest.mark.django_db
@patch("tenant_chat.views._notify_recipient_chat_message")
def test_company_group_notify_fanout(mock_notify):
    company, owner = _company_with_subscription("t_cg_fan")
    _user(company, "e_cg_f1", Role.EMPLOYEE.value)
    _user(company, "e_cg_f2", Role.EMPLOYEE.value)
    cg = ChatConversation.objects.get(company=company, kind=ChatConversation.Kind.COMPANY_GROUP)
    api = APIClient()
    api.force_authenticate(user=owner)
    url = reverse("tenant_chat_conversation-messages", kwargs={"pk": cg.id})
    r = api.post(url, {"body": "all"}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert mock_notify.call_count == 2


@pytest.mark.django_db
def test_company_group_peer_presence():
    company, owner = _company_with_subscription("t_cg_pres")
    emp = _user(company, "emp_cg_pres", Role.EMPLOYEE.value)
    cg = ChatConversation.objects.get(company=company, kind=ChatConversation.Kind.COMPANY_GROUP)
    oc = APIClient()
    oc.force_authenticate(user=owner)
    ec = APIClient()
    ec.force_authenticate(user=emp)
    pres = reverse("tenant_chat_conversation-peer-presence", kwargs={"pk": cg.id})
    assert oc.post(pres, {"action": "typing"}, format="json").status_code == status.HTTP_200_OK
    got = ec.get(pres).data
    assert got.get("mode") == "group"
    peers = got.get("peers") or []
    assert any(p["user_id"] == owner.id and p["activity"] == "typing" for p in peers)


@pytest.mark.django_db
def test_inactive_supervisor_does_not_see_company_group():
    company, _owner = _company_with_subscription("t_cg_inact")
    sup = _user(company, "sup_inact_cg", Role.SUPERVISOR.value)
    SupervisorPermission.objects.create(user=sup, is_active=False)
    ChatConversation.objects.get(company=company, kind=ChatConversation.Kind.COMPANY_GROUP)
    api = APIClient()
    api.force_authenticate(user=sup)
    r = api.get(reverse("tenant_chat_conversation-list"))
    assert r.status_code == status.HTTP_200_OK
    kinds = [x.get("kind") for x in r.data["results"]]
    assert "company_group" not in kinds



@pytest.mark.django_db
def test_conversation_list_query_count_does_not_grow_with_threads():
    """
    The conversation list is polled continuously by every open chat pane, so its
    cost must not scale with how many threads a user is in.

    Each row used to cost 4 queries (last message, read state twice, unread
    count), which put a user in 15 threads at ~60 queries per poll. The list now
    resolves those in bulk, so adding threads must not add queries.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    company, owner = _company_with_subscription("t_qcount")
    url = reverse("tenant_chat_conversation-list")
    client = APIClient()
    client.force_authenticate(user=owner)

    def add_threads(count, tag):
        for i in range(count):
            emp = _user(company, f"emp_{tag}_{i}", Role.EMPLOYEE.value)
            r = client.post(url, {"with_user_id": emp.id}, format="json")
            assert r.status_code == status.HTTP_201_CREATED
            conv_id = r.data["id"]
            # A message from the peer so last_message and unread_count both have
            # real work to do rather than short-circuiting on an empty thread.
            peer = APIClient()
            peer.force_authenticate(user=emp)
            msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
            assert peer.post(msg_url, {"body": f"hi {i}"}, format="json").status_code == (
                status.HTTP_201_CREATED
            )

    def list_query_count():
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(url).status_code == status.HTTP_200_OK
        return len(ctx)

    add_threads(2, "few")
    list_query_count()  # warm the subscription/permission caches
    baseline = list_query_count()

    add_threads(6, "many")
    grown = list_query_count()

    assert grown == baseline, (
        f"conversation list cost grew with thread count: {baseline} -> {grown} queries"
    )


@pytest.mark.django_db
def test_messages_conditional_get_returns_304_without_requerying():
    """An unchanged thread is answered from the version counter, not re-serialized."""
    company, owner = _company_with_subscription("t_msg_etag")
    emp = _user(company, "emp_msg_etag", Role.EMPLOYEE.value)

    client = APIClient()
    client.force_authenticate(user=owner)
    url = reverse("tenant_chat_conversation-list")
    conv_id = client.post(url, {"with_user_id": emp.id}, format="json").data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    client.post(msg_url, {"body": "first"}, format="json")

    first = client.get(msg_url)
    assert first.status_code == status.HTTP_200_OK
    etag = first["ETag"]
    assert etag

    second = client.get(msg_url, HTTP_IF_NONE_MATCH=etag)
    assert second.status_code == status.HTTP_304_NOT_MODIFIED
    assert second.content == b""


@pytest.mark.django_db
def test_messages_etag_changes_on_new_message_and_on_peer_read():
    """
    Both things that alter the rendered thread must break the ETag.

    The second half is the subtle one: a peer marking the thread read changes the
    delivery ticks without adding a message, so the thread counter has to move on
    read-state writes too or receipts would stall.
    """
    company, owner = _company_with_subscription("t_msg_etag2")
    emp = _user(company, "emp_msg_etag2", Role.EMPLOYEE.value)

    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)

    url = reverse("tenant_chat_conversation-list")
    conv_id = owner_client.post(url, {"with_user_id": emp.id}, format="json").data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    mid = owner_client.post(msg_url, {"body": "hello"}, format="json").data["id"]

    etag = owner_client.get(msg_url)["ETag"]

    # A new message breaks it.
    emp_client.post(msg_url, {"body": "reply"}, format="json")
    after_message = owner_client.get(msg_url, HTTP_IF_NONE_MATCH=etag)
    assert after_message.status_code == status.HTTP_200_OK
    etag = after_message["ETag"]

    # So does the peer reading it.
    read_url = reverse("tenant_chat_conversation-mark-read", kwargs={"pk": conv_id})
    assert emp_client.post(read_url, {"message_id": mid}, format="json").status_code == (
        status.HTTP_200_OK
    )
    after_read = owner_client.get(msg_url, HTTP_IF_NONE_MATCH=etag)
    assert after_read.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_messages_etag_is_per_user_and_per_params():
    """A token must not be honoured for a different viewer or a different page."""
    company, owner = _company_with_subscription("t_msg_etag3")
    emp = _user(company, "emp_msg_etag3", Role.EMPLOYEE.value)

    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)

    url = reverse("tenant_chat_conversation-list")
    conv_id = owner_client.post(url, {"with_user_id": emp.id}, format="json").data["id"]
    msg_url = reverse("tenant_chat_conversation-messages", kwargs={"pk": conv_id})
    owner_client.post(msg_url, {"body": "hi"}, format="json")

    etag = owner_client.get(msg_url)["ETag"]

    # Different viewer: read receipts differ, so the token must not match.
    assert emp_client.get(msg_url, HTTP_IF_NONE_MATCH=etag).status_code == status.HTTP_200_OK
    # Different paging: a different slice of the thread.
    assert owner_client.get(
        msg_url, {"page_size": 10}, HTTP_IF_NONE_MATCH=etag
    ).status_code == status.HTTP_200_OK
