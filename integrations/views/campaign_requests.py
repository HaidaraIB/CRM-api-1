"""
Messaging Center campaign-request approval workflow.

Kept separate from campaign_batches.py so the owner's existing instant-send
bookkeeping endpoints there stay untouched and low-risk. This file covers the
restricted-staff submit-for-approval flow: submit/list/detail/resubmit for the
requester, pending-queue/approve/reject for the owner.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import CanReviewCampaignRequest, CanSubmitCampaignRequest, HasActiveSubscription
from crm.models import Client
from crm_saas_api.responses import error_response, success_response
from integrations.campaign_notifications import (
    notify_owner_of_campaign_request,
    notify_requester_of_review,
)
from integrations.models import CampaignBatchStatus, MessageCampaignBatch, MessageTemplate


class RecipientSerializer(serializers.Serializer):
    client_id = serializers.IntegerField()
    phone_number = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")


class SubmitCampaignRequestSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=MessageCampaignBatch.CHANNEL_CHOICES)
    message_preview = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    recipients = RecipientSerializer(many=True, allow_empty=False)
    message_payload = serializers.DictField(required=False, default=dict)


class RejectCampaignRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, allow_blank=False)


_AUDIENCE_PREVIEW_LIMIT = 8


def _template_names_for(batches) -> dict:
    """Resolve WhatsApp template names for a page of batches in one query."""
    template_ids = {
        (b.message_payload or {}).get("template_id")
        for b in batches
        if b.channel == MessageCampaignBatch.CHANNEL_WHATSAPP
    }
    template_ids.discard(None)
    if not template_ids:
        return {}
    return dict(
        MessageTemplate.objects.filter(id__in=template_ids).values_list("id", "name")
    )


def _serialize_batch(batch: MessageCampaignBatch, template_names: dict | None = None) -> dict:
    audience = batch.audience_snapshot or []
    payload = batch.message_payload or {}
    template_id = payload.get("template_id")
    if template_names is None:
        template_names = _template_names_for([batch])
    return {
        "id": batch.id,
        "channel": batch.channel,
        "message_preview": batch.message_preview,
        "status": batch.status,
        "recipient_count": batch.recipient_count,
        "sent_count": batch.sent_count,
        "failed_count": batch.failed_count,
        # Full payload + ids so "Edit & resubmit" can restore the original
        # message and re-select the same audience in the lead picker.
        "message_payload": payload,
        "audience_client_ids": [r.get("client_id") for r in audience if r.get("client_id")],
        "audience_preview": [
            {"client_id": r.get("client_id"), "name": r.get("name") or ""}
            for r in audience[:_AUDIENCE_PREVIEW_LIMIT]
        ],
        "template_name": template_names.get(template_id) if template_id else None,
        "requested_by": (
            {"id": batch.requested_by_id, "name": batch.requested_by.get_full_name() or batch.requested_by.username}
            if batch.requested_by_id
            else None
        ),
        "reviewed_by": (
            {"id": batch.reviewed_by_id, "name": batch.reviewed_by.get_full_name() or batch.reviewed_by.username}
            if batch.reviewed_by_id
            else None
        ),
        "rejection_reason": batch.rejection_reason,
        "submitted_at": batch.submitted_at.isoformat() if batch.submitted_at else None,
        "resubmitted_at": batch.resubmitted_at.isoformat() if batch.resubmitted_at else None,
        "reviewed_at": batch.reviewed_at.isoformat() if batch.reviewed_at else None,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


def _validate_audience(user, company, recipients: list[dict]):
    """
    Hard server-side re-validation, independent of any frontend filter: when
    the requester's role requires approval, every recipient must be a lead
    currently assigned to them. Returns an error_response or None.
    """
    if not user.requires_campaign_approval():
        return None
    requested_ids = {int(r["client_id"]) for r in recipients}
    allowed_ids = set(
        Client.objects.filter(company=company, assigned_to=user, id__in=requested_ids).values_list("id", flat=True)
    )
    if not requested_ids.issubset(allowed_ids):
        return error_response(
            "You can only message leads assigned to you.",
            code="campaign_audience_forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return None


def _validate_message_payload(channel: str, message_payload: dict, company):
    """Basic sanity checks so a bad request fails at submit time, not send time."""
    if channel == MessageCampaignBatch.CHANNEL_WHATSAPP:
        template_id = message_payload.get("template_id")
        if not template_id:
            return error_response("template_id is required for WhatsApp campaigns.", code="bad_request")
        template = MessageTemplate.objects.filter(id=template_id, company=company).first()
        if not template:
            return error_response("Template not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
        if (template.channel_type or "").lower() not in ("whatsapp", "whatsapp_api"):
            return error_response("Only WhatsApp templates can be used here.", code="bad_request")
    else:
        if not (message_payload.get("body") or "").strip():
            return error_response("Message body is required for SMS campaigns.", code="bad_request")
    return None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanSubmitCampaignRequest])
def campaign_requests_list_create(request):
    """
    GET /api/integrations/campaign-requests/  - caller's own requests
    POST /api/integrations/campaign-requests/ - submit a new request
    """
    company = request.user.company
    if not company:
        return error_response("Company not found.", code="bad_request", status_code=400)

    if request.method == "GET":
        qs = MessageCampaignBatch.objects.filter(
            company=company, requested_by=request.user, requires_approval=True
        ).order_by("-created_at")
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        batches = list(qs.select_related("requested_by", "reviewed_by")[:200])
        template_names = _template_names_for(batches)
        return success_response(
            data={"results": [_serialize_batch(b, template_names) for b in batches]}
        )

    ser = SubmitCampaignRequestSerializer(data=request.data)
    if not ser.is_valid():
        return error_response("Invalid request.", code="bad_request", details=ser.errors)

    recipients = [dict(r) for r in ser.validated_data["recipients"]]
    channel = ser.validated_data["channel"]
    message_payload = ser.validated_data.get("message_payload") or {}

    err = _validate_audience(request.user, company, recipients)
    if err:
        return err
    err = _validate_message_payload(channel, message_payload, company)
    if err:
        return err

    phones_by_id = {int(r["client_id"]): (r.get("phone_number") or "") for r in recipients}
    clients = Client.objects.filter(company=company, id__in=phones_by_id.keys())
    audience_snapshot = [
        {
            "client_id": c.id,
            "phone_number": phones_by_id.get(c.id) or c.phone_number or "",
            "name": c.name,
        }
        for c in clients
    ]

    now = timezone.now()
    batch = MessageCampaignBatch.objects.create(
        company=company,
        channel=channel,
        message_preview=(ser.validated_data.get("message_preview") or "")[:2000],
        recipient_count=len(audience_snapshot),
        created_by=request.user,
        requested_by=request.user,
        requires_approval=True,
        status=CampaignBatchStatus.PENDING_APPROVAL,
        submitted_at=now,
        audience_snapshot=audience_snapshot,
        message_payload=message_payload,
    )
    transaction.on_commit(lambda: notify_owner_of_campaign_request(batch))

    return success_response(data=_serialize_batch(batch), status_code=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanReviewCampaignRequest])
def campaign_requests_pending(request):
    """GET /api/integrations/campaign-requests/pending/ - owner's approval queue."""
    company = request.user.company
    if not company:
        return error_response("Company not found.", code="bad_request", status_code=400)
    qs = MessageCampaignBatch.objects.filter(
        company=company, requires_approval=True, status=CampaignBatchStatus.PENDING_APPROVAL
    ).order_by("-created_at")
    batches = list(qs.select_related("requested_by", "reviewed_by")[:200])
    template_names = _template_names_for(batches)
    return success_response(
        data={"results": [_serialize_batch(b, template_names) for b in batches]}
    )


