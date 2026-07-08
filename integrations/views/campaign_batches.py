from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm.models import Client
from crm_saas_api.responses import error_response, success_response
from integrations.models import MessageCampaignBatch, MessageCampaignFailure, MessageSendSource


class CreateCampaignBatchSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=MessageCampaignBatch.CHANNEL_CHOICES)
    message_preview = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    recipient_count = serializers.IntegerField(min_value=0, required=False, default=0)


class CompleteCampaignBatchSerializer(serializers.Serializer):
    sent_count = serializers.IntegerField(min_value=0, required=False)
    failed_count = serializers.IntegerField(min_value=0, required=False)


class RecordCampaignFailureSerializer(serializers.Serializer):
    client_id = serializers.IntegerField(required=False, allow_null=True)
    phone_number = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    error = serializers.CharField(max_length=512, required=False, allow_blank=True, default="")


def _get_company_batch(request, batch_id: int):
    company = request.user.company
    if not company:
        return None, error_response("Company not found.", code="bad_request", status_code=400)
    try:
        batch = MessageCampaignBatch.objects.get(id=batch_id, company=company)
    except MessageCampaignBatch.DoesNotExist:
        return None, error_response("Campaign batch not found.", code="not_found", status_code=404)
    return batch, None


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def create_campaign_batch(request):
    """POST /api/integrations/campaign-batches/"""
    company = request.user.company
    if not company:
        return error_response("Company not found.", code="bad_request", status_code=400)

    ser = CreateCampaignBatchSerializer(data=request.data)
    if not ser.is_valid():
        return error_response("Invalid request.", code="bad_request", details=ser.errors)

    batch = MessageCampaignBatch.objects.create(
        company=company,
        channel=ser.validated_data["channel"],
        message_preview=(ser.validated_data.get("message_preview") or "")[:2000],
        recipient_count=ser.validated_data.get("recipient_count") or 0,
        created_by=request.user,
    )
    return success_response(
        data={
            "id": batch.id,
            "channel": batch.channel,
            "message_preview": batch.message_preview,
            "recipient_count": batch.recipient_count,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
        },
        status_code=status.HTTP_201_CREATED,
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def complete_campaign_batch(request, batch_id: int):
    """PATCH /api/integrations/campaign-batches/:id/complete/"""
    batch, err = _get_company_batch(request, batch_id)
    if err:
        return err

    ser = CompleteCampaignBatchSerializer(data=request.data, partial=True)
    if not ser.is_valid():
        return error_response("Invalid request.", code="bad_request", details=ser.errors)

    if "sent_count" in ser.validated_data:
        batch.sent_count = ser.validated_data["sent_count"]
    if "failed_count" in ser.validated_data:
        batch.failed_count = ser.validated_data["failed_count"]
    batch.save(update_fields=["sent_count", "failed_count"])

    return success_response(
        data={
            "id": batch.id,
            "sent_count": batch.sent_count,
            "failed_count": batch.failed_count,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def record_campaign_failure(request, batch_id: int):
    """POST /api/integrations/campaign-batches/:id/failures/"""
    batch, err = _get_company_batch(request, batch_id)
    if err:
        return err

    ser = RecordCampaignFailureSerializer(data=request.data)
    if not ser.is_valid():
        return error_response("Invalid request.", code="bad_request", details=ser.errors)

    client = None
    client_id = ser.validated_data.get("client_id")
    if client_id:
        client = Client.objects.filter(id=client_id, company=batch.company).first()

    row = MessageCampaignFailure.objects.create(
        batch=batch,
        client=client,
        phone_number=(ser.validated_data.get("phone_number") or "")[:20],
        error=(ser.validated_data.get("error") or "Send failed")[:512],
    )
    return success_response(
        data={"id": row.id},
        status_code=status.HTTP_201_CREATED,
    )


def resolve_campaign_batch(company, batch_id, send_source: str):
    """Return campaign batch when send_source is campaign."""
    if send_source != MessageSendSource.CAMPAIGN:
        return None
    if not batch_id:
        return None
    try:
        return MessageCampaignBatch.objects.get(id=int(batch_id), company=company)
    except (TypeError, ValueError, MessageCampaignBatch.DoesNotExist):
        return None
