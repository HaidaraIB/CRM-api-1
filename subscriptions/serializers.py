from rest_framework import serializers
from .models import Plan, Subscription, Payment, Invoice, Broadcast, PaymentGateway


class CreatePaytabsPaymentSerializer(serializers.Serializer):
    subscription_id = serializers.IntegerField()
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        allow_null=True
    )


class CreateZaincashPaymentSerializer(serializers.Serializer):
    subscription_id = serializers.IntegerField()
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        allow_null=True
    )


class CreateStripePaymentSerializer(serializers.Serializer):
    subscription_id = serializers.IntegerField()
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        allow_null=True
    )


class CreateQicardPaymentSerializer(serializers.Serializer):
    subscription_id = serializers.IntegerField()
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        allow_null=True
    )


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "id",
            "name",
            "name_ar",
            "description",
            "description_ar",
            "price_monthly",
            "price_yearly",
            "trial_days",
            "users",
            "clients",
            "storage",
            "visible",
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
            "name_ar",
            "description",
            "description_ar",
            "price_monthly",
            "price_yearly",
            "trial_days",
            "users",
            "clients",
            "storage",
            "visible",
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
    subscription_company_name = serializers.CharField(
        source="subscription.company.name", read_only=True
    )
    subscription_plan_name = serializers.CharField(
        source="subscription.plan.name", read_only=True
    )

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

    subscription_company_name = serializers.CharField(
        source="subscription.company.name", read_only=True
    )

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


class InvoiceSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(
        source="subscription.company.name", read_only=True
    )
    plan_name = serializers.CharField(source="subscription.plan.name", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "subscription",
            "company_name",
            "plan_name",
            "invoice_number",
            "amount",
            "due_date",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class InvoiceListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    company_name = serializers.CharField(
        source="subscription.company.name", read_only=True
    )

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "company_name",
            "amount",
            "due_date",
            "status",
            "created_at",
        ]


class BroadcastSerializer(serializers.ModelSerializer):
    class Meta:
        model = Broadcast
        fields = [
            "id",
            "subject",
            "content",
            "target",
            "broadcast_type",
            "status",
            "scheduled_at",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "sent_at", "created_at", "updated_at"]


class BroadcastListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    class Meta:
        model = Broadcast
        fields = [
            "id",
            "subject",
            "target",
            "status",
            "created_at",
        ]


class PaymentGatewaySerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentGateway
        fields = [
            "id",
            "name",
            "description",
            "status",
            "enabled",
            "config",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
    
    def update(self, instance, validated_data):
        """
        Custom update to merge config instead of replacing it completely
        This ensures we don't lose existing configuration values
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Handle config merging
        if 'config' in validated_data:
            new_config = validated_data.pop('config')
            existing_config = instance.config or {}
            
            # Log for debugging
            logger.info(f"Updating PaymentGateway {instance.id} ({instance.name})")
            logger.info(f"Existing config: {existing_config}")
            logger.info(f"New config received: {new_config}")
            
            # If new_config is empty dict, keep existing config (don't overwrite)
            if new_config and isinstance(new_config, dict) and len(new_config) > 0:
                # Merge new config with existing config
                # Start with existing config, then update with new values
                merged_config = {**existing_config, **new_config}
                # Remove None values but keep empty strings and other falsy values that might be valid
                cleaned_config = {k: v for k, v in merged_config.items() if v is not None}
                validated_data['config'] = cleaned_config
                logger.info(f"Merged config: {cleaned_config}")
            else:
                # If new_config is empty, keep existing config
                logger.info(f"New config is empty, keeping existing config: {existing_config}")
                # Don't update config field - keep existing
                # validated_data['config'] is not set, so existing config will be preserved
        
        # Update other fields normally
        return super().update(instance, validated_data)


class PaymentGatewayListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    class Meta:
        model = PaymentGateway
        fields = [
            "id",
            "name",
            "description",
            "status",
            "enabled",
        ]
