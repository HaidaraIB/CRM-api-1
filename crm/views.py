from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import (
    CanAccessClient, CanAccessDeal, CanAccessTask, DenyDataEntryNonLeadAPI,
    IsAdmin, HasActiveSubscription, IsAdminOrReadOnlyForEmployee,
    IsAdminOrSupervisorLeadsOrReadOnlyForEmployee,
)
from crm_saas_api.responses import success_response, error_response
from crm_saas_api.utils import clean_int_query_param
from .models import Client, Deal, Task, Campaign, ClientTask, ClientCall, ClientVisit, ClientEvent
from accounts.models import User, Role
from notifications.models import NotificationType
from notifications.services import NotificationService
from notifications.team_activity import notify_owner_team_activity
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
    ClientVisitSerializer,
    ClientVisitListSerializer,
    ClientEventSerializer,
)


class ClientViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Client instances (CRUD)."""

    queryset = Client.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessClient]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "name",
        "phone_number",
        "phone_numbers__phone_number",
        "priority",
        "type",
        "communication_way__name",
        "status__name",
        "notes",
    ]
    ordering_fields = ["created_at", "name", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "company", "assigned_to", "created_by", "communication_way", "status", "campaign",
            "integration_account",
        ).prefetch_related(
            "phone_numbers",
            "client_tasks__stage",
            "client_calls__call_method",
            "client_visits__visit_type",
        )

        if user.is_admin():
            return queryset.filter(company=user.company).distinct()

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(company=user.company).distinct()

        if user.is_data_entry():
            return queryset.filter(company=user.company).distinct()

        if user.is_employee():
            return queryset.filter(assigned_to=user).distinct()

        return queryset.none()

    def get_serializer_class(self):
        if self.action == "list":
            return ClientListSerializer
        return ClientSerializer

    def perform_create(self, serializer):
        """Enforce plan quota for clients (leads) before creating."""
        user = self.request.user
        company = getattr(user, "company", None)
        if company and not user.is_super_admin():
            from subscriptions.entitlements import require_quota

            current_clients = Client.objects.filter(company=company).count()
            require_quota(
                company,
                "max_clients",
                current_count=current_clients,
                requested_delta=1,
                message="You have reached your plan leads limit. Please upgrade your plan to add more leads.",
                error_key="plan_quota_max_clients_exceeded",
            )
        serializer.save(company=company, created_by=user)
        client = serializer.instance
        if user.is_data_entry() and company and client and not client.assigned_to:
            from crm.signals import get_next_data_entry_round_robin_employee
            from django.utils import timezone as dj_tz

            assignee = get_next_data_entry_round_robin_employee(company)
            if not assignee:
                assignee = company.owner
            if assignee:
                client.assigned_to = assignee
                client.assigned_at = dj_tz.now()
                client.save(update_fields=["assigned_to", "assigned_at"])

        if not company or not client:
            return

        if client.campaign and user != company.owner:
            campaign_name = client.campaign.name
            NotificationService.send_notification(
                user=company.owner,
                notification_type=NotificationType.NEW_LEAD,
                data={
                    "lead_id": client.id,
                    "lead_name": client.name,
                    "campaign_name": campaign_name,
                },
                sender_role=getattr(user, "role", None),
            )
            return

        notify_owner_team_activity(
            user,
            company,
            action="lead_created",
            lead_id=client.id,
            lead_name=client.name,
        )

    @action(detail=False, methods=["post"])
    def assign_unassigned(self, request):
        """Auto-assign all unassigned clients for companies with auto_assign_enabled."""
        from crm.signals import get_least_busy_employee
        from django.utils import timezone

        user = request.user
        company = user.company

        if not company:
            return error_response("You must belong to a company.", code="no_company")

        if not user.is_admin():
            return error_response(
                "Only admins can assign unassigned clients.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        unassigned_clients = list(
            Client.objects.filter(company=company, assigned_to__isnull=True)
        )

        if not unassigned_clients:
            return success_response(
                data={"assigned_count": 0},
                message="No unassigned clients found.",
            )

        if not company.auto_assign_enabled:
            return error_response(
                "Auto assign is not enabled for your company.",
                code="auto_assign_disabled",
            )

        has_employees = User.objects.filter(
            company=company, role=Role.EMPLOYEE.value, is_active=True
        ).exists()
        employee = get_least_busy_employee(company)
        if not employee:
            if has_employees:
                return error_response(
                    "No employees are available for assignment today (weekly day off).",
                    code="no_available_employees_day_off",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            return error_response(
                "No active employees found in your company.",
                code="no_employees",
            )

        now = timezone.now()
        employee_name = employee.get_full_name() or employee.username
        events_to_create = []

        for client in unassigned_clients:
            client.assigned_to = employee
            client.assigned_at = now
            events_to_create.append(
                ClientEvent(
                    client=client,
                    event_type="assignment",
                    old_value="Unassigned",
                    new_value=employee_name,
                    notes=f"Auto-assigned to {employee_name}",
                    created_by=user,
                )
            )

        Client.objects.bulk_update(unassigned_clients, ["assigned_to", "assigned_at"])
        ClientEvent.objects.bulk_create(events_to_create)

        return success_response(
            data={
                "assigned_count": len(unassigned_clients),
                "assigned_to": employee_name,
            },
            message=f"Successfully assigned {len(unassigned_clients)} client(s) to {employee_name}.",
        )

    @action(detail=False, methods=["post"])
    def bulk_assign(self, request):
        """Assign multiple clients to a specific user or unassign them."""
        from crm.availability import user_accepts_new_assignments
        from django.utils import timezone

        client_ids = request.data.get("client_ids", [])
        user_id = request.data.get("user_id")

        if not client_ids:
            return error_response("client_ids is required.", code="missing_field")

        if not request.user.is_admin():
            return error_response(
                "Only admins can assign leads.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if user_id is None:
            target_user = None
            new_assigned_name = "Unassigned"
        else:
            try:
                target_user = User.objects.get(pk=user_id, company=request.user.company)
                new_assigned_name = target_user.get_full_name() or target_user.username
            except User.DoesNotExist:
                return error_response(
                    "User not found or does not belong to your company.",
                    code="not_found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            if not user_accepts_new_assignments(target_user):
                return error_response(
                    "Cannot assign to this user on their weekly day off.",
                    code="employee_weekly_day_off",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        clients_to_update = list(
            Client.objects.filter(id__in=client_ids, company=request.user.company)
        )

        now = timezone.now()
        changed_clients = []
        events_to_create = []

        for client in clients_to_update:
            if client.assigned_to == target_user:
                continue
            old_assigned_name = (
                client.assigned_to.get_full_name() or client.assigned_to.username
            ) if client.assigned_to else "Unassigned"

            client.assigned_to = target_user
            client.assigned_at = now if target_user else None
            changed_clients.append(client)

            notes = (
                f"Bulk assigned to {new_assigned_name} (was {old_assigned_name})"
                if target_user
                else f"Unassigned (was {old_assigned_name})"
            )
            events_to_create.append(
                ClientEvent(
                    client=client,
                    event_type="assignment",
                    old_value=old_assigned_name,
                    new_value=new_assigned_name,
                    notes=notes,
                    created_by=request.user,
                )
            )

        if changed_clients:
            Client.objects.bulk_update(changed_clients, ["assigned_to", "assigned_at"])
            ClientEvent.objects.bulk_create(events_to_create)

        action_text = "assigned" if target_user else "unassigned"
        return success_response(
            data={"updated_count": len(changed_clients)},
            message=f"Successfully {action_text} {len(changed_clients)} lead(s).",
        )


class DealViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Deal instances (CRUD)."""

    queryset = Deal.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, CanAccessDeal,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["client__name", "stage", "company__name"]
    ordering_fields = ["created_at", "updated_at", "stage"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "company", "employee", "started_by", "closed_by",
            "unit", "project",
        )

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

    def perform_create(self, serializer):
        user = self.request.user
        company = getattr(user, "company", None)
        if company and not user.is_super_admin():
            from subscriptions.entitlements import require_quota

            current_deals = Deal.objects.filter(company=company).count()
            require_quota(
                company,
                "max_deals",
                current_count=current_deals,
                requested_delta=1,
                message="You have reached your plan deals limit. Please upgrade your plan to add more deals.",
                error_key="plan_quota_max_deals_exceeded",
            )
        serializer.save()


class TaskViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Task instances (CRUD)."""

    queryset = Task.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, CanAccessTask,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage__name", "deal__client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage__name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "deal", "deal__company", "deal__client", "stage",
        )

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
    """ViewSet for managing Campaign instances (CRUD)."""

    queryset = Campaign.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, IsAdminOrSupervisorLeadsOrReadOnlyForEmployee]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name", "budget"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related("company")

        if user.is_admin():
            return queryset.filter(company=user.company)

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(company=user.company)

        if user.is_employee():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        """Auto-generate a unique campaign code."""
        company = serializer.validated_data["company"]

        last_campaign = Campaign.objects.filter(
            company=company,
            code__startswith="CAMP",
        ).order_by("-id").first()

        new_num = 1
        if last_campaign and last_campaign.code:
            try:
                code_suffix = last_campaign.code.replace("CAMP", "").strip()
                if code_suffix:
                    new_num = int(code_suffix) + 1
            except (ValueError, AttributeError):
                new_num = 1

        max_attempts = 1000
        new_code = None
        for _ in range(max_attempts):
            candidate_code = f"CAMP{str(new_num).zfill(3)}"
            if not Campaign.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1

        if not new_code:
            raise ValueError("Unable to generate unique campaign code")

        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return CampaignListSerializer
        return CampaignSerializer


class ClientTaskViewSet(viewsets.ModelViewSet):
    """ViewSet for managing ClientTask instances (CRUD)."""

    queryset = ClientTask.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage__name", "client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage__name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "client__company", "stage", "created_by",
        )

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


class ClientVisitViewSet(viewsets.ModelViewSet):
    """ViewSet for managing ClientVisit instances (real_estate / services)."""

    queryset = ClientVisit.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["summary", "visit_type__name", "client__name"]
    ordering_fields = ["created_at", "visit_datetime", "visit_type__name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "client__company", "visit_type", "created_by",
        )

        if user.is_admin():
            queryset = queryset.filter(client__company=user.company)
        elif user.is_employee():
            queryset = queryset.filter(client__assigned_to=user)
        elif user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            queryset = queryset.filter(client__company=user.company)
        else:
            queryset = queryset.none()

        client_id = clean_int_query_param(self.request, "client")
        if client_id is not None:
            queryset = queryset.filter(client_id=client_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return ClientVisitListSerializer
        return ClientVisitSerializer


class ClientCallViewSet(viewsets.ModelViewSet):
    """ViewSet for managing ClientCall instances (CRUD)."""

    queryset = ClientCall.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "call_method__name", "client__name"]
    ordering_fields = ["created_at", "follow_up_date", "call_method__name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "client__company", "call_method", "created_by",
        )

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
    """ViewSet for viewing ClientEvent instances (read-only)."""

    queryset = ClientEvent.objects.all()
    serializer_class = ClientEventSerializer
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["event_type", "notes", "client__name"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "client__company", "created_by",
        )

        client_id = clean_int_query_param(self.request, "client")
        if client_id is not None:
            queryset = queryset.filter(client_id=client_id)

        if user.is_admin():
            return queryset.filter(client__company=user.company)

        if user.is_employee():
            return queryset.filter(client__assigned_to=user)

        return queryset.none()
