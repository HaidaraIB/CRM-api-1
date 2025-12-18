from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import CanAccessClient, CanAccessDeal, CanAccessTask, IsAdmin, HasActiveSubscription, IsAdminOrReadOnlyForEmployee
from .models import Client, Deal, Task, Campaign, ClientTask, ClientEvent
from accounts.models import User
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
    ClientEventSerializer,
)


class ClientViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Client instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Client.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessClient]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "phone_number", "priority", "type", "communication_way__name", "status__name"]
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

    @action(detail=False, methods=["post"])
    def bulk_assign(self, request):
        """
        Assign multiple clients to a specific user
        POST /api/clients/bulk_assign/
        Body: { "client_ids": [1, 2, 3], "user_id": 4 }
        """
        client_ids = request.data.get("client_ids", [])
        user_id = request.data.get("user_id")

        if not client_ids or not user_id:
            return Response(
                {"error": "Both client_ids and user_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_user = User.objects.get(pk=user_id, company=request.user.company)
        except User.DoesNotExist:
            return Response(
                {"error": "User not found or does not belong to your company."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Only admins can bulk assign leads
        if not request.user.is_admin():
            return Response(
                {"error": "Only admins can assign leads."},
                status=status.HTTP_403_FORBIDDEN,
            )

        clients_to_update = Client.objects.filter(
            id__in=client_ids, company=request.user.company
        )
        
        updated_count = 0
        for client in clients_to_update:
            old_assigned_name = client.assigned_to.username if client.assigned_to else "None"
            if client.assigned_to != target_user:
                client.assigned_to = target_user
                client.save()
                updated_count += 1
                
                # Record the event
                ClientEvent.objects.create(
                    client=client,
                    event_type='assignment',
                    old_value=old_assigned_name,
                    new_value=target_user.username,
                    notes=f"Bulk assigned to {target_user.username} (was {old_assigned_name})",
                    created_by=request.user
                )

        return Response(
            {"message": f"Successfully assigned {updated_count} leads."},
            status=status.HTTP_200_OK,
        )


class DealViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Deal instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Deal.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessDeal]
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessTask]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage__name", "deal__client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage__name"]
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
    Only Admin can manage campaigns, but employees can read (GET) them
    """

    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrReadOnlyForEmployee]
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessClient]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage__name", "client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage__name"]
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


class ClientEventViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing ClientEvent instances.
    Provides read-only access to events.
    """

    queryset = ClientEvent.objects.all()
    serializer_class = ClientEventSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessClient]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["event_type", "notes", "client__name"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()
        
        # Filter by client ID if provided in query params
        client_id = self.request.query_params.get('client', None)
        if client_id:
            queryset = queryset.filter(client_id=client_id)

        if user.is_admin():
            return queryset.filter(client__company=user.company)

        if user.is_employee():
            return queryset.filter(client__assigned_to=user)

        return queryset.none()
