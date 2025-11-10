from rest_framework import serializers
from .models import Plan, Subscription, Payment


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "id",
            "name",
            "description",
            "price_monthly",
            "price_yearly",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PlanListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    class Meta:
        model = Plan
        fields = [
            "id",
            "name",
            "description",
            "price_monthly",
            "price_yearly",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "company",
            "company_name",
            "plan",
            "plan_name",
            "start_date",
            "end_date",
            "is_active",
            "auto_renew",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "start_date", "created_at", "updated_at"]


class SubscriptionListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "company",
            "company_name",
            "plan",
            "plan_name",
            "start_date",
            "end_date",
            "is_active",
            "auto_renew",
        ]


class PaymentSerializer(serializers.ModelSerializer):
    subscription_company_name = serializers.CharField(source="subscription.company.name", read_only=True)
    subscription_plan_name = serializers.CharField(source="subscription.plan.name", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "subscription",
            "subscription_company_name",
            "subscription_plan_name",
            "amount",
            "payment_method",
            "payment_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PaymentListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    subscription_company_name = serializers.CharField(source="subscription.company.name", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "subscription",
            "subscription_company_name",
            "amount",
            "payment_method",
            "payment_status",
            "created_at",
        ]