def _get_own_or_reviewable_batch(request, batch_id: int):
    company = request.user.company
    if not company:
        return None, error_response("Company not found.", code="bad_request", status_code=400)
    batch = MessageCampaignBatch.objects.filter(id=batch_id, company=company, requires_approval=True).first()
    if not batch:
        return None, error_response("Campaign request not found.", code="not_found", status_code=404)
    is_owner = request.user.is_admin()
    is_requester = batch.requested_by_id == request.user.id
    if not (is_owner or is_requester):
        return None, error_response("Campaign request not found.", code="not_found", status_code=404)
    return batch, None


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanSubmitCampaignRequest])
def campaign_requests_detail(request, batch_id: int):
    """GET /api/integrations/campaign-requests/:id/"""
    batch, err = _get_own_or_reviewable_batch(request, batch_id)
    if err:
        return err
    return success_response(data=_serialize_batch(batch))


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanReviewCampaignRequest])
def campaign_requests_approve(request, batch_id: int):
    """PATCH /api/integrations/campaign-requests/:id/approve/"""
    company = request.user.company
    if not company:
        return error_response("Company not found.", code="bad_request", status_code=400)
    batch = MessageCampaignBatch.objects.filter(id=batch_id, company=company, requires_approval=True).first()
    if not batch:
        return error_response("Campaign request not found.", code="not_found", status_code=404)
    if batch.status != CampaignBatchStatus.PENDING_APPROVAL:
        return error_response(
            "Only pending requests can be approved.", code="campaign_request_not_pending", status_code=409
        )

    batch.status = CampaignBatchStatus.APPROVED
    batch.reviewed_by = request.user
    batch.reviewed_at = timezone.now()
    batch.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    def _after_commit():
        notify_requester_of_review(batch)
        from integrations.tasks import enqueue_campaign_batch_send

        enqueue_campaign_batch_send(batch.id)

    transaction.on_commit(_after_commit)

    return success_response(data=_serialize_batch(batch))


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanReviewCampaignRequest])
def campaign_requests_reject(request, batch_id: int):
    """PATCH /api/integrations/campaign-requests/:id/reject/"""
    company = request.user.company
    if not company:
        return error_response("Company not found.", code="bad_request", status_code=400)
    batch = MessageCampaignBatch.objects.filter(id=batch_id, company=company, requires_approval=True).first()
    if not batch:
        return error_response("Campaign request not found.", code="not_found", status_code=404)
    if batch.status != CampaignBatchStatus.PENDING_APPROVAL:
        return error_response(
            "Only pending requests can be rejected.", code="campaign_request_not_pending", status_code=409
        )

    ser = RejectCampaignRequestSerializer(data=request.data)
    if not ser.is_valid():
        return error_response("A rejection reason is required.", code="bad_request", details=ser.errors)

    batch.status = CampaignBatchStatus.REJECTED
    batch.reviewed_by = request.user
    batch.reviewed_at = timezone.now()
    batch.rejection_reason = ser.validated_data["reason"]
    batch.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason"])
    transaction.on_commit(lambda: notify_requester_of_review(batch))

    return success_response(data=_serialize_batch(batch))


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanSubmitCampaignRequest])
def campaign_requests_resubmit(request, batch_id: int):
    """PATCH /api/integrations/campaign-requests/:id/resubmit/"""
    company = request.user.company
    if not company:
        return error_response("Company not found.", code="bad_request", status_code=400)
    batch = MessageCampaignBatch.objects.filter(
        id=batch_id, company=company, requires_approval=True, requested_by=request.user
    ).first()
    if not batch:
        return error_response("Campaign request not found.", code="not_found", status_code=404)
    if batch.status != CampaignBatchStatus.REJECTED:
        return error_response(
            "Only rejected requests can be resubmitted.", code="campaign_request_not_rejected", status_code=409
        )

    ser = SubmitCampaignRequestSerializer(data=request.data)
    if not ser.is_valid():
        return error_response("Invalid request.", code="bad_request", details=ser.errors)

    recipients = [dict(r) for r in ser.validated_data["recipients"]]
    channel = ser.validated_data["channel"]
    message_payload = ser.validated_data.get("message_payload") or {}

    err = _validate_audience(request.user, company, recipients)
    if err:
        return err
    err = _validate_message_payload(channel, message_payload, company)
    if err:
        return err

    phones_by_id = {int(r["client_id"]): (r.get("phone_number") or "") for r in recipients}
    clients = Client.objects.filter(company=company, id__in=phones_by_id.keys())
    audience_snapshot = [
        {
            "client_id": c.id,
            "phone_number": phones_by_id.get(c.id) or c.phone_number or "",
            "name": c.name,
        }
        for c in clients
    ]

    batch.channel = channel
    batch.message_preview = (ser.validated_data.get("message_preview") or "")[:2000]
    batch.recipient_count = len(audience_snapshot)
    batch.audience_snapshot = audience_snapshot
    batch.message_payload = message_payload
    batch.status = CampaignBatchStatus.PENDING_APPROVAL
    batch.resubmitted_at = timezone.now()
    batch.rejection_reason = ""
    batch.reviewed_by = None
    batch.reviewed_at = None
    batch.save(
        update_fields=[
            "channel", "message_preview", "recipient_count", "audience_snapshot", "message_payload",
            "status", "resubmitted_at", "rejection_reason", "reviewed_by", "reviewed_at",
        ]
    )
    transaction.on_commit(lambda: notify_owner_of_campaign_request(batch))

    return success_response(data=_serialize_batch(batch))
