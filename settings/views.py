from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin, IsSuperAdmin
from django.db import models
from .models import Channel, LeadStage, LeadStatus, SMTPSettings
from .serializers import (
    ChannelSerializer,
    ChannelListSerializer,
    LeadStageSerializer,
    LeadStageListSerializer,
    LeadStatusSerializer,
    LeadStatusListSerializer,
    SMTPSettingsSerializer,
)


class ChannelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Channel instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Admin can manage channels
    """

    queryset = Channel.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
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
    Only Admin can manage lead stages
    """

    queryset = LeadStage.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
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
    Only Admin can manage lead statuses
    """

    queryset = LeadStatus.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
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

    def list(self, request, *args, **kwargs):
        """Get SMTP settings (singleton)"""
        settings = SMTPSettings.get_settings()
        serializer = self.get_serializer(settings)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        """Get SMTP settings by ID (always returns the singleton)"""
        settings = SMTPSettings.get_settings()
        serializer = self.get_serializer(settings)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        """Create or update SMTP settings (singleton)"""
        settings = SMTPSettings.get_settings()
        serializer = self.get_serializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Update SMTP settings"""
        settings = SMTPSettings.get_settings()
        serializer = self.get_serializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        """Partially update SMTP settings"""
        return self.update(request, *args, **kwargs)

    @action(detail=False, methods=['post'])
    def test_connection(self, request):
        """Test SMTP connection with current settings"""
        from django.core.mail import get_connection
        from django.core.mail.backends.smtp import EmailBackend
        
        settings = SMTPSettings.get_settings()
        
        if not settings.is_active:
            return Response(
                {'error': 'SMTP is not active. Please enable it first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Create email backend with SMTP settings
            backend = EmailBackend(
                host=settings.host,
                port=settings.port,
                username=settings.username,
                password=settings.password,
                use_tls=settings.use_tls,
                use_ssl=settings.use_ssl,
                fail_silently=False,
            )
            
            # Test connection
            backend.open()
            backend.close()
            
            return Response({
                'status': 'success',
                'message': 'SMTP connection test successful'
            })
        except Exception as e:
            return Response(
                {'error': f'SMTP connection test failed: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )


