from rest_framework import serializers

from crm.availability import assignment_block_error, assignment_block_reason
from crm.deals.pricing import money
from crm.deals.selectors import weighted_value
from crm.models import Deal, DealEvent, DealLineItem


_CAMEL_FIELDS = {
    "startedBy": "started_by",
    "closedBy": "closed_by",
    "startDate": "start_date",
    "closedDate": "closed_date",
    "paymentMethod": "payment_method",
    "reminderDate": "reminder_date",
    "pipelineStage": "pipeline_stage",
    "expectedCloseDate": "expected_close_date",
    "lostReason": "lost_reason",
    "lostNote": "lost_note",
    "discountPercentage": "discount_percentage",
    "discountAmount": "discount_amount",
    "salesCommissionPercentage": "sales_commission_percentage",
    "salesCommissionAmount": "sales_commission_amount",
}


class _CamelInput:
    def to_internal_value(self, data):
        if hasattr(data, "copy"):
            data = data.copy()
        elif isinstance(data, dict):
            data = dict(data)
        else:
            data = dict(data) if data else {}
        for camel, snake in _CAMEL_FIELDS.items():
            if camel in data and snake not in data:
                data[snake] = data.pop(camel)
            elif camel in data:
                data.pop(camel, None)
        return super().to_internal_value(data)


class DealLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = DealLineItem
        fields = [
            "id",
            "item_type",
            "product",
            "service",
            "service_package",
            "unit",
            "name",
            "unit_price",
            "quantity",
            "discount_percentage",
            "line_total",
            "position",
        ]
        read_only_fields = fields


class DealLineItemWriteSerializer(_CamelInput, serializers.Serializer):
    item_type = serializers.ChoiceField(choices=[
        "product", "service", "service_package", "unit", "custom",
    ])
    product = serializers.PrimaryKeyRelatedField(
        queryset=DealLineItem._meta.get_field("product").related_model.objects.all(),
        required=False,
        allow_null=True,
    )
    service = serializers.PrimaryKeyRelatedField(
        queryset=DealLineItem._meta.get_field("service").related_model.objects.all(),
        required=False,
        allow_null=True,
    )
    service_package = serializers.PrimaryKeyRelatedField(
        queryset=DealLineItem._meta.get_field("service_package").related_model.objects.all(),
        required=False,
        allow_null=True,
    )
    unit = serializers.PrimaryKeyRelatedField(
        queryset=DealLineItem._meta.get_field("unit").related_model.objects.all(),
        required=False,
        allow_null=True,
    )
    name = serializers.CharField(required=False, allow_blank=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    discount_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            self.fields["item_type"].required = False


class DealEventSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DealEvent
        fields = [
            "id",
            "event_type",
            "old_value",
            "new_value",
            "reason",
            "metadata",
            "created_by",
            "created_by_username",
            "created_by_name",
            "created_at",
        ]

    def get_created_by_name(self, obj):
        user = obj.created_by
        if not user:
            return None
        full = (user.get_full_name() or "").strip()
        return full or user.username


class _DealReadMixin(serializers.Serializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    deal_client_name = serializers.CharField(source="client.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    employee_username = serializers.CharField(source="employee.username", read_only=True, allow_null=True)
    started_by_username = serializers.CharField(source="started_by.username", read_only=True, allow_null=True)
    closed_by_username = serializers.CharField(source="closed_by.username", read_only=True, allow_null=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True, allow_null=True)
    project_name = serializers.CharField(source="project.name", read_only=True, allow_null=True)
    lead_id = serializers.IntegerField(source="client_id", read_only=True)
    pipeline_name = serializers.CharField(source="pipeline.name", read_only=True, allow_null=True)
    pipeline_stage_name = serializers.CharField(source="pipeline_stage.name", read_only=True, allow_null=True)
    pipeline_stage_color = serializers.CharField(source="pipeline_stage.color", read_only=True, allow_null=True)
    stage_type = serializers.CharField(source="pipeline_stage.stage_type", read_only=True, allow_null=True)
    stage_probability = serializers.IntegerField(source="pipeline_stage.probability", read_only=True, allow_null=True)
    lost_reason_name = serializers.CharField(source="lost_reason.name", read_only=True, allow_null=True)
    weighted_value = serializers.SerializerMethodField()
    line_items_count = serializers.SerializerMethodField()

    def get_weighted_value(self, obj):
        return str(money(weighted_value(obj)))

    def get_line_items_count(self, obj):
        annotated = getattr(obj, "line_items_count", None)
        if annotated is not None:
            return annotated
        return obj.line_items.count()


_READ_FIELDS = [
    "id",
    "title",
    "client",
    "client_name",
    "deal_client_name",
    "lead_id",
    "company",
    "company_name",
    "employee",
    "employee_username",
    "stage",
    "pipeline",
    "pipeline_name",
    "pipeline_stage",
    "pipeline_stage_name",
    "pipeline_stage_color",
    "stage_type",
    "stage_probability",
    "probability",
    "payment_method",
    "status",
    "value",
    "weighted_value",
    "currency",
    "expected_close_date",
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
    "lost_reason",
    "lost_reason_name",
    "lost_note",
    "won_at",
    "lost_at",
    "stage_changed_at",
    "line_items_count",
    "started_by",
    "started_by_username",
    "closed_by",
    "closed_by_username",
    "created_at",
    "updated_at",
]


class DealListSerializer(_DealReadMixin, serializers.ModelSerializer):
    class Meta:
        model = Deal
        fields = _READ_FIELDS


class DealDetailSerializer(_DealReadMixin, serializers.ModelSerializer):
    line_items = DealLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = Deal
        fields = _READ_FIELDS + ["line_items"]


class DealSerializer(DealDetailSerializer):
    """Backward-compatible name for the deal detail payload."""


class DealWriteSerializer(_CamelInput, serializers.ModelSerializer):
    class Meta:
        model = Deal
        fields = [
            "client",
            "company",
            "employee",
            "title",
            "stage",
            "pipeline",
            "pipeline_stage",
            "probability",
            "payment_method",
            "status",
            "value",
            "currency",
            "expected_close_date",
            "reminder_date",
            "start_date",
            "closed_date",
            "discount_percentage",
            "discount_amount",
            "sales_commission_percentage",
            "sales_commission_amount",
            "description",
            "unit",
            "project",
            "lost_reason",
            "lost_note",
            "started_by",
            "closed_by",
        ]
        extra_kwargs = {
            "company": {"required": False},
            "stage": {"required": False},
            "title": {"required": False, "allow_blank": True},
            "lost_note": {"required": False, "allow_blank": True},
            "currency": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        if "employee" in attrs:
            employee = attrs.get("employee")
            if employee:
                same_as_before = (
                    self.instance is not None
                    and self.instance.employee_id is not None
                    and self.instance.employee_id == employee.pk
                )
                if not same_as_before:
                    reason = assignment_block_reason(employee)
                    if reason:
                        raise serializers.ValidationError(
                            assignment_block_error(reason, field="employee")
                        )
        # probability's 0-100 range is enforced by the `deal.upsert` catalog form
        # (validation/schemas/deals.py) via CatalogValidatedSerializerMixin.
        return attrs
