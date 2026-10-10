"""Map configurable pipeline stages onto the legacy Deal.stage enum mobile still writes."""

from django.db.models import Q

from settings.deal_pipeline_defaults import LEGACY_STAGE_KEYS

_TYPE_TO_LEGACY = {
    "won": "won",
    "lost": "lost",
    "open": "in_progress",
}


def legacy_stage_for(stage) -> str:
    if stage is None:
        return "in_progress"
    key = (getattr(stage, "system_key", None) or "").strip()
    if key in LEGACY_STAGE_KEYS:
        return key
    return _TYPE_TO_LEGACY.get(getattr(stage, "stage_type", None), "in_progress")


def default_pipeline(company):
    from settings.models import DealPipeline

    if company is None:
        return None
    pipeline = (
        DealPipeline.objects.filter(company=company, is_default=True, is_active=True)
        .order_by("id")
        .first()
    )
    if pipeline is not None:
        return pipeline
    return (
        DealPipeline.objects.filter(company=company, is_active=True).order_by("order", "id").first()
    )


def resolve_legacy_stage(company, legacy_value):
    """Resolve a legacy stage string to a DealStage on the company's default pipeline."""
    pipeline = default_pipeline(company)
    if pipeline is None:
        return None
    key = (legacy_value or "").strip().lower()
    stages = pipeline.stages.filter(is_active=True)
    match = stages.filter(system_key=key).first() if key else None
    if match is not None:
        return match
    if key == "won":
        return stages.filter(stage_type="won").order_by("order", "id").first()
    if key in ("lost", "cancelled"):
        return stages.filter(stage_type="lost").order_by("order", "id").first()
    return (
        stages.filter(system_key="in_progress").first()
        or stages.filter(stage_type="open").order_by("order", "id").first()
    )


def is_won_deal(deal) -> bool:
    stage = getattr(deal, "pipeline_stage", None)
    if stage is not None and stage.stage_type == "won":
        return True
    return (deal.stage or "").lower() == "won"


def won_deals_q():
    return Q(pipeline_stage__stage_type="won") | Q(stage__iexact="won")


def open_deals_q():
    return Q(pipeline_stage__stage_type="open") | Q(
        pipeline_stage__isnull=True, stage__in=["in_progress", "on_hold"]
    )
