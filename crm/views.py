from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import CanAccessClient, CanAccessDeal, CanAccessTask
from .models import Client, Deal, Task, Campaign, ClientTask
from .serializers import (
    ClientSerializer,
    ClientListSerializer,
    DealSerializer,
    DealListSerializer,
    TaskSerializer,
    TaskListSerializer,
    CampaignSerializer,
    CampaignListSerializer,
    ClientTaskSerializer,
    ClientTaskListSerializer,
)


class ClientViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Client instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Client.objects.all()
    permission_classes = [IsAuthenticated, CanAccessClient]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "phone_number", "priority", "type", "communication_way"]
    ordering_fields = ["created_at", "name", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        if user.is_employee():
            return queryset.filter(assigned_to=user)

        return queryset.none()

    def get_serializer_class(self):
        if self.action == "list":
            return ClientListSerializer
        return ClientSerializer


class DealViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Deal instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Deal.objects.all()
    permission_classes = [IsAuthenticated, CanAccessDeal]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["client__name", "stage", "company__name"]
    ordering_fields = ["created_at", "updated_at", "stage"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        if user.is_employee():
            return queryset.filter(employee=user)

        return queryset.none()

    def get_serializer_class(self):
        if self.action == "list":
            return DealListSerializer
        return DealSerializer


class TaskViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Task instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Task.objects.all()
    permission_classes = [IsAuthenticated, CanAccessTask]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage", "deal__client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(deal__company=user.company)

        if user.is_employee():
            return queryset.filter(deal__employee=user)

        return queryset.none()

    def get_serializer_class(self):
        if self.action == "list":
            return TaskListSerializer
        return TaskSerializer


class CampaignViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Campaign instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name", "budget"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        if user.is_employee():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        
        # البحث عن آخر campaign لهذه الشركة مع code يبدأ بـ CAMP
        # نستخدم order_by('-id') بدلاً من '-code' لضمان الحصول على آخر campaign تم إنشاؤه
        last_campaign = Campaign.objects.filter(
            company=company,
            code__startswith='CAMP'
        ).order_by('-id').first()
        
        new_num = 1
        if last_campaign and last_campaign.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_campaign.code.replace('CAMP', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition أو بيانات مكررة)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"CAMP{str(new_num).zfill(3)}"
            if not Campaign.objects.filter(code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        # إذا فشلنا في إيجاد code فريد، استخدم timestamp كبديل
        if not new_code:
            import time
            timestamp_suffix = str(int(time.time()))[-6:]  # آخر 6 أرقام من timestamp
            new_code = f"CAMP{timestamp_suffix}"
            # تأكد مرة أخرى من عدم التكرار
            while Campaign.objects.filter(code=new_code).exists():
                timestamp_suffix = str(int(time.time()) + attempt)[-6:]
                new_code = f"CAMP{timestamp_suffix}"
                attempt += 1
                if attempt > 100:
                    raise ValueError("Unable to generate unique campaign code")
        
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return CampaignListSerializer
        return CampaignSerializer


class ClientTaskViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing ClientTask instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = ClientTask.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage", "client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(client__company=user.company)

        if user.is_employee():
            return queryset.filter(client__assigned_to=user)

        return queryset.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return ClientTaskListSerializer
        return ClientTaskSerializer
