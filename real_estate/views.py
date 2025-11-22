from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin
from .models import Developer, Project, Unit, Owner
from .serializers import (
    DeveloperSerializer,
    DeveloperListSerializer,
    ProjectSerializer,
    ProjectListSerializer,
    UnitSerializer,
    UnitListSerializer,
    OwnerSerializer,
    OwnerListSerializer,
)


class DeveloperViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Developer instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Developer.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data.get('company')
        if not company:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'company': 'Company is required'})
        
        last_developer = Developer.objects.filter(company=company).order_by('-code').first()
        if last_developer and last_developer.code:
            try:
                last_num = int(last_developer.code.replace('DEV', ''))
                new_code = f"DEV{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "DEV001"
        else:
            new_code = "DEV001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return DeveloperListSerializer
        return DeveloperSerializer


class ProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Project instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Project.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "developer__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_project = Project.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_project and last_project.code:
            try:
                last_num = int(last_project.code.replace('PROJ', ''))
                new_code = f"PROJ{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "PROJ001"
        else:
            new_code = "PROJ001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ProjectListSerializer
        return ProjectSerializer


class UnitViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Unit instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Unit.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "project__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_unit = Unit.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_unit and last_unit.code:
            try:
                last_num = int(last_unit.code.replace('UNIT', ''))
                new_code = f"UNIT{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "UNIT001"
        else:
            new_code = "UNIT001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return UnitListSerializer
        return UnitSerializer


class OwnerViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Owner instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Owner.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "phone"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_owner = Owner.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_owner and last_owner.code:
            try:
                last_num = int(last_owner.code.replace('OWN', ''))
                new_code = f"OWN{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "OWN001"
        else:
            new_code = "OWN001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return OwnerListSerializer
        return OwnerSerializer
