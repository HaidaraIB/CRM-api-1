from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import mixins, viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import (
    CanAccessClient, CanAccessDeal, CanAccessTask, DenyDataEntryNonLeadAPI,
    DenyCallCenterNonLeadAPI, DenyCallCenterWriteExceptCreate, CanAnnounceLeadArrival,
    IsAdmin, HasActiveSubscription, IsAdminOrReadOnlyForEmployee,
    IsAdminOrSupervisorLeadsOrReadOnlyForEmployee,
)
from crm_saas_api.responses import success_response, error_response
from crm_saas_api.utils import clean_int_query_param
from .arrivals import announce_arrival, acknowledge_arrival, ArrivalCooldownActive
from .models import (
    Client,
    Deal,
    Task,
    Campaign,
    ClientTask,
    ClientCall,
    ClientVisit,
    ClientFieldVisit,
    ClientEvent,
    LeadArrival,
)
from accounts.models import User, Role
from notifications.models import NotificationType
from notifications.services import NotificationService
from settings.models import LeadStatus
from .client_list_filters import apply_client_list_filters
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
    ClientFieldVisitSerializer,
    ClientFieldVisitListSerializer,
    ClientEventSerializer,
    LeadArrivalSerializer,
)


class ClientViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Client instances (CRUD)."""

    queryset = Client.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyCallCenterWriteExceptCreate, CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "name",
        "phone_number",
        "phone_numbers__phone_number",
        "priority",
        "type",
        "communication_way__name",
        "status__name",
        "tags__name",
        "notes",
        "residence",
        "lead_company_name",
        "profession",
        "source",
        "campaign__name",
    ]
    ordering_fields = ["created_at", "name", "priority"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        aggregate_actions = {
            "mission_bar_summary",
            "dashboard_summary",
            "status_counts",
        }
        if getattr(self, "action", None) in aggregate_actions:
            queryset = super().get_queryset().select_related(
                "company", "assigned_to", "status",
            )
        else:
            queryset = super().get_queryset().select_related(
                "company", "assigned_to", "created_by", "communication_way", "status", "campaign",
                "integration_account",
                "interested_developer", "interested_project", "interested_unit",
            ).prefetch_related(
                "phone_numbers",
                "tags",
                "client_tasks__stage",
                "client_calls__call_method",
                "client_visits__visit_type",
                "client_field_visits",
            )

        if user.is_admin() or user.is_reception():
            return queryset.filter(company=user.company).distinct()

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(company=user.company).distinct()

        if user.is_data_entry():
            return queryset.filter(company=user.company).distinct()

        if user.is_call_center():
            return queryset.filter(company=user.company).distinct()

        if user.is_assigned_clinical_staff():
            return queryset.filter(assigned_to=user).distinct()

        return queryset.none()

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if self.action in ("list", "status_counts"):
            exclude_status = self.action == "status_counts"
            queryset = apply_client_list_filters(
                queryset, self.request, exclude_status=exclude_status
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return ClientListSerializer
        return ClientSerializer

    def destroy(self, request, *args, **kwargs):
        """
        Delete a client (customer). Non-admins require can_delete_clients;
        CanAccessClient still enforces assignment / supervisor scope.
        """
        user = request.user
        if not user.is_admin() and not getattr(user, "can_delete_clients", False):
            return error_response(
                "You do not have permission to delete customers.",
                code="cannot_delete_clients",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        self._urgent_assignment_warning = None
        response = super().create(request, *args, **kwargs)
        warning = getattr(self, "_urgent_assignment_warning", None)
        if warning and isinstance(response.data, dict):
            response.data = {
                **response.data,
                "urgent_assignment_warning": warning,
            }
        return response

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

        save_kwargs = {"company": company, "created_by": user}
        is_urgent = bool(serializer.validated_data.get("is_urgent", False))
        urgent_warning = None

        if is_urgent and company:
            from crm.assignment import get_urgent_on_shift_employee

            on_shift = get_urgent_on_shift_employee(company)
            if on_shift:
                # Urgent on-shift pick wins over manual assigned_to on create.
                save_kwargs["assigned_to"] = on_shift
                save_kwargs["assigned_at"] = timezone.now()
            else:
                urgent_warning = (
                    "No employee is currently within working hours; "
                    "assigned via normal rules."
                )

        serializer.save(**save_kwargs)
        self._urgent_assignment_warning = urgent_warning

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
                client._notification_actor = user
                client.save(update_fields=["assigned_to", "assigned_at"])

        if not company or not client:
            return

        # Owner always gets NEW_LEAD for company-wide new clients (any source).
        # Skip when the owner creates the lead themselves.
        owner = getattr(company, "owner", None)
        if owner is not None and user.pk != owner.pk:
            campaign_name = client.campaign.name if client.campaign_id else ""
            added_by = (user.get_full_name() or user.username or "").strip()

            NotificationService.send_notification(
                user=owner,
                notification_type=NotificationType.NEW_LEAD,
                data={
                    "lead_id": client.id,
                    "lead_name": client.name,
                    "campaign_name": campaign_name,
                    "added_by": added_by,
                },
                sender_role=getattr(user, "role", None),
            )

    @action(detail=False, methods=["get"], url_path="status-counts")
    def status_counts(self, request):
        """Return global per-status lead counts for the current filter set (excluding status)."""
        queryset = self.filter_queryset(self.get_queryset())
        company = getattr(request.user, "company", None)

        client_ids = queryset.values_list("pk", flat=True).distinct()
        agg_qs = Client.objects.filter(pk__in=client_ids)
        counts_by_name = {
            row["status__name"]: row["count"]
            for row in agg_qs.values("status__name").annotate(count=Count("pk"))
            if row["status__name"]
        }

        result = {"All": agg_qs.count()}
        if company:
            status_names = (
                LeadStatus.objects.filter(company=company, is_active=True, is_hidden=False)
                .order_by("-is_default", "name")
                .values_list("name", flat=True)
            )
            for name in status_names:
                result[name] = counts_by_name.get(name, 0)
        else:
            for name, count in counts_by_name.items():
                result[name] = count

        return Response(result)

    @action(detail=False, methods=["get"], url_path="mission-bar-summary")
    def mission_bar_summary(self, request):
        """Return dashboard mission bar counts (role-scoped)."""
        from crm.dashboard_summary import build_mission_bar

        return Response(build_mission_bar(request.user, self.get_queryset()))

    @action(detail=False, methods=["get"], url_path="dashboard-summary")
    def dashboard_summary(self, request):
        """Return role-scoped dashboard aggregates (replaces full-list client sync)."""
        from crm.dashboard_summary import build_dashboard_summary

        try:
            days = int(request.query_params.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        source = (request.query_params.get("source") or "all").strip().lower()
        try:
            daily_target = int(request.query_params.get("daily_target") or 5)
        except (TypeError, ValueError):
            daily_target = 5

        payload = build_dashboard_summary(
            request.user,
            self.get_queryset(),
            days=days,
            source=source,
            daily_target=daily_target,
            lite=str(request.query_params.get("lite") or "").lower() in ("1", "true", "yes"),
        )
        return Response(payload)

    @action(detail=False, methods=["post"])
    def assign_unassigned(self, request):
        """Assign each unassigned client to the current least-busy employee (one pick per lead)."""
        from crm.assignment import has_assignable_employee
        from crm.services import distribute_clients_to_least_busy

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
            Client.objects.filter(
                company=company, assigned_to__isnull=True
            ).select_related("status")
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

        role_filter = [Role.EMPLOYEE.value]
        if getattr(company, "specialization", None) == "medical":
            role_filter.append(Role.DOCTOR.value)

        has_employees = User.objects.filter(
            company=company, role__in=role_filter, is_active=True
        ).exists()
        if not has_employees:
            return error_response(
                "No active employees found in your company.",
                code="no_employees",
            )
        if not has_assignable_employee(company):
            return error_response(
                "No employees are available for assignment today (weekly day off).",
                code="no_available_employees_day_off",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        result = distribute_clients_to_least_busy(
            company,
            unassigned_clients,
            user,
            event_notes=lambda _c, _e, _old, new_name: f"Auto-assigned to {new_name}",
        )
        assigned_count = result["assigned_count"]
        assignee_names = result["assignee_names"]

        if assigned_count == 0:
            return error_response(
                "No employees are available for assignment today (weekly day off).",
                code="no_available_employees_day_off",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        assignee_summary = (
            assignee_names[0] if len(assignee_names) == 1 else ", ".join(assignee_names)
        )

        return success_response(
            data={
                "assigned_count": assigned_count,
                "assigned_to": assignee_summary,
                "assignments": result["assignments"],
            },
            message=(
                f"Successfully assigned {assigned_count} client(s) using least-busy "
                f"distribution ({assignee_summary})."
            ),
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
            Client.objects.filter(
                id__in=client_ids, company=request.user.company
            ).select_related("assigned_to")
        )

        now = timezone.now()
        changed_clients = []
        events_to_create = []
        notification_changes = []

        for client in clients_to_update:
            if client.assigned_to == target_user:
                continue
            old_assignee = client.assigned_to
            old_assigned_name = (
                old_assignee.get_full_name() or old_assignee.username
            ) if old_assignee else "Unassigned"

            client.assigned_to = target_user
            client.assigned_at = now if target_user else None
            changed_clients.append(client)
            notification_changes.append((client, old_assignee))

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
            from crm.signals import notify_lead_assignment_change

            Client.objects.bulk_update(changed_clients, ["assigned_to", "assigned_at"])
            ClientEvent.objects.bulk_create(events_to_create)
            for client, old_assignee in notification_changes:
                notify_lead_assignment_change(
                    client=client,
                    old_assignee=old_assignee,
                    new_assignee=target_user,
                    actor=request.user,
                )

        action_text = "assigned" if target_user else "unassigned"
        return success_response(
            data={"updated_count": len(changed_clients)},
            message=f"Successfully {action_text} {len(changed_clients)} lead(s).",
        )


class DealViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Deal instances (CRUD)."""

    queryset = Deal.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, DenyCallCenterNonLeadAPI, CanAccessDeal,
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
            queryset = queryset.filter(company=user.company)
        elif user.is_supervisor() and user.supervisor_has_permission("manage_deals"):
            queryset = queryset.filter(company=user.company)
        elif user.is_employee():
            queryset = queryset.filter(employee=user)
        else:
            return queryset.none()

        stage = self.request.query_params.get("stage")
        if stage:
            queryset = queryset.filter(stage=stage)

        return queryset

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
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, DenyCallCenterNonLeadAPI, CanAccessTask,
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

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        """Mark this deal task as done so it leaves the Active Todos queue."""
        task = self.get_object()
        if task.completed_at is None:
            task.completed_at = timezone.now()
            task.save(update_fields=["completed_at", "updated_at"])
        serializer = self.get_serializer(task)
        return Response(serializer.data)


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

        if user.is_employee() or user.is_doctor():
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
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, DenyCallCenterNonLeadAPI, CanAccessClient,
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

        if user.is_admin() or user.is_reception():
            queryset = queryset.filter(client__company=user.company)
        elif user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            queryset = queryset.filter(client__company=user.company)
        elif user.is_assigned_clinical_staff():
            queryset = queryset.filter(client__assigned_to=user)
        else:
            queryset = queryset.none()

        client_id = clean_int_query_param(self.request, "client")
        if client_id is not None:
            queryset = queryset.filter(client_id=client_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="complete-reminder")
    def complete_reminder(self, request, pk=None):
        """Mark this client task reminder as done so it leaves calendar/Todos queues."""
        task = self.get_object()
        if not task.reminder_date:
            return error_response(
                "This task has no reminder to complete.",
                code="no_reminder",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if task.reminder_completed_at is None:
            task.reminder_completed_at = timezone.now()
            task.save(update_fields=["reminder_completed_at", "updated_at"])
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    def get_serializer_class(self):
        if self.action == "list":
            return ClientTaskListSerializer
        return ClientTaskSerializer


class ClientVisitViewSet(viewsets.ModelViewSet):
    """ViewSet for managing ClientVisit instances (real_estate / services)."""

    queryset = ClientVisit.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, DenyCallCenterNonLeadAPI, CanAccessClient,
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

        if user.is_admin() or user.is_reception():
            queryset = queryset.filter(client__company=user.company)
        elif user.is_assigned_clinical_staff():
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


class ClientFieldVisitViewSet(viewsets.ModelViewSet):
    """ViewSet for field visits (الزيارة الميدانية); all company specializations."""

    queryset = ClientFieldVisit.objects.all()
    permission_classes = [
        IsAuthenticated,
        HasActiveSubscription,
        DenyDataEntryNonLeadAPI,
        DenyCallCenterNonLeadAPI,
        CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["summary", "client__name"]
    ordering_fields = ["created_at", "visit_datetime"]
    ordering = ["-created_at"]

    def _field_visit_feature_error(self):
        from settings.feature_policy import get_field_visit_access

        company = self.request.user.company
        if not company:
            return error_response(
                "Company is required.",
                code="company_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        access = get_field_visit_access(company)
        if access.get("enabled"):
            return None
        message = access.get("message") or "Field visits are not enabled for your company."
        return error_response(
            message,
            code="field_visit_disabled",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def list(self, request, *args, **kwargs):
        blocked = self._field_visit_feature_error()
        if blocked:
            return blocked
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        blocked = self._field_visit_feature_error()
        if blocked:
            return blocked
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        blocked = self._field_visit_feature_error()
        if blocked:
            return blocked
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "client__company", "created_by",
        )

        if user.is_admin() or user.is_reception():
            queryset = queryset.filter(client__company=user.company)
        elif user.is_assigned_clinical_staff():
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
            return ClientFieldVisitListSerializer
        return ClientFieldVisitSerializer


class ClientCallViewSet(viewsets.ModelViewSet):
    """ViewSet for managing ClientCall instances (CRUD)."""

    queryset = ClientCall.objects.all()
    permission_classes = [
        IsAuthenticated, HasActiveSubscription, DenyDataEntryNonLeadAPI, DenyCallCenterNonLeadAPI, CanAccessClient,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "call_method__name", "client__name"]
    ordering_fields = ["created_at", "follow_up_date", "call_method__name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related(
            "client", "client__company", "call_method", "created_by", "pbx_call_record",
        ).prefetch_related("whatsapp_calls")

        if user.is_admin() or user.is_reception():
            queryset = queryset.filter(client__company=user.company)
        elif user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            queryset = queryset.filter(client__company=user.company)
        elif user.is_assigned_clinical_staff():
            queryset = queryset.filter(client__assigned_to=user)
        else:
            queryset = queryset.none()

        client_id = clean_int_query_param(self.request, "client")
        if client_id is not None:
            queryset = queryset.filter(client_id=client_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="complete-follow-up")
    def complete_follow_up(self, request, pk=None):
        """Mark this call follow-up as done so it leaves calendar/Todos queues."""
        call = self.get_object()
        if not call.follow_up_date:
            return error_response(
                "This call has no follow-up to complete.",
                code="no_follow_up",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if call.follow_up_completed_at is None:
            call.follow_up_completed_at = timezone.now()
            call.save(update_fields=["follow_up_completed_at", "updated_at"])
        serializer = self.get_serializer(call)
        return Response(serializer.data)

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

        if user.is_admin() or user.is_reception():
            return queryset.filter(client__company=user.company)

        if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
            return queryset.filter(client__company=user.company)

        if user.is_call_center():
            return queryset.filter(client__company=user.company)

        if user.is_assigned_clinical_staff():
            return queryset.filter(client__assigned_to=user)

        return queryset.none()


class LeadArrivalViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Walk-in "customer arrived" announcements (CALL_CENTER front desk).

    POST /lead-arrivals/               -> announce (create() override)
    POST /lead-arrivals/{id}/acknowledge/ -> acknowledge
    GET  /lead-arrivals/                -> today's company board (or ?date=)
    GET  /lead-arrivals/pending/        -> current user's unacknowledged arrivals
    """

    queryset = LeadArrival.objects.all()
    serializer_class = LeadArrivalSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAnnounceLeadArrival]

    def get_queryset(self):
        user = self.request.user
        company = getattr(user, "company", None)
        queryset = (
            super()
            .get_queryset()
            .select_related("client", "announced_by", "acknowledged_by", "company")
            .prefetch_related("notified_users")
            .filter(company=company)
        )

        if (
            user.is_admin()
            or user.is_reception()
            or user.is_call_center()
            or (user.is_supervisor() and user.supervisor_has_permission("manage_leads"))
        ):
            pass  # company-wide, already filtered above
        elif user.is_assigned_clinical_staff():
            queryset = queryset.filter(
                Q(notified_users=user) | Q(client__assigned_to=user)
            ).distinct()
        else:
            return queryset.none()

        if self.action == "list":
            date_param = self.request.query_params.get("date")
            from crm.availability import local_now_for_company

            local_now = local_now_for_company(company)
            if date_param:
                try:
                    target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                except ValueError:
                    target_date = local_now.date()
            else:
                target_date = local_now.date()
            tz = local_now.tzinfo
            day_start = datetime.combine(target_date, time.min, tzinfo=tz)
            day_end = day_start + timedelta(days=1)
            queryset = queryset.filter(announced_at__gte=day_start, announced_at__lt=day_end)

            status_param = self.request.query_params.get("status")
            if status_param == "waiting":
                queryset = queryset.filter(acknowledged_at__isnull=True, escalated_at__isnull=True)
            elif status_param == "acknowledged":
                queryset = queryset.filter(acknowledged_at__isnull=False)
            elif status_param == "escalated":
                queryset = queryset.filter(acknowledged_at__isnull=True, escalated_at__isnull=False)

            if self.request.query_params.get("mine"):
                queryset = queryset.filter(
                    Q(notified_users=user) | Q(announced_by=user)
                ).distinct()

        return queryset

    def create(self, request, *args, **kwargs):
        client_id = request.data.get("client")
        if not client_id:
            return error_response(
                "client is required.", code="client_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        notes = request.data.get("notes") or ""

        try:
            arrival = announce_arrival(
                client_id=client_id,
                company=request.user.company,
                actor=request.user,
                notes=notes,
            )
        except Client.DoesNotExist:
            return error_response(
                "Lead not found.", code="lead_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except ArrivalCooldownActive as exc:
            return error_response(
                "This customer's arrival was already announced recently.",
                code="arrival_cooldown_active",
                details={"arrival": LeadArrivalSerializer(exc.existing_arrival).data},
                status_code=status.HTTP_409_CONFLICT,
            )

        serializer = self.get_serializer(arrival)
        return success_response(serializer.data, status_code=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        arrival = self.get_object()
        user = request.user
        is_notified = arrival.notified_users.filter(pk=user.pk).exists()
        # Anyone who can see the company-wide board may confirm receipt on the
        # recipient's behalf (e.g. the desk verbally confirmed the employee got it).
        is_privileged = (
            user.is_admin()
            or user.is_call_center()
            or user.is_reception()
            or (user.is_supervisor() and user.supervisor_has_permission("manage_leads"))
        )
        if not (is_notified or is_privileged):
            return error_response(
                "You are not a recipient of this arrival.",
                code="not_arrival_recipient",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        updated = acknowledge_arrival(arrival=arrival, actor=user)
        serializer = self.get_serializer(updated)
        return success_response(serializer.data)

    @action(detail=False, methods=["get"])
    def pending(self, request):
        user = request.user
        queryset = (
            LeadArrival.objects.filter(
                company=user.company,
                notified_users=user,
                acknowledged_at__isnull=True,
                announced_at__gte=timezone.now() - timedelta(hours=2),
            )
            .select_related("client", "announced_by")
            .prefetch_related("notified_users")
            .distinct()
            .order_by("-announced_at")
        )
        serializer = self.get_serializer(queryset, many=True)
        return success_response(serializer.data)


from rest_framework.decorators import api_view, permission_classes


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def feature_policy_view(request):
    """Effective feature access for the authenticated user's company."""
    from settings.feature_policy import (
        FEATURE_POLICY_KEYS,
        FIELD_VISIT_FEATURE,
        get_effective_feature_policy,
        get_field_visit_access,
    )
    from settings.models import SystemSettings

    company = request.user.company
    if not company:
        return error_response(
            "Company is required.",
            code="company_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    settings_obj = SystemSettings.get_settings()
    policies = settings_obj.feature_policies or {}
    data = {}
    for feature_key in FEATURE_POLICY_KEYS:
        if feature_key == FIELD_VISIT_FEATURE:
            data[feature_key] = get_field_visit_access(company)
        else:
            data[feature_key] = get_effective_feature_policy(
                policies,
                company_id=company.id,
                feature_key=feature_key,
            )
    return success_response(data=data)
