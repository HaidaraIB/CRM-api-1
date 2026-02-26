from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import CanAccessClient, CanAccessDeal, CanAccessTask, IsAdmin, HasActiveSubscription, IsAdminOrReadOnlyForEmployee, IsAdminOrSupervisorLeadsOrReadOnlyForEmployee
from .models import Client, Deal, Task, Campaign, ClientTask, ClientCall, ClientEvent
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
    ClientCallSerializer,
    ClientCallListSerializer,
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

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(company=user.company)

        if user.is_employee():
            return queryset.filter(assigned_to=user)

        return queryset.none()

    def get_serializer_class(self):
        if self.action == "list":
            return ClientListSerializer
        return ClientSerializer

    @action(detail=False, methods=["post"])
    def assign_unassigned(self, request):
        """
        Auto-assign all unassigned clients for companies with auto_assign_enabled
        POST /api/clients/assign_unassigned/
        """
        from crm.signals import get_least_busy_employee
        from django.utils import timezone
        
        user = request.user
        company = user.company
        
        if not company:
            return Response(
                {"error": "You must belong to a company."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Only admins can trigger this
        if not user.is_admin():
            return Response(
                {"error": "Only admins can assign unassigned clients."},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        # Get unassigned clients for this company
        unassigned_clients = Client.objects.filter(
            company=company,
            assigned_to__isnull=True
        )
        
        if not unassigned_clients.exists():
            return Response({
                "message": "No unassigned clients found.",
                "assigned_count": 0
            })
        
        # Only assign if auto_assign is enabled
        if not company.auto_assign_enabled:
            return Response({
                "message": "Auto assign is not enabled for your company.",
                "assigned_count": 0
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the least busy employee
        employee = get_least_busy_employee(company)
        
        if not employee:
            return Response({
                "error": "No active employees found in your company.",
                "assigned_count": 0
            }, status=status.HTTP_400_BAD_REQUEST)
        
        assigned_count = 0
        for client in unassigned_clients:
            client.assigned_to = employee
            client.assigned_at = timezone.now()
            client.save(update_fields=['assigned_to', 'assigned_at'])
            
            # Record the event
            ClientEvent.objects.create(
                client=client,
                event_type='assignment',
                old_value="Unassigned",
                new_value=employee.get_full_name() or employee.username,
                notes=f"Auto-assigned to {employee.get_full_name() or employee.username}",
                created_by=user
            )
            assigned_count += 1
        
        return Response({
            "message": f"Successfully assigned {assigned_count} client(s) to {employee.get_full_name() or employee.username}.",
            "assigned_count": assigned_count,
            "assigned_to": employee.get_full_name() or employee.username
        })

    @action(detail=False, methods=["post"])
    def bulk_assign(self, request):
        """
        Assign multiple clients to a specific user or unassign them
        POST /api/clients/bulk_assign/
        Body: { "client_ids": [1, 2, 3], "user_id": 4 } or { "client_ids": [1, 2, 3], "user_id": null }
        """
        client_ids = request.data.get("client_ids", [])
        user_id = request.data.get("user_id")

        if not client_ids:
            return Response(
                {"error": "client_ids is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only admins can bulk assign leads
        if not request.user.is_admin():
            return Response(
                {"error": "Only admins can assign leads."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Handle unassign (user_id is None or null)
        if user_id is None:
            target_user = None
            new_assigned_name = "Unassigned"
        else:
            try:
                target_user = User.objects.get(pk=user_id, company=request.user.company)
                new_assigned_name = target_user.get_full_name() or target_user.username
            except User.DoesNotExist:
                return Response(
                    {"error": "User not found or does not belong to your company."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        clients_to_update = Client.objects.filter(
            id__in=client_ids, company=request.user.company
        )
        
        from django.utils import timezone
        
        updated_count = 0
        for client in clients_to_update:
            old_assigned_name = client.assigned_to.get_full_name() or client.assigned_to.username if client.assigned_to else "Unassigned"
            
            # Check if assignment actually changed
            if client.assigned_to != target_user:
                client.assigned_to = target_user
                if target_user:
                    client.assigned_at = timezone.now()
                else:
                    # When unassigning, clear assigned_at
                    client.assigned_at = None
                client.save(update_fields=['assigned_to', 'assigned_at'])
                updated_count += 1
                
                # Format notes based on assignment/unassignment
                if target_user:
                    notes = f"Bulk assigned to {new_assigned_name} (was {old_assigned_name})"
                else:
                    notes = f"Unassigned (was {old_assigned_name})"
                
                # Record the event
                ClientEvent.objects.create(
                    client=client,
                    event_type='assignment',
                    old_value=old_assigned_name,
                    new_value=new_assigned_name,
                    notes=notes,
                    created_by=request.user
                )

        action_text = "assigned" if target_user else "unassigned"
        return Response(
            {"message": f"Successfully {action_text} {updated_count} lead(s)."},
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

        if user.is_supervisor() and user.supervisor_has_permission("manage_deals"):
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

        if user.is_supervisor() and user.supervisor_has_permission("manage_tasks"):
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
    Admin and supervisor (with can_manage_leads) can manage; employees can read (GET) only.
    """

    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorLeadsOrReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name", "budget"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(company=user.company)

        if user.is_employee():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        
        # البحث عن آخر campaign لهذه الشركة مع code يبدأ بـ CAMP
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
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"CAMP{str(new_num).zfill(3)}"
            if not Campaign.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
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

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(client__company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return ClientTaskListSerializer
        return ClientTaskSerializer


class ClientCallViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing ClientCall instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = ClientCall.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessClient]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "call_method__name", "client__name"]
    ordering_fields = ["created_at", "follow_up_date", "call_method__name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(client__company=user.company)

        if user.is_employee():
            return queryset.filter(client__assigned_to=user)

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(client__company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return ClientCallListSerializer
        return ClientCallSerializer


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
