from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import Client, Deal, Task


class ClientSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)

    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "priority",
            "type",
            "communication_way",
            "budget",
            "phone_number",
            "company",
            "company_name",
            "assigned_to",
            "assigned_to_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ClientListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)
    assigned_to_username = serializers.CharField(source="assigned_to.username", read_only=True)

    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "priority",
            "type",
            "communication_way",
            "budget",
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

    class Meta:
        model = Task
        fields = [
            "id",
            "deal",
            "deal_client_name",
            "deal_stage",
            "stage",
            "notes",
            "reminder_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TaskListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    deal_client_name = serializers.CharField(source="deal.client.name", read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "deal",
            "deal_client_name",
            "stage",
            "reminder_date",
            "created_at",
        ]

