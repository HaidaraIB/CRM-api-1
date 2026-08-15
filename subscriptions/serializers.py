from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from subscriptions.plan_constraints import validate_single_free_trial_and_free_forever_plans

from .gateway_config import (
    mask_gateway_config,
    merge_config_for_write,
    strip_masked_values,
)
from .models import Plan, Subscription, Payment, Invoice, Broadcast, PaymentGateway
from .services.gateway_activation import apply_exclusive_activation


class CreateCheckoutSessionSerializer(serializers.Serializer):
    """Request body for every create-*-session endpoint."""

    subscription_id = serializers.IntegerField()
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        allow_null=True
    )


# The five per-gateway serializers were byte-for-byte identical; these aliases
# keep any external imports working.
CreatePaytabsPaymentSerializer = CreateCheckoutSessionSerializer
CreateZaincashPaymentSerializer = CreateCheckoutSessionSerializer
CreateStripePaymentSerializer = CreateCheckoutSessionSerializer
CreateQicardPaymentSerializer = CreateCheckoutSessionSerializer
CreateFibPaymentSerializer = CreateCheckoutSessionSerializer


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
            "features",
            "limits",
            "usage_limits_monthly",
            "visible",
            "tier",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name

    def validate_name_ar(self, value):
        return (value or "").strip()

    def validate_description(self, value):
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("This field may not be blank.")
        return text

    def validate_description_ar(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        inst = self.instance
        if inst:
            pm = attrs.get("price_monthly", inst.price_monthly)
            py = attrs.get("price_yearly", inst.price_yearly)
            td = attrs.get("trial_days", inst.trial_days)
        else:
            pm = attrs.get("price_monthly", 0)
            py = attrs.get("price_yearly", 0)
            td = attrs.get("trial_days", 0)
        try:
            validate_single_free_trial_and_free_forever_plans(
                price_monthly=pm,
                price_yearly=py,
                trial_days=td,
                exclude_plan_id=inst.pk if inst else None,
            )
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages[0] if e.messages else str(e))
        return attrs


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
            "features",
            "limits",
            "usage_limits_monthly",
            "visible",
            "tier",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    pending_plan_name = serializers.CharField(source="pending_plan.name", read_only=True)

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
            "current_period_start",
            "billing_cycle",
            "subscription_status",
            "pending_plan",
            "pending_plan_name",
            "pending_billing_cycle",
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
    pending_plan_name = serializers.CharField(source="pending_plan.name", read_only=True)

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
            "current_period_start",
            "billing_cycle",
            "subscription_status",
            "pending_plan",
            "pending_plan_name",
            "pending_billing_cycle",
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
    target_plan_name = serializers.CharField(source="target_plan.name", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "subscription",
            "subscription_company_name",
            "subscription_plan_name",
            "target_plan",
            "target_plan_name",
            "billing_cycle",
            "amount",
            "currency",
            "exchange_rate",
            "amount_usd",
            "payment_method",
            "payment_status",
            "tran_ref",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PaymentListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views. Use amount_usd for display in USD."""

    subscription_company_name = serializers.CharField(
        source="subscription.company.name", read_only=True
    )
    target_plan_name = serializers.CharField(source="target_plan.name", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "subscription",
            "subscription_company_name",
            "target_plan",
            "target_plan_name",
            "billing_cycle",
            "amount",
            "currency",
            "exchange_rate",
            "amount_usd",
            "payment_method",
            "payment_status",
            "tran_ref",
            "created_at",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    """Invoice is read-only; status comes from the linked payment."""

    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "payment",
            "payment_status",
            "subscription",
            "invoice_number",
            "amount",
            "currency",
            "company_name",
            "plan_name",
            "line_description",
            "billing_cycle",
            "due_date",
            "last_emailed_at",
            "legacy_payment_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "payment",
            "subscription",
            "invoice_number",
            "amount",
            "currency",
            "company_name",
            "plan_name",
            "line_description",
            "billing_cycle",
            "due_date",
            "last_emailed_at",
            "legacy_payment_status",
            "created_at",
            "updated_at",
        ]

    def get_payment_status(self, obj):
        return obj.effective_payment_status()


class InvoiceListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "payment",
            "invoice_number",
            "company_name",
            "amount",
            "currency",
            "payment_status",
            "due_date",
            "last_emailed_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "payment",
            "invoice_number",
            "company_name",
            "amount",
            "currency",
            "due_date",
            "last_emailed_at",
            "created_at",
        ]

    def get_payment_status(self, obj):
        return obj.effective_payment_status()


def _validate_broadcast_target(value):
    """Validate broadcast target format and that referenced plan/company exists."""
    from subscriptions.models import Plan
    from companies.models import Company
    if value == "all":
        return
    if value in ("role_admin", "role_supervisor", "role_employee"):
        return
    if value.startswith("plan_"):
        try:
            plan_id = int(value.replace("plan_", ""))
            if not Plan.objects.filter(id=plan_id).exists():
                raise serializers.ValidationError(f"Plan with id {plan_id} does not exist.")
        except (ValueError, TypeError):
            raise serializers.ValidationError("Invalid plan target format. Use plan_<id>.")
        return
    if value.startswith("company_"):
        try:
            company_id = int(value.replace("company_", ""))
            if not Company.objects.filter(id=company_id).exists():
                raise serializers.ValidationError(f"Company with id {company_id} does not exist.")
        except (ValueError, TypeError):
            raise serializers.ValidationError("Invalid company target format. Use company_<id>.")
        return
    raise serializers.ValidationError(
        "Target must be: all, plan_<id>, company_<id>, role_admin, role_supervisor, or role_employee."
    )


class BroadcastSerializer(serializers.ModelSerializer):
    targets = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = Broadcast
        fields = [
            "id",
            "subject",
            "content",
            "targets",
            "broadcast_type",
            "status",
            "scheduled_at",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "sent_at", "created_at", "updated_at"]

    def validate_targets(self, value):
        if not value:
            return []
        for t in value:
            if not t:
                continue
            _validate_broadcast_target(t)
        return value

    def create(self, validated_data):
        targets = validated_data.get("targets") or []
        if not targets:
            validated_data["targets"] = ["all"]
        return super().create(validated_data)

    def update(self, instance, validated_data):
        targets = validated_data.get("targets")
        if targets is not None and len(targets) == 0:
            validated_data["targets"] = ["all"]
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        targets = getattr(instance, "targets", None)
        data["targets"] = list(targets) if targets and len(targets) > 0 else ["all"]
        return data


class BroadcastListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    targets = serializers.SerializerMethodField()

    class Meta:
        model = Broadcast
        fields = [
            "id",
            "subject",
            "targets",
            "broadcast_type",
            "status",
            "scheduled_at",
            "sent_at",
            "created_at",
        ]

    def get_targets(self, obj):
        targets = getattr(obj, "targets", None)
        return list(targets) if targets and len(targets) > 0 else ["all"]


class PaymentGatewaySerializer(serializers.ModelSerializer):
    #: Gateways switched off because this one was enabled (see gateway_activation).
    disabled_gateways = serializers.SerializerMethodField()

    class Meta:
        model = PaymentGateway
        fields = [
            "id",
            "name",
            "description",
            "status",
            "enabled",
            "config",
            "disabled_gateways",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_disabled_gateways(self, obj):
        return getattr(obj, "_disabled_gateways", [])

    def to_representation(self, instance):
        """Never return live credentials; secrets go out masked (see gateway_config)."""
        data = super().to_representation(instance)
        data["config"] = mask_gateway_config(instance.config)
        return data

    def update(self, instance, validated_data):
        """
        Merge config rather than replacing it, so a partial form submit does not
        wipe unrelated keys, and drop values the client echoed back still masked
        so an unedited secret field leaves the stored credential intact.
        """
        import logging
        logger = logging.getLogger(__name__)

        if 'config' in validated_data:
            new_config = validated_data.pop('config')
            real_edits = strip_masked_values(new_config)

            if real_edits:
                merged_config = merge_config_for_write(instance.config, real_edits)
                validated_data['config'] = merged_config
                logger.info(
                    "Updating PaymentGateway %s (%s) config keys=%s",
                    instance.id,
                    instance.name,
                    sorted(real_edits.keys()),
                )
            else:
                # Nothing but masked/empty values came back - keep what is stored.
                logger.info(
                    "PaymentGateway %s config unchanged (no unmasked values submitted)",
                    instance.id,
                )

        return self._enforce_exclusivity(super().update(instance, validated_data))

    def create(self, validated_data):
        return self._enforce_exclusivity(super().create(validated_data))

    @staticmethod
    def _enforce_exclusivity(instance):
        """
        A gateway saved as enabled switches its rivals off.

        Lives here rather than only in the toggle_enabled action because a plain
        PATCH {"enabled": true} reaches the model directly - that gap is how two
        card gateways could end up live despite the admin panel's own checks.
        """
        instance._disabled_gateways = apply_exclusive_activation(instance)
        return instance


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
