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
from integrations.models import CampaignBatchStatus, MessageCampaignBatch, MessageCampaignFailure


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
        send_campaign_batch_task(batch.id)

    batch.refresh_from_db()
    assert batch.status == CampaignBatchStatus.COMPLETED
    assert batch.sent_count == 1
    assert batch.failed_count == 1
    assert MessageCampaignFailure.objects.filter(batch=batch).count() == 1


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
