from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer

from crm.availability import user_accepts_new_assignments
from .models import (
    Client,
    Deal,
    Task,
    Campaign,
    ClientTask,
    ClientCall,
    ClientVisit,
    ClientPhoneNumber,
    ClientEvent,
)


class ClientActivitySummaryMixin:
    """Expose the latest visible activity for clients across tasks, calls, and visits."""

    last_feedback = serializers.SerializerMethodField()
    last_stage = serializers.SerializerMethodField()
    last_feedback_at = serializers.SerializerMethodField()

    @staticmethod
    def _get_latest_activity(client):
        latest_task = client.client_tasks.order_by("-created_at").first()
        latest_call = client.client_calls.order_by("-created_at").first()
        latest_visit = client.client_visits.order_by("-created_at").first()

        candidates = []
        if latest_task:
            candidates.append(("task", latest_task, latest_task.created_at))
        if latest_call:
            candidates.append(("call", latest_call, latest_call.created_at))
        if latest_visit:
            candidates.append(("visit", latest_visit, latest_visit.created_at))
        if not candidates:
            return (None, None)
        candidates.sort(key=lambda x: x[2], reverse=True)
        kind, obj, _ = candidates[0]
        return (kind, obj)

    def get_last_feedback(self, obj):
        activity_type, activity = self._get_latest_activity(obj)
        if not activity:
            return None
        if activity_type == "visit":
            summary = getattr(activity, "summary", None)
            if summary:
                return summary
            visit_type = getattr(activity, "visit_type", None)
            return getattr(visit_type, "name", None) if visit_type else None
        notes = getattr(activity, "notes", None)
        if notes:
            return notes
        if activity_type == "task":
            stage = getattr(activity, "stage", None)
            return getattr(stage, "name", None) if stage else None
        call_method = getattr(activity, "call_method", None)
        return getattr(call_method, "name", None) if call_method else None

    def get_last_stage(self, obj):
        activity_type, activity = self._get_latest_activity(obj)
        if not activity:
            return None
        if activity_type == "task":
            stage = getattr(activity, "stage", None)
            return getattr(stage, "name", None) if stage else None
        if activity_type == "visit":
            visit_type = getattr(activity, "visit_type", None)
            return getattr(visit_type, "name", None) if visit_type else None
        call_method = getattr(activity, "call_method", None)
        return getattr(call_method, "name", None) if call_method else None

    def get_last_feedback_at(self, obj):
        _, activity = self._get_latest_activity(obj)
        return activity.created_at if activity else None


class ClientCreatorDisplayMixin(serializers.Serializer):
    """Who created the lead (API users); integrations leave created_by null."""

    created_by = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    created_by_name = serializers.SerializerMethodField()

    def get_created_by_name(self, obj):
        user = getattr(obj, "created_by", None)
        if not user:
            return None
        full = (user.get_full_name() or "").strip()
        return full or user.username


class CamelToSnakeMixin:
    """Mixin that auto-converts camelCase keys to snake_case."""
    camel_to_snake_fields = {}  # {'camelCase': 'snake_case'}

    def to_internal_value(self, data):
        if hasattr(data, "copy"):
            data = data.copy()
        elif isinstance(data, dict):
            data = dict(data)
        else:
            data = dict(data) if data else {}

        for camel, snake in getattr(self, 'camel_to_snake_fields', {}).items():
            if camel in data and snake not in data:
                data[snake] = data.pop(camel)
            elif camel in data:
                data.pop(camel, None)

        return super().to_internal_value(data)


