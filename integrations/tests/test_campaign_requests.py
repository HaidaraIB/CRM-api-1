"""
Messaging Center campaign-request approval workflow: restricted staff (every
role except Owner/Supervisor) may only submit bulk-send requests for their own
assigned leads, and the owner must approve before anything sends.
"""
from unittest.mock import patch

import pytest
from django.urls import reverse

from conftest import api_body
from crm.models import Client
from integrations.models import (
    CampaignBatchStatus,
    MessageCampaignBatch,
    MessageCampaignFailure,
    MessageTemplate,
)


@pytest.fixture
def own_lead(company, employee_user, db):
    return Client.objects.create(
        company=company, name="Own Lead", assigned_to=employee_user, phone_number="9647700000001",
    )


@pytest.fixture
def other_lead(company, admin_user, db):
    return Client.objects.create(
        company=company, name="Other Lead", assigned_to=admin_user, phone_number="9647700000002",
    )


def _submit_payload(lead):
    return {
        "channel": "sms",
        "message_preview": "Hello there",
        "recipients": [{"client_id": lead.id, "phone_number": lead.phone_number}],
        "message_payload": {"body": "Hello [first_name]"},
    }


@pytest.mark.django_db
def test_employee_can_submit_request_for_own_leads(authenticated_employee, own_lead):
    res = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    assert res.status_code == 201
    data = api_body(res)
    assert data["status"] == CampaignBatchStatus.PENDING_APPROVAL


@pytest.mark.django_db
def test_submitted_request_exposes_prefill_payload(authenticated_employee, own_lead):
    """Edit & resubmit rebuilds the compose form from these fields."""
    res = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    data = api_body(res)
    assert data["message_payload"] == {"body": "Hello [first_name]"}
    assert data["audience_client_ids"] == [own_lead.id]
    assert data["audience_preview"] == [{"client_id": own_lead.id, "name": own_lead.name}]
    assert data["template_name"] is None  # SMS campaign


@pytest.mark.django_db
def test_employee_cannot_submit_request_for_others_leads(authenticated_employee, other_lead):
    res = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(other_lead), format="json"
    )
    assert res.status_code == 403


@pytest.mark.django_db
def test_employee_cannot_approve_own_request(authenticated_employee, own_lead):
    submit = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    batch_id = api_body(submit)["id"]
    res = authenticated_employee.patch(reverse("campaign_requests_approve", args=[batch_id]), {}, format="json")
    assert res.status_code == 403


@pytest.mark.django_db
def test_employee_cannot_list_pending_queue(authenticated_employee):
    res = authenticated_employee.get(reverse("campaign_requests_pending"))
    assert res.status_code == 403


@pytest.mark.django_db
def test_owner_can_approve_request_and_task_is_enqueued(authenticated_employee, authenticated_admin, own_lead):
    submit = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    batch_id = api_body(submit)["id"]

    with patch("integrations.tasks.enqueue_campaign_batch_send") as mock_enqueue:
        res = authenticated_admin.patch(reverse("campaign_requests_approve", args=[batch_id]), {}, format="json")
        assert res.status_code == 200
        assert api_body(res)["status"] == CampaignBatchStatus.APPROVED
        mock_enqueue.assert_called_once_with(batch_id)


@pytest.mark.django_db
def test_owner_can_reject_request_with_reason(authenticated_employee, authenticated_admin, own_lead):
    submit = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    batch_id = api_body(submit)["id"]

    res = authenticated_admin.patch(
        reverse("campaign_requests_reject", args=[batch_id]), {"reason": "Not now"}, format="json"
    )
    assert res.status_code == 200
    data = api_body(res)
    assert data["status"] == CampaignBatchStatus.REJECTED
    assert data["rejection_reason"] == "Not now"


@pytest.mark.django_db
def test_reject_without_reason_is_rejected_with_400(authenticated_employee, authenticated_admin, own_lead):
    submit = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    batch_id = api_body(submit)["id"]

    res = authenticated_admin.patch(reverse("campaign_requests_reject", args=[batch_id]), {}, format="json")
    assert res.status_code == 400


@pytest.mark.django_db
def test_employee_can_resubmit_after_rejection_same_row(authenticated_employee, authenticated_admin, own_lead):
    submit = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    batch_id = api_body(submit)["id"]
    authenticated_admin.patch(reverse("campaign_requests_reject", args=[batch_id]), {"reason": "x"}, format="json")

    res = authenticated_employee.patch(
        reverse("campaign_requests_resubmit", args=[batch_id]), _submit_payload(own_lead), format="json"
    )
    assert res.status_code == 200
    data = api_body(res)
    assert data["id"] == batch_id
    assert data["status"] == CampaignBatchStatus.PENDING_APPROVAL
    assert data["rejection_reason"] == ""


@pytest.mark.django_db
def test_other_employee_cannot_resubmit_someone_elses_rejected_request(
    authenticated_employee, authenticated_admin, own_lead, company, db,
):
    from accounts.models import User

    submit = authenticated_employee.post(
        reverse("campaign_requests_list_create"), _submit_payload(own_lead), format="json"
    )
    batch_id = api_body(submit)["id"]
    authenticated_admin.patch(reverse("campaign_requests_reject", args=[batch_id]), {"reason": "x"}, format="json")

    other = User.objects.create_user(
        username="other_employee", email="other_employee@test.com", password="testpass123",
        company=company, role="employee",
    )
    from rest_framework.test import APIClient

    other_client = APIClient()
    other_client.force_authenticate(user=other)
    res = other_client.patch(
        reverse("campaign_requests_resubmit", args=[batch_id]), _submit_payload(own_lead), format="json"
    )
    assert res.status_code == 404


@pytest.mark.django_db
def test_owner_instant_send_flow_unaffected(authenticated_admin):
    res = authenticated_admin.post(reverse("campaign_batches_create"), {"channel": "whatsapp"}, format="json")
    assert res.status_code in (200, 201)
    batch = MessageCampaignBatch.objects.get(id=api_body(res)["id"])
    assert batch.status == CampaignBatchStatus.SENT
    assert batch.requires_approval is False


@pytest.mark.django_db
def test_send_campaign_batch_task_updates_counts_and_failures(company, employee_user, own_lead, other_lead, db):
    from integrations.tasks import send_campaign_batch_task
    from integrations.models import TwilioSettings

    TwilioSettings.objects.create(company=company, is_enabled=True, account_sid="AC1", twilio_number="+10000000000")
    batch = MessageCampaignBatch.objects.create(
        company=company,
        channel=MessageCampaignBatch.CHANNEL_SMS,
        requested_by=employee_user,
        created_by=employee_user,
        requires_approval=True,
        status=CampaignBatchStatus.APPROVED,
        recipient_count=2,
        audience_snapshot=[
            {"client_id": own_lead.id, "phone_number": own_lead.phone_number, "name": own_lead.name},
            {"client_id": 999999, "phone_number": "9647700000099", "name": "Ghost"},
        ],
        message_payload={"body": "Hi [first_name]"},
    )

    def fake_send_company_sms(settings, *, to_phone, body):
        if to_phone == own_lead.phone_number:
            return True, "SM123", None, None, "twilio"
        return False, None, "sms_error_send_failed", "boom", "twilio"

    with patch("integrations.tasks.send_company_sms", side_effect=fake_send_company_sms):
        with patch("integrations.tasks.notify_campaign_batch_complete"):
            send_campaign_batch_task(batch.id)

    batch.refresh_from_db()
    assert batch.status == CampaignBatchStatus.COMPLETED
    assert batch.sent_count == 1
    assert batch.failed_count == 1
    assert MessageCampaignFailure.objects.filter(batch=batch).count() == 1


def _sms_batch(company, employee_user, leads, **kwargs):
    audience = [
        {"client_id": lead.id, "phone_number": lead.phone_number, "name": lead.name}
        for lead in leads
    ]
    defaults = {
        "company": company,
        "channel": MessageCampaignBatch.CHANNEL_SMS,
        "requested_by": employee_user,
        "created_by": employee_user,
        "requires_approval": True,
        "status": CampaignBatchStatus.APPROVED,
        "recipient_count": len(audience),
        "audience_snapshot": audience,
        "message_payload": {"body": "Hi"},
    }
    defaults.update(kwargs)
    return MessageCampaignBatch.objects.create(**defaults)


