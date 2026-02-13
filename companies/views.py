from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Company
from .serializers import CompanySerializer, CompanyListSerializer
from accounts.permissions import CanManageTenants
from accounts.models import User


class CompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Company instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Company.objects.all()
    permission_classes = [IsAuthenticated, CanManageTenants]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "domain", "owner__username"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        """Create company and automatically link owner's company field"""
        company = serializer.save()
        
        # تحديث company field في User (owner) تلقائياً
        if company.owner:
            company.owner.company = company
            company.owner.save(update_fields=['company'])
    
    def perform_update(self, serializer):
        """Update company and handle owner changes"""
        old_owner = None
        if self.get_object():
            old_owner = self.get_object().owner
        
        company = serializer.save()
        new_owner = company.owner
        
        # إذا تغير owner، تحديث company field في User الجديد
        if new_owner and new_owner != old_owner:
            new_owner.company = company
            new_owner.save(update_fields=['company'])
            
            # إزالة company من owner القديم (إن وجد)
            if old_owner and old_owner != new_owner:
                old_owner.company = None
                old_owner.save(update_fields=['company'])
        elif company.owner:
            # تأكد من أن owner مرتبط بالـ company
            if company.owner.company != company:
                company.owner.company = company
                company.owner.save(update_fields=['company'])

    def get_serializer_class(self):
        if self.action == "list":
            return CompanyListSerializer
        return CompanySerializer
    
    @action(detail=True, methods=['patch'], permission_classes=[IsAuthenticated])
    def update_assignment_settings(self, request, pk=None):
        """
        Update auto assign and re-assign settings for the company
        PATCH /api/companies/{id}/update_assignment_settings/
        Body: {
            "auto_assign_enabled": true/false,
            "re_assign_enabled": true/false,
            "re_assign_hours": 24
        }
        """
        company = self.get_object()
        user = request.user
        
        # Check permissions: user must be admin of this company or super admin
        if not (user.is_super_admin() or (user.is_admin() and user.company == company)):
            return Response(
                {'error': 'You do not have permission to update these settings.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Update settings
        auto_assign_enabled = request.data.get('auto_assign_enabled')
        re_assign_enabled = request.data.get('re_assign_enabled')
        re_assign_hours = request.data.get('re_assign_hours')
        
        if auto_assign_enabled is not None:
            company.auto_assign_enabled = bool(auto_assign_enabled)
        if re_assign_enabled is not None:
            company.re_assign_enabled = bool(re_assign_enabled)
        if re_assign_hours is not None:
            try:
                hours = int(re_assign_hours)
                if hours < 1:
                    return Response(
                        {'error': 're_assign_hours must be at least 1 hour.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                company.re_assign_hours = hours
            except (ValueError, TypeError):
                return Response(
                    {'error': 're_assign_hours must be a valid integer.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        company.save(update_fields=['auto_assign_enabled', 're_assign_enabled', 're_assign_hours'])
        
        serializer = CompanySerializer(company, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)