class ClientEventSerializer(serializers.ModelSerializer):
    """Serializer for client events"""
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = ClientEvent
        fields = [
            "id",
            "client",
            "event_type",
            "old_value",
            "new_value",
            "notes",
            "created_by",
            "created_by_username",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ClientPhoneNumberSerializer(serializers.ModelSerializer):
    """Serializer for client phone numbers"""

    class Meta:
        model = ClientPhoneNumber
        fields = [
            "id",
            "phone_number",
            "phone_type",
            "is_primary",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ClientSerializer(ClientActivitySummaryMixin, ClientCreatorDisplayMixin, serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True
    )
    communication_way_name = serializers.CharField(
        source="communication_way.name", read_only=True
    )
    status_name = serializers.CharField(source="status.name", read_only=True)
    phone_numbers = ClientPhoneNumberSerializer(many=True, read_only=True)
    last_feedback = serializers.SerializerMethodField()
    last_stage = serializers.SerializerMethodField()
    last_feedback_at = serializers.SerializerMethodField()
    interested_developer_name = serializers.CharField(
        source="interested_developer.name", read_only=True, allow_null=True
    )
    interested_project_name = serializers.CharField(
        source="interested_project.name", read_only=True, allow_null=True
    )
    interested_unit_name = serializers.CharField(
        source="interested_unit.name", read_only=True, allow_null=True
    )
    interested_unit_code = serializers.CharField(
        source="interested_unit.code", read_only=True, allow_null=True
    )

    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "priority",
            "type",
            "communication_way",
            "communication_way_name",
            "status",
            "status_name",
            "budget",
            "budget_max",
            "phone_number",  # Keep for backward compatibility
            "phone_numbers",  # New field for multiple phone numbers
            "lead_company_name",
            "profession",
            "notes",
            "interested_developer",
            "interested_developer_name",
            "interested_project",
            "interested_project_name",
            "interested_unit",
            "interested_unit_name",
            "interested_unit_code",
            "company",
            "company_name",
            "assigned_to",
            "assigned_to_username",
            "campaign",
            "source",
            "integration_account",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
            "last_feedback",
            "last_stage",
            "last_feedback_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "created_by", "created_by_name"]

    def validate_communication_way(self, value):
        """Ensure communication_way belongs to the same company"""
        if value:
            # Get company from instance or initial_data
            company_id = None
            if self.instance and self.instance.company_id:
                company_id = self.instance.company_id
            elif hasattr(self, "initial_data"):
                company_id = self.initial_data.get("company")

            if company_id and value.company_id != company_id:
                raise serializers.ValidationError(
                    "Communication way must belong to the same company as the client."
                )
        return value

    def validate_status(self, value):
        """Ensure status belongs to the same company"""
        if value:
            # Get company from instance or initial_data
            company_id = None
            if self.instance and self.instance.company_id:
                company_id = self.instance.company_id
            elif hasattr(self, "initial_data"):
                company_id = self.initial_data.get("company")

            if company_id and value.company_id != company_id:
                raise serializers.ValidationError(
                    "Status must belong to the same company as the client."
                )
        return value

    def _validate_interested_real_estate(self, attrs):
        """Optional developer → project → unit; enforce company and hierarchy."""
        if "interested_developer" in attrs and attrs.get("interested_developer") is None:
            attrs["interested_project"] = None
            attrs["interested_unit"] = None
        if "interested_project" in attrs and attrs.get("interested_project") is None:
            attrs["interested_unit"] = None

        inst = self.instance

        def pick(field):
            if field in attrs:
                return attrs[field]
            if inst is not None:
                return getattr(inst, field)
            return None

        dev = pick("interested_developer")
        proj = pick("interested_project")
        unit = pick("interested_unit")

        if dev is None and proj is None and unit is None:
            return

        company = None
        if inst is not None:
            company = inst.company
        elif "company" in attrs:
            company = attrs["company"]
        else:
            req = self.context.get("request")
            if req and req.user.is_authenticated:
                company = getattr(req.user, "company", None)
        if company is None:
            raise serializers.ValidationError(
                {
                    "interested_developer": "Company is required to validate inventory interest.",
                }
            )
        cid = company.id if hasattr(company, "id") else company

        def assert_same_company(obj, field_name):
            if obj is not None and getattr(obj, "company_id", None) != cid:
                raise serializers.ValidationError(
                    {field_name: "Selection must belong to your company."}
                )

        assert_same_company(dev, "interested_developer")
        assert_same_company(proj, "interested_project")
        assert_same_company(unit, "interested_unit")

        if unit is not None:
            if proj is not None and unit.project_id != proj.id:
                raise serializers.ValidationError(
                    {"interested_unit": "Unit must belong to the selected project."}
                )
            if dev is not None and unit.project.developer_id != dev.id:
                raise serializers.ValidationError(
                    {
                        "interested_developer": "Developer must match the unit's project developer.",
                    }
                )
            attrs["interested_project"] = unit.project
            attrs["interested_developer"] = unit.project.developer
        elif proj is not None:
            if dev is not None and proj.developer_id != dev.id:
                raise serializers.ValidationError(
                    {"interested_project": "Project must belong to the selected developer."}
                )
            attrs["interested_developer"] = proj.developer

    def validate(self, attrs):
        budget = attrs["budget"] if "budget" in attrs else (self.instance.budget if self.instance else None)
        budget_max = (
            attrs["budget_max"]
            if "budget_max" in attrs
            else (self.instance.budget_max if self.instance else None)
        )
        if budget_max is not None and budget is None:
            raise serializers.ValidationError(
                {"budget": "Minimum budget is required when a maximum budget is set."}
            )
        if budget is not None and budget_max is not None and budget_max < budget:
            raise serializers.ValidationError(
                {"budget_max": "Maximum budget must be greater than or equal to minimum budget."}
            )

        self._validate_interested_real_estate(attrs)

        request = self.context.get("request")
        if request and request.user.is_authenticated and request.user.is_data_entry():
            attrs.pop("assigned_to", None)
            company = getattr(request.user, "company", None)
            if not company:
                raise serializers.ValidationError(
                    {"company": "Data entry users must belong to a company."}
                )
            # Assignment (least-busy employee, else company owner) runs in ClientViewSet.perform_create.
            return attrs

        if "assigned_to" in attrs:
            assignee = attrs.get("assigned_to")
            if assignee:
                same_as_before = (
                    self.instance is not None
                    and self.instance.assigned_to_id is not None
                    and self.instance.assigned_to_id == assignee.pk
                )
                if not same_as_before and not user_accepts_new_assignments(assignee):
                    raise serializers.ValidationError(
                        {
                            "assigned_to": "Cannot assign to this user on their weekly day off.",
                            "error_key": "employee_weekly_day_off",
                        }
                    )
        return attrs

    def create(self, validated_data):
        """Create client and handle phone numbers"""
        phone_numbers_data = self.initial_data.get("phone_numbers", [])
        
        # Don't auto-assign to current user unless explicitly provided or auto_assign is enabled
        # The signal will handle auto-assignment if enabled
        # Only set assigned_to if it's explicitly provided in the request
        request = self.context.get('request')
        if request and 'assigned_to' not in validated_data:
            # If assigned_to is not provided, set it to None
            # The auto_assign signal will handle assignment if enabled
            validated_data['assigned_to'] = None
        
        client = Client.objects.create(**validated_data)

        # Create phone numbers if provided
        if phone_numbers_data:
            for phone_data in phone_numbers_data:
                ClientPhoneNumber.objects.create(client=client, **phone_data)
        elif validated_data.get("phone_number"):
            # If old phone_number field is provided, create a primary phone number
            ClientPhoneNumber.objects.create(
                client=client,
                phone_number=validated_data["phone_number"],
                phone_type="mobile",
                is_primary=True,
            )

        return client

    def update(self, instance, validated_data):
        """Update client and handle phone numbers"""
        phone_numbers_data = self.initial_data.get("phone_numbers", None)
        request = self.context.get('request')
        user = request.user if request else None

        # Track changes for event logging
        changes = []
        
        if 'status' in validated_data:
            new_status = validated_data['status']
            if instance.status != new_status:
                old_status_name = instance.status.name if instance.status else "None"
                new_status_name = new_status.name if new_status else "None"
                changes.append({
                    'event_type': 'status_change',
                    'old_value': old_status_name,
                    'new_value': new_status_name,
                    'notes': f"Status changed from {old_status_name} to {new_status_name}"
                })

        if 'assigned_to' in validated_data:
            new_assigned = validated_data['assigned_to']
            if instance.assigned_to != new_assigned:
                # Use "Unassigned" instead of "None" for better display
                old_assigned_name = instance.assigned_to.get_full_name() or instance.assigned_to.username if instance.assigned_to else "Unassigned"
                new_assigned_name = new_assigned.get_full_name() or new_assigned.username if new_assigned else "Unassigned"
                # Update assigned_at when assignment changes
                from django.utils import timezone
                validated_data['assigned_at'] = timezone.now()
                
                # Format notes based on assignment/unassignment
                if new_assigned:
                    if instance.assigned_to:
                        notes = f"Assigned to {new_assigned_name} (was {old_assigned_name})"
                    else:
                        notes = f"Assigned to {new_assigned_name}"
                else:
                    notes = f"Unassigned (was {old_assigned_name})"
                
                changes.append({
                    'event_type': 'assignment',
                    'old_value': old_assigned_name,
                    'new_value': new_assigned_name,
                    'notes': notes
                })

        # Generic edit detection for other important fields
        other_fields = [
            'name',
            'priority',
            'type',
            'budget',
            'budget_max',
            'communication_way',
            'lead_company_name',
            'profession',
            'notes',
            'interested_developer',
            'interested_project',
            'interested_unit',
        ]
        for field in other_fields:
            if field in validated_data and getattr(instance, field) != validated_data[field]:
                field_name = field.replace('_', ' ').capitalize()
                changes.append({
                    'event_type': 'edit',
                    'old_value': str(getattr(instance, field)),
                    'new_value': str(validated_data[field]),
                    'notes': f"{field_name} updated"
                })

        # Update client fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Create event logs
        for change in changes:
            ClientEvent.objects.create(
                client=instance,
                event_type=change['event_type'],
                old_value=change['old_value'],
                new_value=change['new_value'],
                notes=change['notes'],
                created_by=user
            )

        # Update phone numbers if provided
        if phone_numbers_data is not None:
            # Delete existing phone numbers
            instance.phone_numbers.all().delete()
            # Create new phone numbers
            for phone_data in phone_numbers_data:
                ClientPhoneNumber.objects.create(client=instance, **phone_data)
        elif validated_data.get("phone_number") and not instance.phone_numbers.exists():
            # If old phone_number field is provided and no phone numbers exist, create one
            ClientPhoneNumber.objects.create(
                client=instance,
                phone_number=validated_data["phone_number"],
                phone_type="mobile",
                is_primary=True,
            )

        return instance


class ClientListSerializer(ClientActivitySummaryMixin, ClientCreatorDisplayMixin, serializers.ModelSerializer):
    """Simplified serializer for list views"""

    company_name = serializers.CharField(source="company.name", read_only=True)
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True
    )
    communication_way_name = serializers.CharField(
        source="communication_way.name", read_only=True
    )
    status_name = serializers.CharField(source="status.name", read_only=True)
    phone_numbers = ClientPhoneNumberSerializer(many=True, read_only=True)
    last_feedback = serializers.SerializerMethodField()
    last_stage = serializers.SerializerMethodField()
    last_feedback_at = serializers.SerializerMethodField()
    interested_developer_name = serializers.CharField(
        source="interested_developer.name", read_only=True, allow_null=True
    )
    interested_project_name = serializers.CharField(
        source="interested_project.name", read_only=True, allow_null=True
    )
    interested_unit_name = serializers.CharField(
        source="interested_unit.name", read_only=True, allow_null=True
    )
    interested_unit_code = serializers.CharField(
        source="interested_unit.code", read_only=True, allow_null=True
    )

    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "priority",
            "type",
            "communication_way",
            "communication_way_name",
            "status",
            "status_name",
            "budget",
            "budget_max",
            "phone_number",  # Keep for backward compatibility
            "phone_numbers",  # New field for multiple phone numbers
            "lead_company_name",
            "profession",
            "notes",
            "interested_developer",
            "interested_developer_name",
            "interested_project",
            "interested_project_name",
            "interested_unit",
            "interested_unit_name",
            "interested_unit_code",
            "company",
            "company_name",
            "assigned_to",
            "assigned_to_username",
            "campaign",
            "source",
            "integration_account",
            "created_by",
            "created_by_name",
            "created_at",
            "last_feedback",
            "last_stage",
            "last_feedback_at",
        ]


