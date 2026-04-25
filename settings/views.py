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
    CanManageSettings,
)
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    CallMethod,
    VisitType,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
    SystemSettings,
    PlatformTwilioSettings,
    BillingSettings,
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
    SMTPSettingsSerializer,
    PlatformTwilioSettingsSerializer,
    SystemBackupSerializer,
    SystemAuditLogSerializer,
    SystemSettingsSerializer,
    BillingSettingsSerializer,
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
    Singleton pattern - only one instance (pk=1). GET and PUT only.
    """
    permission_classes = [IsAuthenticated, CanManageSettings]
    serializer_class = PlatformTwilioSettingsSerializer
    http_method_names = ["get", "put", "head", "options"]

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
