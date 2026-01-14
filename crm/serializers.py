from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import Client, Deal, Task, Campaign, ClientTask, ClientCall, ClientPhoneNumber, ClientEvent


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


class ClientSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True
    )
    communication_way_name = serializers.CharField(
        source="communication_way.name", read_only=True
    )
    status_name = serializers.CharField(source="status.name", read_only=True)
    phone_numbers = ClientPhoneNumberSerializer(many=True, read_only=True)

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
            "phone_number",  # Keep for backward compatibility
            "phone_numbers",  # New field for multiple phone numbers
            "company",
            "company_name",
            "assigned_to",
            "assigned_to_username",
            "campaign",
            "source",
            "integration_account",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

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
        other_fields = ['name', 'priority', 'type', 'budget', 'communication_way']
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


class ClientListSerializer(serializers.ModelSerializer):
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
            "phone_number",  # Keep for backward compatibility
            "phone_numbers",  # New field for multiple phone numbers
            "company",
            "company_name",
            "assigned_to",
            "assigned_to_username",
            "campaign",
            "source",
            "integration_account",
            "created_at",
        ]


@extend_schema_serializer(component_name="Deal")
class DealSerializer(serializers.ModelSerializer):
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

    def to_internal_value(self, data):
        """Convert camelCase to snake_case before validation"""
        # Create a mutable copy of data
        if hasattr(data, "copy"):
            # QueryDict or similar
            data = data.copy()
        elif isinstance(data, dict):
            data = dict(data)
        else:
            # Fallback: convert to dict
            data = dict(data) if data else {}

        # Handle camelCase fields and convert to snake_case
        # Only convert if snake_case version doesn't exist
        if "startedBy" in data and "started_by" not in data:
            data["started_by"] = data.pop("startedBy")
        elif "startedBy" in data:
            # Remove camelCase if snake_case exists
            data.pop("startedBy", None)

        if "closedBy" in data and "closed_by" not in data:
            data["closed_by"] = data.pop("closedBy")
        elif "closedBy" in data:
            data.pop("closedBy", None)

        if "startDate" in data and "start_date" not in data:
            data["start_date"] = data.pop("startDate")
        elif "startDate" in data:
            data.pop("startDate", None)

        if "closedDate" in data and "closed_date" not in data:
            data["closed_date"] = data.pop("closedDate")
        elif "closedDate" in data:
            data.pop("closedDate", None)

        if "paymentMethod" in data and "payment_method" not in data:
            data["payment_method"] = data.pop("paymentMethod")
        elif "paymentMethod" in data:
            data.pop("paymentMethod", None)

        return super().to_internal_value(data)

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
            "follow_up_date",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_follow_up_date(self, value):
        """Validate that follow_up_date is required"""
        if value is None:
            raise serializers.ValidationError("Follow up date is required for call tasks.")
        return value
    
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
            "follow_up_date",
            "created_by",
            "created_by_username",
            "created_at",
        ]
