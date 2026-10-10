from io import BytesIO
from pathlib import Path

from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from crm_saas_api.responses import error_response, success_response
from django.http import FileResponse
from django.db import models
from accounts.permissions import (
    HasActiveSubscription,
    IsAdminOrReadOnlyForEmployee,
    IsAdminOrSupervisorSettingsOrReadOnlyForEmployee,
    IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee,
    IsAdminOrSupervisorSettingsOrDealsReadOnly,
    CanManageSettings,
)
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    CallMethod,
    VisitType,
    Tag,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
    SystemSettings,
    PlatformTwilioSettings,
    PlatformOTPIQSettings,
    PlatformWhatsAppSettings,
    BillingSettings,
    DealPipeline,
    DealStage,
    DealLostReason,
)
from .serializers import (
    ChannelSerializer,
    ChannelListSerializer,
    LeadStageSerializer,
    LeadStageListSerializer,
    LeadStatusSerializer,
    LeadStatusListSerializer,
    CallMethodSerializer,
    CallMethodListSerializer,
    VisitTypeSerializer,
    VisitTypeListSerializer,
    TagSerializer,
    TagListSerializer,
    SMTPSettingsSerializer,
    PlatformTwilioSettingsSerializer,
    PlatformOTPIQSettingsSerializer,
    PlatformWhatsAppSettingsSerializer,
    SystemBackupSerializer,
    SystemAuditLogSerializer,
    SystemSettingsSerializer,
    BillingSettingsSerializer,
    DealPipelineSerializer,
    DealStageSerializer,
    DealLostReasonSerializer,
)
from .services import create_database_backup, restore_database_backup, delete_backup


class ChannelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Channel instances.
    Admin/supervisor with can_manage_settings: full access; supervisor with can_manage_leads: read-only (for Activities); employees: read-only.
    """

    queryset = Channel.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "type", "priority"]
    ordering_fields = ["is_default", "created_at", "name", "priority"]
    ordering = ["-is_default", "-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(company=user.company)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default", False):
            Channel.objects.filter(
                company=self.request.user.company,
                is_default=True,
            ).exclude(id=serializer.instance.id).update(is_default=False)
        serializer.save()

    def get_serializer_class(self):
        if self.action == "list":
            return ChannelListSerializer
        return ChannelSerializer


class LeadStageViewSet(viewsets.ModelViewSet):
    """
    Admin/supervisor with can_manage_settings: full access; supervisor with can_manage_leads: read-only (for Activities); employees: read-only.
    """

    queryset = LeadStage.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["is_default", "order", "name", "created_at"]
    ordering = ["-is_default", "order", "name"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        # Set order to be the last one
        max_order = LeadStage.objects.filter(company=user.company).aggregate(
            max_order=models.Max('order')
        )['max_order'] or 0
        serializer.save(company=user.company, order=max_order + 1)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default", False):
            LeadStage.objects.filter(
                company=self.request.user.company,
                is_default=True,
            ).exclude(id=serializer.instance.id).update(is_default=False)
        serializer.save()

    def get_serializer_class(self):
        if self.action == "list":
            return LeadStageListSerializer
        return LeadStageSerializer


class LeadStatusViewSet(viewsets.ModelViewSet):
    """
    Admin/supervisor with can_manage_settings: full access; supervisor with can_manage_leads: read-only (for Activities); employees: read-only.
    """

    queryset = LeadStatus.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "category"]
    ordering_fields = ["is_default", "name", "created_at"]
    ordering = ["-is_default", "name"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(company=user.company)

    def perform_update(self, serializer):
        # If setting a status as default, unset other defaults
        if serializer.validated_data.get('is_default', False):
            LeadStatus.objects.filter(
                company=self.request.user.company,
                is_default=True
            ).exclude(id=serializer.instance.id).update(is_default=False)
        serializer.save()

    def get_serializer_class(self):
        if self.action == "list":
            return LeadStatusListSerializer
        return LeadStatusSerializer


class CallMethodViewSet(viewsets.ModelViewSet):
    """
    Admin/supervisor with can_manage_settings: full access; supervisor with can_manage_leads: read-only (for Activities); employees: read-only.
    """

    queryset = CallMethod.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["is_default", "name", "created_at"]
    ordering = ["-is_default", "name"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(company=user.company)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default", False):
            CallMethod.objects.filter(
                company=self.request.user.company,
                is_default=True,
            ).exclude(id=serializer.instance.id).update(is_default=False)
        serializer.save()

    def get_serializer_class(self):
        if self.action == "list":
            return CallMethodListSerializer
        return CallMethodSerializer


class VisitTypeViewSet(viewsets.ModelViewSet):
    """
    Admin/supervisor with can_manage_settings: full access; supervisor with can_manage_leads: read-only; employees: read-only.
    """

    queryset = VisitType.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["is_default", "name", "created_at"]
    ordering = ["-is_default", "name"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(company=user.company)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default", False):
            VisitType.objects.filter(
                company=self.request.user.company,
                is_default=True,
            ).exclude(id=serializer.instance.id).update(is_default=False)
        serializer.save()

    def get_serializer_class(self):
        if self.action == "list":
            return VisitTypeListSerializer
        return VisitTypeSerializer


class TagViewSet(viewsets.ModelViewSet):
    """
    Per-tenant lead tags (secondary classification, many per lead).
    Admin/supervisor with can_manage_settings: full access; supervisor with can_manage_leads: read-only; employees: read-only.
    """

    queryset = Tag.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(company=user.company)

    def get_serializer_class(self):
        if self.action == "list":
            return TagListSerializer
        return TagSerializer


class SMTPSettingsViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing platform outbound email (Resend): from address, name, enable flag.
    Only SuperAdmin can manage these settings. API key is configured via RESEND_API_KEY on the server.
    Singleton pattern - only one instance exists.
    """
    queryset = SMTPSettings.objects.all()
    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = SMTPSettingsSerializer

    def get_queryset(self):
        """Return singleton instance"""
        return SMTPSettings.objects.filter(pk=1)


