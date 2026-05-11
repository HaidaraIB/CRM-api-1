"""Tests for tenant internal DM authorization and flows."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
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

    read_url = reverse("tenant_chat_conversation-mark-read", kwargs={"pk": conv_id})
    r_read = emp_client.post(read_url, {"message_id": mid}, format="json")
    assert r_read.status_code == status.HTTP_200_OK
    assert r_read.data["last_read_message_id"] == mid

    r_list_emp2 = emp_client.get(url)
    row_emp2 = next(x for x in r_list_emp2.data["results"] if x["id"] == conv_id)
    assert row_emp2["unread_count"] == 0

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

