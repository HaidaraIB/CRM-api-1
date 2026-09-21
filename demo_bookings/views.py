import logging
import threading
from datetime import timedelta, timezone as dt_timezone

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import CanManageDemoBookings
from crm_saas_api.responses import error_response, success_response, validation_error_response
from crm_saas_api.throttles import AuthRateThrottle

from .models import DemoBooking, DemoBookingBlockedDate, DemoBookingSettings, DemoBookingStatus
from .serializers import (
    DemoBookingBlockedDateSerializer,
    DemoBookingCreateSerializer,
    DemoBookingListSerializer,
    DemoBookingPublicConfigSerializer,
    DemoBookingPublicLookupSerializer,
    DemoBookingSettingsSerializer,
    DemoBookingStatusSerializer,
)
from .services import (
    compute_available_slots,
    dates_with_slots,
    has_upcoming_active_booking,
    is_slot_available,
    normalize_email,
    normalize_phone,
)
from .notifications import DemoBookingTransitionError, approve_demo_booking, not_confirm_demo_booking

logger = logging.getLogger(__name__)


def _parse_date_param(value: str):
    from datetime import date

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _send_demo_booking_emails_async(booking_id: int):
    def _run():
        try:
            from accounts.event_emails import (
                send_demo_booking_admin_notifications,
                send_demo_booking_request_received_email,
            )

            booking = DemoBooking.objects.get(pk=booking_id)
            settings_obj = DemoBookingSettings.get_settings()
            send_demo_booking_request_received_email(booking, settings_obj)
            send_demo_booking_admin_notifications(booking, settings_obj)
        except Exception:
            logger.exception("Failed to send demo booking emails for id=%s", booking_id)

    transaction.on_commit(lambda: threading.Thread(target=_run, daemon=True).start())


class DemoBookingPublicConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        settings_obj = DemoBookingSettings.get_settings()
        data = DemoBookingPublicConfigSerializer(settings_obj).data
        return success_response(data=data)


class DemoBookingPublicSlotsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        settings_obj = DemoBookingSettings.get_settings()
        if not settings_obj.is_enabled:
            return success_response(data={"slots": [], "dates_with_slots": []})
        from_date = _parse_date_param(request.query_params.get("from", ""))
        to_date = _parse_date_param(request.query_params.get("to", ""))
        if not from_date or not to_date or to_date < from_date:
            return error_response(
                "Query params 'from' and 'to' (YYYY-MM-DD) are required.",
                code="invalid_date_range",
            )
        span = (to_date - from_date).days
        if span > 93:
            return error_response(
                "Date range cannot exceed 93 days.",
                code="invalid_date_range",
            )
        slots = compute_available_slots(settings_obj, from_date, to_date)
        return success_response(
            data={
                "slots": slots,
                "dates_with_slots": dates_with_slots(settings_obj, from_date, to_date),
            }
        )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def create_demo_booking_public(request):
    settings_obj = DemoBookingSettings.get_settings()
    if not settings_obj.is_enabled:
        return error_response(
            "Demo booking is not available at this time.",
            code="booking_disabled",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    serializer = DemoBookingCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)
    data = serializer.validated_data
    starts_at = data["starts_at"]
    if timezone.is_naive(starts_at):
        starts_at = timezone.make_aware(starts_at, dt_timezone.utc)
    starts_at = starts_at.astimezone(dt_timezone.utc).replace(microsecond=0)
    email = normalize_email(data["email"])
    phone = normalize_phone(data["phone"])
    if has_upcoming_active_booking(email=email):
        return error_response(
            "You already have an upcoming demo booking with this email.",
            code="duplicate_booking",
        )
    if has_upcoming_active_booking(phone=phone):
        return error_response(
            "You already have an upcoming demo booking with this phone number.",
            code="duplicate_booking",
        )
    duration = int(settings_obj.duration_minutes or 30)
    ends_at = starts_at + timedelta(minutes=duration)

    try:
        with transaction.atomic():
            DemoBookingSettings.objects.select_for_update().get(pk=1)
            if not is_slot_available(settings_obj, starts_at):
                return error_response(
                    "This time slot is no longer available.",
                    code="slot_unavailable",
                    status_code=status.HTTP_409_CONFLICT,
                )
            booking = DemoBooking.objects.create(
                name=data["name"].strip(),
                email=email,
                phone=phone,
                company_name=(data.get("company_name") or "").strip(),
                notes=(data.get("notes") or "").strip(),
                starts_at=starts_at,
                ends_at=ends_at,
                status=DemoBookingStatus.PENDING,
                language=data.get("language") or "en",
            )
    except Exception as exc:
        from django.db import IntegrityError

        if isinstance(exc, IntegrityError):
            return error_response(
                "This time slot is no longer available.",
                code="slot_unavailable",
                status_code=status.HTTP_409_CONFLICT,
            )
        raise

    _send_demo_booking_emails_async(booking.pk)
    out = DemoBookingListSerializer(booking).data
    out["timezone"] = settings_obj.timezone or "Asia/Baghdad"
    return success_response(data=out, status_code=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def lookup_demo_booking_public(request):
    settings_obj = DemoBookingSettings.get_settings()
    if not settings_obj.is_enabled:
        return error_response(
            "Demo booking is not available at this time.",
            code="booking_disabled",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    serializer = DemoBookingPublicLookupSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)
    data = serializer.validated_data
    email = normalize_email(data["email"])
    booking = DemoBooking.objects.filter(pk=data["booking_id"], email=email).first()
    if not booking:
        return error_response(
            "No booking found for this confirmation number and email.",
            code="booking_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    out = DemoBookingListSerializer(booking).data
    out["timezone"] = settings_obj.timezone or "Asia/Baghdad"
    return success_response(data=out)


class DemoBookingSettingsView(APIView):
    permission_classes = [IsAuthenticated, CanManageDemoBookings]

    def get(self, request, *args, **kwargs):
        settings_obj = DemoBookingSettings.get_settings()
        return success_response(data=DemoBookingSettingsSerializer(settings_obj).data)

    def patch(self, request, *args, **kwargs):
        settings_obj = DemoBookingSettings.get_settings()
        serializer = DemoBookingSettingsSerializer(
            settings_obj, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save()
        return success_response(data=serializer.data)


class DemoBookingBlockedDateViewSet(viewsets.ModelViewSet):
    queryset = DemoBookingBlockedDate.objects.all()
    serializer_class = DemoBookingBlockedDateSerializer
    permission_classes = [IsAuthenticated, CanManageDemoBookings]
    http_method_names = ["get", "post", "delete", "head", "options"]


class DemoBookingAdminViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = DemoBooking.objects.all().order_by("-starts_at")
    permission_classes = [IsAuthenticated, CanManageDemoBookings]
    filterset_fields = []
    http_method_names = ["get", "patch", "delete", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action in ("partial_update", "update"):
            return DemoBookingStatusSerializer
        return DemoBookingListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(company_name__icontains=search)
            )
        from_date = _parse_date_param(self.request.query_params.get("from_date", ""))
        to_date = _parse_date_param(self.request.query_params.get("to_date", ""))
        if from_date:
            qs = qs.filter(starts_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(starts_at__date__lte=to_date)
        return qs

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = DemoBookingStatusSerializer(instance, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save()
        instance.refresh_from_db()
        return success_response(data=DemoBookingListSerializer(instance).data)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        booking = self.get_object()
        try:
            updated = approve_demo_booking(booking)
        except DemoBookingTransitionError as exc:
            if exc.code == "not_pending":
                return error_response(
                    "Only pending bookings can be approved.",
                    code="invalid_status",
                    status_code=status.HTTP_409_CONFLICT,
                )
            raise
        return success_response(data=DemoBookingListSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="not-confirm")
    def not_confirm(self, request, pk=None):
        booking = self.get_object()
        try:
            updated = not_confirm_demo_booking(booking)
        except DemoBookingTransitionError as exc:
            if exc.code == "not_pending":
                return error_response(
                    "Only pending bookings can be marked as not confirmed.",
                    code="invalid_status",
                    status_code=status.HTTP_409_CONFLICT,
                )
            raise
        return success_response(data=DemoBookingListSerializer(updated).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return success_response(status_code=status.HTTP_204_NO_CONTENT)