@pytest.mark.django_db
def test_campaign_batch_chunks_when_time_budget_exceeded(company, employee_user, own_lead, other_lead, db):
    from integrations.models import TwilioSettings
    from integrations.tasks import send_campaign_batch_task

    TwilioSettings.objects.create(company=company, is_enabled=True, account_sid="AC1", twilio_number="+10000000000")
    third = Client.objects.create(
        company=company, name="Third", assigned_to=employee_user, phone_number="9647700000003",
    )
    batch = _sms_batch(company, employee_user, [own_lead, other_lead, third])

    send_calls = []

    def fake_send(settings, *, to_phone, body):
        send_calls.append(to_phone)
        return True, "SM1", None, None, "twilio"

    # Budget starts at 1000; deadline 1050. First recipient ok at 1000, second check hits 1051.
    monotonic_values = [1000.0, 1000.0, 1051.0]

    with patch("integrations.tasks.send_company_sms", side_effect=fake_send):
        with patch("integrations.tasks.time.monotonic", side_effect=monotonic_values):
            with patch("integrations.tasks.enqueue_campaign_batch_send") as mock_enqueue:
                with patch("integrations.tasks.notify_campaign_batch_complete") as mock_notify:
                    send_campaign_batch_task(batch.id)
                    mock_enqueue.assert_called_once_with(batch.id)
                    mock_notify.assert_not_called()

    batch.refresh_from_db()
    assert batch.status == CampaignBatchStatus.SENDING
    assert batch.sent_count == 1
    assert send_calls == [own_lead.phone_number]

    with patch("integrations.tasks.send_company_sms", side_effect=fake_send):
        with patch("integrations.tasks.notify_campaign_batch_complete") as mock_notify:
            send_campaign_batch_task(batch.id)

    batch.refresh_from_db()
    assert batch.status == CampaignBatchStatus.COMPLETED
    assert batch.sent_count == 3
    mock_notify.assert_called_once()


@pytest.mark.django_db
def test_campaign_batch_resumes_from_partial_progress(company, employee_user, own_lead, other_lead, db):
    from integrations.models import TwilioSettings
    from integrations.tasks import send_campaign_batch_task

    TwilioSettings.objects.create(company=company, is_enabled=True, account_sid="AC1", twilio_number="+10000000000")
    batch = _sms_batch(
        company, employee_user, [own_lead, other_lead],
        status=CampaignBatchStatus.SENDING,
        sent_count=1,
    )

    send_calls = []

    def fake_send(settings, *, to_phone, body):
        send_calls.append(to_phone)
        return True, "SM1", None, None, "twilio"

    with patch("integrations.tasks.send_company_sms", side_effect=fake_send):
        with patch("integrations.tasks.notify_campaign_batch_complete"):
            send_campaign_batch_task(batch.id)

    batch.refresh_from_db()
    assert batch.status == CampaignBatchStatus.COMPLETED
    assert batch.sent_count == 2
    assert send_calls == [other_lead.phone_number]


