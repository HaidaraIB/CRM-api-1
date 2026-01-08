from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin, CanAccessDeveloper, CanAccessProject, CanAccessUnit, CanAccessOwner, HasActiveSubscription
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessDeveloper]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data.get('company')
        if not company:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'company': 'Company is required'})
        
        # البحث عن آخر developer لهذه الشركة مع code يبدأ بـ DEV
        last_developer = Developer.objects.filter(
            company=company,
            code__startswith='DEV'
        ).order_by('-id').first()
        
        new_num = 1
        if last_developer and last_developer.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_developer.code.replace('DEV', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"DEV{str(new_num).zfill(3)}"
            if not Developer.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique developer code")
        
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessProject]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "developer__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        # البحث عن آخر project لهذه الشركة مع code يبدأ بـ PROJ
        last_project = Project.objects.filter(
            company=company,
            code__startswith='PROJ'
        ).order_by('-id').first()
        
        new_num = 1
        if last_project and last_project.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_project.code.replace('PROJ', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"PROJ{str(new_num).zfill(3)}"
            if not Project.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique project code")
        
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessUnit]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "project__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        # البحث عن آخر unit لهذه الشركة مع code يبدأ بـ UNIT
        last_unit = Unit.objects.filter(
            company=company,
            code__startswith='UNIT'
        ).order_by('-id').first()
        
        new_num = 1
        if last_unit and last_unit.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_unit.code.replace('UNIT', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"UNIT{str(new_num).zfill(3)}"
            if not Unit.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique unit code")
        
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessOwner]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "phone"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        # البحث عن آخر owner لهذه الشركة مع code يبدأ بـ OWN
        last_owner = Owner.objects.filter(
            company=company,
            code__startswith='OWN'
        ).order_by('-id').first()
        
        new_num = 1
        if last_owner and last_owner.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_owner.code.replace('OWN', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"OWN{str(new_num).zfill(3)}"
            if not Owner.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique owner code")
        
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return OwnerListSerializer
        return OwnerSerializer