@extend_schema_serializer(component_name="Deal")
class DealSerializer(CamelToSnakeMixin, serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    employee_username = serializers.CharField(
        source="employee.username", read_only=True
    )
    started_by_username = serializers.CharField(
        source="started_by.username", read_only=True, allow_null=True
    )
    closed_by_username = serializers.CharField(
        source="closed_by.username", read_only=True, allow_null=True
    )
    unit_code = serializers.CharField(
        source="unit.code", read_only=True, allow_null=True
    )
    project_name = serializers.CharField(
        source="project.name", read_only=True, allow_null=True
    )

    camel_to_snake_fields = {
        "startedBy": "started_by",
        "closedBy": "closed_by",
        "startDate": "start_date",
        "closedDate": "closed_date",
        "paymentMethod": "payment_method",
    }

    class Meta:
        model = Deal
        fields = [
            "id",
            "client",
            "client_name",
            "company",
            "company_name",
            "employee",
            "employee_username",
            "stage",
            "payment_method",
            "status",
            "value",
            "reminder_date",
            "start_date",
            "closed_date",
            "discount_percentage",
            "discount_amount",
            "sales_commission_percentage",
            "sales_commission_amount",
            "description",
            "unit",
            "unit_code",
            "project",
            "project_name",
            "started_by",
            "started_by_username",
            "closed_by",
            "closed_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        if "employee" in attrs:
            emp = attrs.get("employee")
            if emp:
                same_as_before = (
                    self.instance is not None
                    and self.instance.employee_id is not None
                    and self.instance.employee_id == emp.pk
                )
                if not same_as_before and not user_accepts_new_assignments(emp):
                    raise serializers.ValidationError(
                        {
                            "employee": "Cannot assign to this user on their weekly day off.",
                            "error_key": "employee_weekly_day_off",
                        }
                    )
        return attrs

    def create(self, validated_data):
        """Create deal and ensure started_by and closed_by are set"""
        # If started_by is not provided, set it to the current user
        if (
            "started_by" not in validated_data
            or validated_data.get("started_by") is None
        ):
            validated_data["started_by"] = self.context["request"].user

        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Update deal"""
        return super().update(instance, validated_data)


class DealListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    client_name = serializers.CharField(source="client.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    employee_username = serializers.CharField(
        source="employee.username", read_only=True, allow_null=True
    )
    started_by_username = serializers.CharField(
        source="started_by.username", read_only=True, allow_null=True
    )
    closed_by_username = serializers.CharField(
        source="closed_by.username", read_only=True, allow_null=True
    )
    unit_code = serializers.CharField(
        source="unit.code", read_only=True, allow_null=True
    )
    project_name = serializers.CharField(
        source="project.name", read_only=True, allow_null=True
    )

    class Meta:
        model = Deal
        fields = [
            "id",
            "client",
            "client_name",
            "company",
            "company_name",
            "employee",
            "employee_username",
            "started_by",
            "started_by_username",
            "closed_by",
            "closed_by_username",
            "stage",
            "payment_method",
            "status",
            "value",
            "reminder_date",
            "start_date",
            "closed_date",
            "discount_percentage",
            "discount_amount",
            "sales_commission_percentage",
            "sales_commission_amount",
            "description",
            "unit",
            "unit_code",
            "project",
            "project_name",
            "created_at",
            "updated_at",
        ]


@extend_schema_serializer(component_name="Task")
class TaskSerializer(serializers.ModelSerializer):
    deal_client_name = serializers.CharField(source="deal.client.name", read_only=True)
    deal_stage = serializers.CharField(source="deal.stage", read_only=True)
    deal_employee_username = serializers.CharField(
        source="deal.employee.username", read_only=True, allow_null=True
    )
    stage_name = serializers.CharField(
        source="stage.name", read_only=True, allow_null=True, allow_blank=True
    )

    class Meta:
        model = Task
        fields = [
            "id",
            "deal",
            "deal_client_name",
            "deal_stage",
            "deal_employee_username",
            "stage",
            "stage_name",
            "notes",
            "reminder_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TaskListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    deal_client_name = serializers.CharField(source="deal.client.name", read_only=True)
    deal_employee_username = serializers.CharField(
        source="deal.employee.username", read_only=True, allow_null=True
    )
    stage_name = serializers.CharField(source="stage.name", read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "deal",
            "deal_client_name",
            "deal_employee_username",
            "stage",
            "stage_name",
            "notes",
            "reminder_date",
            "created_at",
        ]


@extend_schema_serializer(component_name="Campaign")
class CampaignSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "code",
            "budget",
            "is_active",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class CampaignListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "code",
            "budget",
            "is_active",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="ClientTask")
class ClientTaskSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    stage_name = serializers.CharField(source="stage.name", read_only=True)

    class Meta:
        model = ClientTask
        fields = [
            "id",
            "client",
            "client_name",
            "stage",
            "stage_name",
            "notes",
            "reminder_date",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_stage(self, value):
        """Ensure stage belongs to the same company as the client"""
        if value:
            # Get client from instance or initial_data
            client = None
            if self.instance and self.instance.client_id:
                client = self.instance.client
            elif hasattr(self, "initial_data"):
                client_id = self.initial_data.get("client")
                if client_id:
                    from .models import Client

                    try:
                        client = Client.objects.get(pk=client_id)
                    except Client.DoesNotExist:
                        pass

            if client and value.company_id != client.company_id:
                raise serializers.ValidationError(
                    "Stage must belong to the same company as the client."
                )
        return value


class ClientTaskListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    client_name = serializers.CharField(source="client.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    stage_name = serializers.CharField(source="stage.name", read_only=True)

    class Meta:
        model = ClientTask
        fields = [
            "id",
            "client",
            "client_name",
            "stage",
            "stage_name",
            "notes",
            "reminder_date",
            "created_by",
            "created_by_username",
            "created_at",
        ]


@extend_schema_serializer(component_name="ClientCall")
class ClientCallSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    call_method_name = serializers.CharField(source="call_method.name", read_only=True)

    class Meta:
        model = ClientCall
        fields = [
            "id",
            "client",
            "client_name",
            "call_method",
            "call_method_name",
            "notes",
            "call_datetime",
            "follow_up_date",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_call_method(self, value):
        """Ensure call_method belongs to the same company as the client"""
        if value:
            # Get client from instance or initial_data
            client = None
            if self.instance and self.instance.client_id:
                client = self.instance.client
            elif hasattr(self, "initial_data"):
                client_id = self.initial_data.get("client")
                if client_id:
                    from .models import Client

                    try:
                        client = Client.objects.get(pk=client_id)
                    except Client.DoesNotExist:
                        pass

            if client and value.company_id != client.company_id:
                raise serializers.ValidationError(
                    "Call method must belong to the same company as the client."
                )
        return value


@extend_schema_serializer(component_name="ClientVisit")
class ClientVisitSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    visit_type_name = serializers.CharField(source="visit_type.name", read_only=True)

    class Meta:
        model = ClientVisit
        fields = [
            "id",
            "client",
            "client_name",
            "visit_type",
            "visit_type_name",
            "summary",
            "visit_datetime",
            "upcoming_visit_date",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, data):
        request = self.context.get("request")
        company = getattr(getattr(request, "user", None), "company", None)
        if company and getattr(company, "specialization", None) not in (
            "real_estate",
            "services",
        ):
            raise serializers.ValidationError(
                "Visits are only available for real estate and services companies."
            )
        if self.instance is None:
            if not data.get("visit_datetime"):
                raise serializers.ValidationError(
                    {"visit_datetime": "This field is required."}
                )
            if not data.get("visit_type"):
                raise serializers.ValidationError(
                    {"visit_type": "This field is required."}
                )
            summary = data.get("summary")
            if summary is None or str(summary).strip() == "":
                raise serializers.ValidationError(
                    {"summary": "This field is required."}
                )
        return data

    def validate_visit_type(self, value):
        if value:
            client = None
            if self.instance and self.instance.client_id:
                client = self.instance.client
            elif hasattr(self, "initial_data"):
                client_id = self.initial_data.get("client")
                if client_id:
                    try:
                        client = Client.objects.get(pk=client_id)
                    except Client.DoesNotExist:
                        pass
            if client and value.company_id != client.company_id:
                raise serializers.ValidationError(
                    "Visit type must belong to the same company as the client."
                )
        return value


class ClientVisitListSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    visit_type_name = serializers.CharField(source="visit_type.name", read_only=True)

    class Meta:
        model = ClientVisit
        fields = [
            "id",
            "client",
            "client_name",
            "visit_type",
            "visit_type_name",
            "summary",
            "visit_datetime",
            "upcoming_visit_date",
            "created_by",
            "created_by_username",
            "created_at",
        ]


class ClientCallListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    client_name = serializers.CharField(source="client.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    call_method_name = serializers.CharField(source="call_method.name", read_only=True)

    class Meta:
        model = ClientCall
        fields = [
            "id",
            "client",
            "client_name",
            "call_method",
            "call_method_name",
            "notes",
            "call_datetime",
            "follow_up_date",
            "created_by",
            "created_by_username",
            "created_at",
        ]
