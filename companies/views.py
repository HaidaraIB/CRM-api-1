from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from .models import Company
from .serializers import CompanySerializer, CompanyListSerializer
from accounts.permissions import IsSuperAdmin
from accounts.models import User


class CompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Company instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Company.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
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