class PlatformTwilioSettingsViewSet(viewsets.ModelViewSet):
    """
    ViewSet for platform Twilio settings (admin SMS broadcast).
    Singleton pattern - only one instance (pk=1). GET and partial PATCH/PUT.
    """
    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = PlatformTwilioSettingsSerializer
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_queryset(self):
        return PlatformTwilioSettings.objects.filter(pk=1)

    def get_object(self):
        """Ensure singleton exists (get_or_create) when accessing pk=1."""
        if self.kwargs.get("pk") == "1" or self.kwargs.get("pk") == 1:
            return PlatformTwilioSettings.get_settings()
        return super().get_object()

    def list(self, request, *args, **kwargs):
        """Return the singleton data."""
        instance = PlatformTwilioSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)


class PlatformOTPIQSettingsViewSet(viewsets.ModelViewSet):
    """
    Platform OTPIQ settings (registration phone OTP).
    Singleton (pk=1). GET and partial PATCH/PUT.
    """

    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = PlatformOTPIQSettingsSerializer
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_queryset(self):
        return PlatformOTPIQSettings.objects.filter(pk=1)

    def get_object(self):
        if self.kwargs.get("pk") == "1" or self.kwargs.get("pk") == 1:
            return PlatformOTPIQSettings.get_settings()
        return super().get_object()

    def list(self, request, *args, **kwargs):
        instance = PlatformOTPIQSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)


