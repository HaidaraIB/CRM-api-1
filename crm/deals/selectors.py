"""Read-side queries for deals: visibility, filters, and pipeline summary."""

from datetime import datetime
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from crm.deals.legacy import default_pipeline, open_deals_q, won_deals_q
from crm.deals.pricing import money
from crm.models import Deal

_ORDERING = {
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
    "stage",
    "-stage",
    "value",
    "-value",
    "expected_close_date",
    "-expected_close_date",
}


def deals_visible_to(user):
    qs = Deal.objects.select_related(
        "client",
        "company",
        "employee",
        "started_by",
        "closed_by",
        "unit",
        "project",
        "pipeline",
        "pipeline_stage",
        "lost_reason",
    ).annotate(line_items_count=Count("line_items"))
    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    if user.is_admin():
        return qs.filter(company=user.company)
    if user.is_supervisor() and user.supervisor_has_permission("manage_deals"):
        return qs.filter(company=user.company)
    if user.is_assigned_clinical_staff():
        return qs.filter(company=user.company, employee=user)
    return qs.none()


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def apply_deal_filters(qs, params):
    """Apply list filters from a query-param mapping. `search` is handled by DRF SearchFilter."""
    if params is None:
        return qs
    get = params.get

    pipeline = get("pipeline")
    if pipeline:
        qs = qs.filter(pipeline_id=pipeline)
    stage_id = get("stage_id")
    if stage_id:
        qs = qs.filter(pipeline_stage_id=stage_id)
    stage = get("stage")
    if stage:
        qs = qs.filter(stage=stage)
    outcome = (get("outcome") or "").strip().lower()
    if outcome == "won":
        qs = qs.filter(won_deals_q())
    elif outcome == "open":
        qs = qs.filter(open_deals_q())
    elif outcome == "lost":
        qs = qs.filter(
            Q(pipeline_stage__stage_type="lost")
            | Q(pipeline_stage__isnull=True, stage__in=["lost", "cancelled"])
        )
    employee = get("employee")
    if employee:
        qs = qs.filter(employee_id=employee)
    client = get("client")
    if client:
        qs = qs.filter(client_id=client)
    status = get("status")
    if status:
        qs = qs.filter(status__iexact=status)
    payment = get("payment_method")
    if payment:
        qs = qs.filter(payment_method__iexact=payment)
    project = get("project")
    if project:
        qs = qs.filter(project_id=project)
    unit = get("unit")
    if unit:
        qs = qs.filter(unit_id=unit)
    value_min = _parse_decimal(get("value_min"))
    if value_min is not None:
        qs = qs.filter(value__gte=value_min)
    value_max = _parse_decimal(get("value_max"))
    if value_max is not None:
        qs = qs.filter(value__lte=value_max)
    expected_from = _parse_date(get("expected_close_from"))
    if expected_from:
        qs = qs.filter(expected_close_date__gte=expected_from)
    expected_to = _parse_date(get("expected_close_to"))
    if expected_to:
        qs = qs.filter(expected_close_date__lte=expected_to)
    created_from = _parse_date(get("created_from"))
    if created_from:
        qs = qs.filter(created_at__date__gte=created_from)
    created_to = _parse_date(get("created_to"))
    if created_to:
        qs = qs.filter(created_at__date__lte=created_to)

    ordering = get("ordering")
    if ordering in _ORDERING:
        qs = qs.order_by(ordering)
    return qs


def _effective_probability(deal) -> int:
    if deal.probability is not None:
        return int(deal.probability)
    stage = deal.pipeline_stage
    if stage is not None and stage.probability is not None:
        return int(stage.probability)
    if (deal.stage or "").lower() == "won":
        return 100
    return 0


def weighted_value(deal) -> Decimal:
    return (money(deal.value) * Decimal(_effective_probability(deal)) / Decimal("100")).quantize(
        Decimal("0.01")
    )


def _sum_values(qs) -> Decimal:
    total = qs.aggregate(total=Coalesce(Sum("value"), Decimal("0")))["total"]
    return money(total)


def pipeline_summary(qs, company, params=None):
    """Per-stage totals, win rate, and a monthly forecast from expected close dates."""
    from settings.models import DealStage

    params = params or {}
    pipeline_id = params.get("pipeline")
    pipeline = None
    if pipeline_id:
        from settings.models import DealPipeline

        pipeline = DealPipeline.objects.filter(company=company, pk=pipeline_id).first()
    if pipeline is None:
        pipeline = default_pipeline(company)

    stages = []
    if pipeline is not None:
        stages = list(
            DealStage.objects.filter(pipeline=pipeline, is_active=True).order_by("order", "id")
        )

    stage_rows = []
    for stage in stages:
        stage_qs = qs.filter(pipeline_stage=stage)
        count = stage_qs.count()
        value = _sum_values(stage_qs)
        weighted = sum(
            (weighted_value(deal) for deal in stage_qs.select_related("pipeline_stage")),
            Decimal("0"),
        )
        stage_rows.append(
            {
                "id": stage.id,
                "name": stage.name,
                "color": stage.color,
                "stage_type": stage.stage_type,
                "probability": stage.probability,
                "order": stage.order,
                "count": count,
                "value": str(money(value)),
                "weighted_value": str(money(weighted)),
            }
        )

    open_qs = qs.filter(open_deals_q())
    won_qs = qs.filter(won_deals_q())
    decided = qs.filter(won_deals_q() | Q(pipeline_stage__stage_type="lost") | Q(
        pipeline_stage__isnull=True, stage__in=["lost", "cancelled"]
    ))
    won_count = won_qs.count()
    decided_count = decided.count()
    win_rate = round((won_count / decided_count) * 100) if decided_count else 0

    today = timezone.localdate()
    month_start = today.replace(day=1)
    created_from = _parse_date(params.get("created_from")) if hasattr(params, "get") else None
    period_start = created_from or month_start
    won_period = won_qs.filter(Q(won_at__date__gte=period_start) | Q(won_at__isnull=True, closed_date__gte=period_start) | Q(
        won_at__isnull=True, closed_date__isnull=True, updated_at__date__gte=period_start
    ))

    forecast = []
    open_with_date = open_qs.exclude(expected_close_date__isnull=True)
    buckets = {}
    for deal in open_with_date.select_related("pipeline_stage"):
        if not deal.expected_close_date:
            continue
        key = deal.expected_close_date.strftime("%Y-%m")
        slot = buckets.setdefault(key, {"value": Decimal("0"), "weighted": Decimal("0")})
        slot["value"] += money(deal.value)
        slot["weighted"] += weighted_value(deal)
    for key in sorted(buckets)[:6]:
        forecast.append(
            {
                "month": key,
                "value": str(money(buckets[key]["value"])),
                "weighted": str(money(buckets[key]["weighted"])),
            }
        )

    open_weighted = sum(
        (weighted_value(deal) for deal in open_qs.select_related("pipeline_stage")),
        Decimal("0"),
    )
    return {
        "pipeline_id": pipeline.id if pipeline else None,
        "pipeline_name": pipeline.name if pipeline else "",
        "stages": stage_rows,
        "open_count": open_qs.count(),
        "open_value": str(_sum_values(open_qs)),
        "weighted_forecast": str(money(open_weighted)),
        "won_count": won_count,
        "won_value": str(_sum_values(won_qs)),
        "won_this_period": {
            "count": won_period.count(),
            "value": str(_sum_values(won_period)),
        },
        "win_rate": win_rate,
        "forecast_by_month": forecast,
    }
