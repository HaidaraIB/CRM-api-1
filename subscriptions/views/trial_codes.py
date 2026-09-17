import csv
import io

from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from crm_saas_api.responses import error_response, success_response, validation_error_response
from crm_saas_api.throttles import AuthRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.permissions import CanManageSubscriptions
from subscriptions.models import TrialCode
from subscriptions.serializers import (
    TrialCodeBatchGenerateSerializer,
    TrialCodeCreateSerializer,
    TrialCodeListSerializer,
    TrialCodeRedemptionSerializer,
    TrialCodeSerializer,
    TrialCodeValidateSerializer,
)
from subscriptions.services.checkout_auth import require_subscription_owner
from subscriptions.services.trial_codes import (
    TrialCodeError,
    generate_unique_code,
    is_paid_plan,
    redeem_trial_code,
    validate_trial_code,
)


class TrialCodeViewSet(viewsets.ModelViewSet):
    """Super Admin CRUD for launch trial codes."""

    queryset = TrialCode.objects.select_related("plan", "created_by").all()
    permission_classes = [IsAuthenticated, CanManageSubscriptions]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["code", "label", "notes"]
    ordering_fields = ["created_at", "code", "trial_days", "redeemed_count"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return TrialCodeListSerializer
        if self.action == "create":
            return TrialCodeCreateSerializer
        return TrialCodeSerializer

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        status_filter = (self.request.query_params.get("status") or "").strip().lower()
        plan_id = self.request.query_params.get("plan")
        label = (self.request.query_params.get("label") or "").strip()
        now = timezone.now()

        if plan_id:
            try:
                queryset = queryset.filter(plan_id=int(plan_id))
            except (TypeError, ValueError):
                pass
        if label:
            queryset = queryset.filter(label=label)
        if status_filter == "active":
            queryset = queryset.filter(is_active=True).exclude(
                redeemed_count__gte=F("max_redemptions")
            )
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        elif status_filter == "exhausted":
            queryset = queryset.filter(redeemed_count__gte=F("max_redemptions"))
        elif status_filter == "expired":
            queryset = queryset.filter(expires_at__lt=now)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.redeemed_count > 0:
            return error_response(
                "Cannot delete a trial code that has been redeemed. Deactivate it instead.",
                code="has_redemptions",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        return success_response(data=TrialCodeSerializer(instance).data)

    @action(detail=True, methods=["get"], url_path="redemptions")
    def redemptions(self, request, pk=None):
        instance = self.get_object()
        rows = instance.redemptions.select_related("company", "subscription").all()
        return success_response(
            data=TrialCodeRedemptionSerializer(rows, many=True).data,
        )

    @action(detail=False, methods=["post"], url_path="generate-batch")
    def generate_batch(self, request):
        ser = TrialCodeBatchGenerateSerializer(data=request.data)
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        data = ser.validated_data
        plan = data["plan"]
        if not is_paid_plan(plan):
            return error_response(
                "Trial codes must target a paid plan.",
                code="invalid_plan",
            )
        quantity = int(data["quantity"])
        label = (data.get("label") or "").strip()
        created = []
        for _ in range(quantity):
            code = generate_unique_code()
            tc = TrialCode.objects.create(
                code=code,
                label=label,
                trial_days=data["trial_days"],
                plan=plan,
                max_redemptions=1,
                starts_at=data.get("starts_at"),
                expires_at=data.get("expires_at"),
                notes=(data.get("notes") or "").strip(),
                created_by=request.user,
            )
            created.append(tc)
        return success_response(
            data={
                "created_count": len(created),
                "codes": TrialCodeListSerializer(created, many=True).data,
            },
            status_code=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="export-unused")
    def export_unused(self, request):
        label = (request.query_params.get("label") or "").strip()
        if not label:
            return error_response("label query parameter is required.", code="missing_label")
        rows = TrialCode.objects.filter(
            label=label,
            redeemed_count=0,
            is_active=True,
        ).order_by("code")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["code", "trial_days", "plan", "label"])
        for tc in rows.select_related("plan"):
            writer.writerow([tc.code, tc.trial_days, tc.plan.name, tc.label])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="trial-codes-{label}.csv"'
        return response


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def validate_trial_code_public(request):
    """
    Validate a trial code without consuming it.
    POST /api/v1/public/trial-codes/validate/
    """
    ser = TrialCodeValidateSerializer(data=request.data)
    if not ser.is_valid():
        return validation_error_response(ser.errors)
    raw = ser.validated_data["code"]
    try:
        validated = validate_trial_code(raw)
    except TrialCodeError as exc:
        return error_response(exc.message, code=exc.error_code)
    plan = validated.plan
    return success_response(
        data={
            "valid": True,
            "trial_days": validated.trial_code.trial_days,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "plan_name_ar": plan.name_ar or "",
        },
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def redeem_trial_code_subscription(request):
    """
    Redeem a trial code for the authenticated owner's company (inactive subscription path).
    POST /api/v1/subscriptions/redeem-trial-code/
    """
    ser = TrialCodeValidateSerializer(data=request.data)
    if not ser.is_valid():
        return validation_error_response(ser.errors)

    user = request.user
    company = getattr(user, "company", None)
    if not company:
        return error_response("User is not associated with a company.", code="no_company")

    from subscriptions.models import Subscription

    subscription = (
        Subscription.objects.filter(company=company).order_by("-created_at").first()
    )
    if subscription:
        err = require_subscription_owner(request, subscription)
        if err is not None:
            return err

    try:
        subscription = redeem_trial_code(
            raw_code=ser.validated_data["code"],
            company=company,
            owner=user,
        )
    except TrialCodeError as exc:
        return error_response(exc.message, code=exc.error_code)

    refresh = RefreshToken.for_user(user)
    return success_response(
        data={
            "subscription_id": subscription.id,
            "plan_id": subscription.plan_id,
            "plan_name": subscription.plan.name,
            "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
            "subscription_status": subscription.subscription_status,
            "is_active": subscription.is_active,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        },
    )
