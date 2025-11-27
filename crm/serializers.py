from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import Client, Deal, Task, Campaign, ClientTask, ClientPhoneNumber


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
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)
    communication_way_name = serializers.CharField(source="communication_way.name", read_only=True)
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
            elif hasattr(self, 'initial_data'):
                company_id = self.initial_data.get('company')
            
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
            elif hasattr(self, 'initial_data'):
                company_id = self.initial_data.get('company')
            
            if company_id and value.company_id != company_id:
                raise serializers.ValidationError(
                    "Status must belong to the same company as the client."
                )
        return value

    def create(self, validated_data):
        """Create client and handle phone numbers"""
        phone_numbers_data = self.initial_data.get('phone_numbers', [])
        client = Client.objects.create(**validated_data)
        
        # Create phone numbers if provided
        if phone_numbers_data:
            for phone_data in phone_numbers_data:
                ClientPhoneNumber.objects.create(client=client, **phone_data)
        elif validated_data.get('phone_number'):
            # If old phone_number field is provided, create a primary phone number
            ClientPhoneNumber.objects.create(
                client=client,
                phone_number=validated_data['phone_number'],
                phone_type='mobile',
                is_primary=True
            )
        
        return client

    def update(self, instance, validated_data):
        """Update client and handle phone numbers"""
        phone_numbers_data = self.initial_data.get('phone_numbers', None)
        
        # Update client fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update phone numbers if provided
        if phone_numbers_data is not None:
            # Delete existing phone numbers
            instance.phone_numbers.all().delete()
            # Create new phone numbers
            for phone_data in phone_numbers_data:
                ClientPhoneNumber.objects.create(client=instance, **phone_data)
        elif validated_data.get('phone_number') and not instance.phone_numbers.exists():
            # If old phone_number field is provided and no phone numbers exist, create one
            ClientPhoneNumber.objects.create(
                client=instance,
                phone_number=validated_data['phone_number'],
                phone_type='mobile',
                is_primary=True
            )
        
        return instance


class ClientListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)
    communication_way_name = serializers.CharField(source="communication_way.name", read_only=True)
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
            "created_at",
        ]


@extend_schema_serializer(component_name="Deal")
class DealSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    employee_username = serializers.CharField(source="employee.username", read_only=True)
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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class DealListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    client_name = serializers.CharField(source="client.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    employee_username = serializers.CharField(source="employee.username", read_only=True)
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
            "created_at",
        ]


@extend_schema_serializer(component_name="Task")
class TaskSerializer(serializers.ModelSerializer):
    deal_client_name = serializers.CharField(source="deal.client.name", read_only=True)
    deal_stage = serializers.CharField(source="deal.stage", read_only=True)
    deal_employee_username = serializers.CharField(source="deal.employee.username", read_only=True, allow_null=True)
    stage_name = serializers.CharField(source="stage.name", read_only=True)

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

    def validate_stage(self, value):
        """Ensure stage belongs to the same company as the deal"""
        if value:
            # Get deal from instance or initial_data
            deal = None
            if self.instance and self.instance.deal_id:
                deal = self.instance.deal
            elif hasattr(self, 'initial_data'):
                deal_id = self.initial_data.get('deal')
                if deal_id:
                    from .models import Deal
                    try:
                        deal = Deal.objects.get(pk=deal_id)
                    except Deal.DoesNotExist:
                        pass
            
            if deal and value.company_id != deal.company_id:
                raise serializers.ValidationError(
                    "Stage must belong to the same company as the deal."
                )
        return value


class TaskListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    deal_client_name = serializers.CharField(source="deal.client.name", read_only=True)
    deal_employee_username = serializers.CharField(source="deal.employee.username", read_only=True, allow_null=True)
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
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
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
            elif hasattr(self, 'initial_data'):
                client_id = self.initial_data.get('client')
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
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
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