@pytest.mark.django_db
def test_campaign_batch_reuses_header_media_id(company, employee_user, own_lead, other_lead, db):
    from integrations.models import IntegrationAccount, MessageTemplate, WhatsAppAccount
    from integrations.tasks import send_campaign_batch_task

    account = IntegrationAccount.objects.create(company=company, platform="whatsapp", name="WA", status="connected")
    account.set_access_token("tok")
    account.save(update_fields=["access_token"])
    wa = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-1",
        phone_number_id="phone-1",
        display_phone_number="+15550001",
        status="connected",
        integration_account=account,
    )
    wa.set_access_token("tok")
    wa.save(update_fields=["access_token"])
    template = MessageTemplate.objects.create(
        company=company,
        name="promo",
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content="Hello",
        header_type="image",
    )
    batch = MessageCampaignBatch.objects.create(
        company=company,
        channel=MessageCampaignBatch.CHANNEL_WHATSAPP,
        requested_by=employee_user,
        created_by=employee_user,
        requires_approval=True,
        status=CampaignBatchStatus.APPROVED,
        recipient_count=2,
        audience_snapshot=[
            {"client_id": own_lead.id, "phone_number": own_lead.phone_number, "name": own_lead.name},
            {"client_id": other_lead.id, "phone_number": other_lead.phone_number, "name": other_lead.name},
        ],
        message_payload={"template_id": template.id, "phone_number_id": wa.phone_number_id},
    )

    media_ids_seen = []

    def fake_send(*args, **kwargs):
        media_ids_seen.append(kwargs.get("header_media_id"))
        return True, "wam-1", None, None, {}

    with patch("integrations.tasks._resolve_batch_header_media_id", return_value=("media-cache-1", None)):
        with patch("integrations.tasks.send_whatsapp_template_message", side_effect=fake_send):
            with patch("integrations.tasks.time.sleep"):
                with patch("integrations.tasks.notify_campaign_batch_complete"):
                    send_campaign_batch_task(batch.id)

    batch.refresh_from_db()
    assert batch.status == CampaignBatchStatus.COMPLETED
    assert media_ids_seen == ["media-cache-1", "media-cache-1"]


@pytest.mark.django_db
def test_campaign_batch_lock_prevents_double_send(company, employee_user, own_lead, db):
    from django.core.cache import cache
    from integrations.models import TwilioSettings
    from integrations.tasks import send_campaign_batch_task

    TwilioSettings.objects.create(company=company, is_enabled=True, account_sid="AC1", twilio_number="+10000000000")
    batch = _sms_batch(company, employee_user, [own_lead])
    cache.set(f"campaign_send:{batch.id}", "1", timeout=90)

    with patch("integrations.tasks.send_company_sms") as mock_send:
        send_campaign_batch_task(batch.id)
        mock_send.assert_not_called()

    batch.refresh_from_db()
    assert batch.sent_count == 0
    cache.delete(f"campaign_send:{batch.id}")


@pytest.mark.django_db
def test_resume_stale_campaign_batches_reenqueues_without_lock(company, employee_user, own_lead, other_lead, db):
    from integrations.models import TwilioSettings
    from integrations.tasks import resume_stale_campaign_batches

    TwilioSettings.objects.create(company=company, is_enabled=True, account_sid="AC1", twilio_number="+10000000000")
    batch = _sms_batch(
        company, employee_user, [own_lead, other_lead],
        status=CampaignBatchStatus.SENDING,
        sent_count=1,
    )

    with patch("integrations.tasks.enqueue_campaign_batch_send", return_value=True) as mock_enqueue:
        count = resume_stale_campaign_batches()

    assert count == 1
    mock_enqueue.assert_called_once_with(batch.id)


@pytest.mark.django_db
def test_build_whatsapp_template_components_uses_cached_header_media_id(company, db):
    from integrations.views.templates_whatsapp import build_whatsapp_template_components_for_client

    template = MessageTemplate.objects.create(
        company=company,
        name="img_tpl",
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content="Hi",
        header_type="image",
    )
    components = build_whatsapp_template_components_for_client(
        template,
        None,
        header_media_id="cached-media-99",
    )
    assert components == [
        {
            "type": "header",
            "parameters": [{"type": "image", "image": {"id": "cached-media-99"}}],
        }
    ]


@pytest.mark.parametrize(
    "role,expected",
    [
        ("admin", False),
        ("supervisor", False),
        ("employee", True),
        ("doctor", True),
        ("data_entry", True),
        ("reception", True),
        ("call_center", True),
    ],
)
@pytest.mark.django_db
def test_role_requires_approval_matrix(company, role, expected, db):
    from accounts.models import User

    user = User.objects.create_user(
        username=f"{role}_matrix_user", email=f"{role}_matrix@test.com", password="testpass123",
        company=company, role=role,
    )
    assert user.requires_campaign_approval() is expected
