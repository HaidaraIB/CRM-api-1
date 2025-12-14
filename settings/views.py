from pathlib import Path

from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse
from django.db import models
from accounts.permissions import IsAdmin, IsSuperAdmin, HasActiveSubscription, IsAdminOrReadOnlyForEmployee
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
)
from .serializers import (
    ChannelSerializer,
    ChannelListSerializer,
    LeadStageSerializer,
    LeadStageListSerializer,
    LeadStatusSerializer,
    LeadStatusListSerializer,
    SMTPSettingsSerializer,
    SystemBackupSerializer,
    SystemAuditLogSerializer,
)
from .services import create_database_backup, restore_database_backup, delete_backup


class ChannelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Channel instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Admin can manage channels, but employees can read (GET) them
    """

    queryset = Channel.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "type", "priority"]
    ordering_fields = ["created_at", "name", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(company=user.company)

    def get_serializer_class(self):
        if self.action == "list":
            return ChannelListSerializer
        return ChannelSerializer


class LeadStageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing LeadStage instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Admin can manage lead stages, but employees can read (GET) them
    """

    queryset = LeadStage.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["order", "name", "created_at"]
    ordering = ["order", "name"]

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

    def get_serializer_class(self):
        if self.action == "list":
            return LeadStageListSerializer
        return LeadStageSerializer


class LeadStatusViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing LeadStatus instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Admin can manage lead statuses, but employees can read (GET) them
    """

    queryset = LeadStatus.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrReadOnlyForEmployee]
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


class SMTPSettingsViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing SMTP Settings.
    Only SuperAdmin can manage SMTP settings.
    Singleton pattern - only one instance exists.
    """
    queryset = SMTPSettings.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = SMTPSettingsSerializer

    def get_queryset(self):
        """Return singleton instance"""
        return SMTPSettings.objects.filter(pk=1)


class SystemBackupViewSet(viewsets.ModelViewSet):
    """
    Manage database backups stored on disk.
    Only super admins can trigger or delete backups.
    """

    queryset = SystemBackup.objects.all().order_by("-created_at")
    serializer_class = SystemBackupSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
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
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(backup)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        backup = self.get_object()
        delete_backup(backup, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        backup = self.get_object()
        try:
            snapshot_path = restore_database_backup(backup, user=request.user)
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "status": "restored",
                "backup_id": backup.id,
                "snapshot": str(snapshot_path),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        backup = self.get_object()
        if not backup.file:
            return Response(
                {"detail": "Backup file not found on disk."},
                status=status.HTTP_404_NOT_FOUND,
            )
        file_path = backup.file.path
        response = FileResponse(
            open(file_path, "rb"),
            as_attachment=True,
            filename=Path(file_path).name,
        )
        return response


class SystemAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only viewset exposing system-level audit events.
    """

    queryset = SystemAuditLog.objects.select_related("actor").all()
    serializer_class = SystemAuditLogSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["action", "message"]
    ordering_fields = ["created_at", "action"]
    ordering = ["-created_at"]