class PlatformWhatsAppSettingsViewSet(viewsets.ModelViewSet):
    """
    Platform WhatsApp Cloud API settings (signup OTP + admin messaging).
    Singleton (pk=1). GET and partial PATCH/PUT.
    The admin panel sends only changed fields, so PATCH must be allowed.
    """

    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = PlatformWhatsAppSettingsSerializer
    http_method_names = ["get", "put", "patch", "post", "head", "options"]

    def get_queryset(self):
        return PlatformWhatsAppSettings.objects.filter(pk=1)

    def get_object(self):
        if self.kwargs.get("pk") == "1" or self.kwargs.get("pk") == 1:
            return PlatformWhatsAppSettings.get_settings()
        return super().get_object()

    def list(self, request, *args, **kwargs):
        instance = PlatformWhatsAppSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)

    @action(detail=True, methods=["post"], url_path="send-test-otp")
    def send_test_otp(self, request, pk=None):
        """
        Super-admin utility: send a one-off WhatsApp OTP template to a phone
        without creating a registration challenge.
        """
        import logging
        import secrets

        from django.core.cache import cache

        from accounts.platform_whatsapp import (
            effective_otp_template_lang,
            effective_otp_template_name,
            normalize_phone_digits,
            platform_whatsapp_configured,
            send_otp_template,
        )

        logger = logging.getLogger(__name__)
        self.get_object()

        phone_raw = (request.data.get("phone") or "").strip()
        phone = normalize_phone_digits(phone_raw)
        if len(phone) < 10 or len(phone) > 15:
            # Keep the specific `invalid_phone` business code (clients may branch on
            # it) but also add `error.fields.phone` so catalog-aware error handling
            # (serverFieldErrors/CatalogFormBinding) picks it up like any other
            # field-shaped error, instead of only the flat `error.details`.
            response = error_response(
                "Enter a valid phone number with country code.",
                code="invalid_phone",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
            response.data["error"]["fields"] = {
                "phone": [{"code": "validation.invalid_phone", "params": {}, "message": response.data["error"]["message"]}]
            }
            return response

        if not platform_whatsapp_configured():
            return error_response(
                "Platform WhatsApp is not configured (phone number ID and access token).",
                code="whatsapp_otp_not_configured",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        template_name = effective_otp_template_name()
        if not template_name:
            return error_response(
                "OTP template name is not configured.",
                code="otp_template_not_configured",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        rate_key = f"platform_wa_test_otp:{request.user.pk}"
        attempts = cache.get(rate_key, 0)
        if attempts >= 5:
            return error_response(
                "Too many test OTP sends. Wait a minute and try again.",
                code="otp_rate_limited",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(rate_key, attempts + 1, timeout=60)

        code = f"{secrets.randbelow(900000) + 100000}"
        ok, details = send_otp_template(phone, code)
        if not ok:
            logger.warning(
                "Platform WhatsApp test OTP failed: user=%s phone=%s details=%s",
                request.user.pk,
                phone[-4:],
                details,
            )
            return error_response(
                "Could not send test OTP via WhatsApp.",
                code="whatsapp_send_failed",
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                details=details if isinstance(details, dict) else {"error": str(details)},
            )

        template_lang = effective_otp_template_lang()
        return success_response(
            data={
                "phone_suffix": phone[-4:],
                "otp_code": code,
                "template_name": template_name,
                "template_lang": template_lang,
            },
            message="Test OTP sent.",
        )


class SystemBackupViewSet(viewsets.ModelViewSet):
    """
    Manage database backups stored on disk.
    Only super admins can trigger or delete backups.
    """

    queryset = SystemBackup.objects.all().order_by("-created_at")
    serializer_class = SystemBackupSerializer
    permission_classes = [IsAuthenticated, CanManageSettings]
    http_method_names = ["get", "post", "delete"]

    def create(self, request, *args, **kwargs):
        notes = request.data.get("notes", "")
        try:
            backup = create_database_backup(
                initiator=SystemBackup.Initiator.MANUAL,
                user=request.user,
                notes=notes,
            )
        except Exception as exc:
            return error_response(
                str(exc),
                code="bad_request",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(backup)
        headers = self.get_success_headers(serializer.data)
        return success_response(
            data=serializer.data,
            status_code=status.HTTP_201_CREATED,
            headers=headers,
        )

    def destroy(self, request, *args, **kwargs):
        backup = self.get_object()
        delete_backup(backup, user=request.user)
        return success_response(status_code=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        backup = self.get_object()
        try:
            snapshot_path = restore_database_backup(backup, user=request.user)
        except Exception as exc:
            return error_response(
                str(exc),
                code="bad_request",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return success_response(
            data={
                "status": "restored",
                "backup_id": backup.id,
                "snapshot": str(snapshot_path),
            },
            status_code=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        backup = self.get_object()
        if not backup.file or not backup.file.name:
            return error_response(
                "Backup file not found on disk.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        try:
            with backup.file.open("rb") as f:
                content = f.read()
        except (OSError, FileNotFoundError):
            return error_response(
                "Backup file not found on disk.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        filename = Path(backup.file.name).name
        response = FileResponse(
            BytesIO(content),
            as_attachment=True,
            filename=filename,
        )
        return response


class SystemAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only viewset exposing system-level audit events.
    """

    queryset = SystemAuditLog.objects.select_related("actor").all()
    serializer_class = SystemAuditLogSerializer
    permission_classes = [IsAuthenticated, CanManageSettings]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["action", "message"]
    ordering_fields = ["created_at", "action"]
    ordering = ["-created_at"]


class SystemSettingsViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing System Settings.
    Only SuperAdmin can manage system settings.
    Singleton pattern - only one instance exists.
    """
    queryset = SystemSettings.objects.all()
    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = SystemSettingsSerializer

    def get_queryset(self):
        """Return singleton instance"""
        return SystemSettings.objects.filter(pk=1)

    def get_object(self):
        """Get or create singleton instance"""
        return SystemSettings.get_settings()

    def list(self, request, *args, **kwargs):
        """Override list to return singleton as single item"""
        instance = SystemSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)

    def retrieve(self, request, *args, **kwargs):
        """Override retrieve to always return singleton"""
        instance = SystemSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)


class BillingSettingsViewSet(viewsets.ModelViewSet):
    """
    Singleton billing / invoice branding (issuer, logo, footer). GET and PATCH/PUT.
    """

    queryset = BillingSettings.objects.filter(pk=1)
    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = BillingSettingsSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_queryset(self):
        return BillingSettings.objects.filter(pk=1)

    def get_object(self):
        return BillingSettings.get_settings()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def list(self, request, *args, **kwargs):
        instance = BillingSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = BillingSettings.get_settings()
        serializer = self.get_serializer(instance)
        return success_response(data=serializer.data)


class DealPipelineViewSet(viewsets.ModelViewSet):
    queryset = DealPipeline.objects.all()
    serializer_class = DealPipelineSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrDealsReadOnly]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["order", "name", "created_at"]
    ordering = ["-is_default", "order", "name"]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(company=self.request.user.company)
            .prefetch_related("stages")
        )

    def perform_create(self, serializer):
        company = self.request.user.company
        is_default = serializer.validated_data.get("is_default", False)
        if is_default or not DealPipeline.objects.filter(company=company, is_default=True).exists():
            DealPipeline.objects.filter(company=company, is_default=True).update(is_default=False)
            serializer.save(company=company, is_default=True)
            return
        serializer.save(company=company)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default", False):
            DealPipeline.objects.filter(company=self.request.user.company, is_default=True).exclude(
                pk=serializer.instance.pk
            ).update(is_default=False)
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        pipeline = self.get_object()
        others = DealPipeline.objects.filter(company=request.user.company).exclude(pk=pipeline.pk)
        if not others.exists():
            return error_response("Keep at least one pipeline.", status_code=status.HTTP_400_BAD_REQUEST)
        if pipeline.is_default:
            replacement = others.order_by("order", "id").first()
            DealPipeline.objects.filter(pk=pipeline.pk).update(is_default=False)
            DealPipeline.objects.filter(pk=replacement.pk).update(is_default=True)
        return super().destroy(request, *args, **kwargs)


class DealStageViewSet(viewsets.ModelViewSet):
    queryset = DealStage.objects.all()
    serializer_class = DealStageSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrDealsReadOnly]
    filter_backends = [filters.OrderingFilter]
    ordering = ["order", "name"]

    def get_queryset(self):
        qs = super().get_queryset().filter(pipeline__company=self.request.user.company)
        pipeline = self.request.query_params.get("pipeline")
        if pipeline:
            qs = qs.filter(pipeline_id=pipeline)
        return qs

    def perform_create(self, serializer):
        pipeline = serializer.validated_data["pipeline"]
        if pipeline.company_id != self.request.user.company_id:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"pipeline": "Pipeline does not belong to this company."})
        max_order = DealStage.objects.filter(pipeline=pipeline).aggregate(max_order=models.Max("order"))[
            "max_order"
        ]
        serializer.save(order=(max_order or 0) + 1 if "order" not in serializer.validated_data else serializer.validated_data["order"])

    def destroy(self, request, *args, **kwargs):
        stage = self.get_object()
        deal_count = stage.deals.count()
        if deal_count:
            from crm.deals.exceptions import DealServiceError
            from crm.deals.services import DealService

            move_to = request.query_params.get("move_to") or request.data.get("move_to")
            target = DealStage.objects.filter(
                pk=move_to, pipeline__company=request.user.company
            ).select_related("pipeline").first()
            if target is None:
                return error_response(
                    "This stage still has deals. Pass move_to with another stage id.",
                    details={"deal_count": deal_count},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            try:
                DealService(request.user).rehome_stage(stage, target)
            except DealServiceError as exc:
                return error_response(exc.message, status_code=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        pipeline_id = request.data.get("pipeline")
        ordered_ids = request.data.get("ordered_ids") or []
        stages = DealStage.objects.filter(pipeline_id=pipeline_id, pipeline__company=request.user.company)
        known = {stage.id: stage for stage in stages}
        if not ordered_ids or any(int(stage_id) not in known for stage_id in ordered_ids):
            return error_response("ordered_ids must list every stage in the pipeline.", status_code=status.HTTP_400_BAD_REQUEST)
        if len(ordered_ids) != len(known):
            return error_response("ordered_ids must list every stage in the pipeline.", status_code=status.HTTP_400_BAD_REQUEST)
        for index, stage_id in enumerate(ordered_ids):
            DealStage.objects.filter(pk=stage_id).update(order=index)
        return Response(DealStageSerializer(self.get_queryset().filter(pipeline_id=pipeline_id), many=True).data)


class DealLostReasonViewSet(viewsets.ModelViewSet):
    queryset = DealLostReason.objects.all()
    serializer_class = DealLostReasonSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorSettingsOrDealsReadOnly]
    ordering = ["order", "name"]

    def get_queryset(self):
        return super().get_queryset().filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)
