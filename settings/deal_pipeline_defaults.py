"""Idempotent default deal pipeline, stages, and lost reasons for a company."""

from __future__ import annotations

DEFAULT_PIPELINE_NAME = "Sales"

DEFAULT_STAGES = (
    {
        "name": "In progress",
        "system_key": "in_progress",
        "stage_type": "open",
        "probability": 50,
        "color": "#3B82F6",
        "order": 0,
    },
    {
        "name": "On hold",
        "system_key": "on_hold",
        "stage_type": "open",
        "probability": 20,
        "color": "#F59E0B",
        "order": 1,
    },
    {
        "name": "Won",
        "system_key": "won",
        "stage_type": "won",
        "probability": 100,
        "color": "#10B981",
        "order": 2,
    },
    {
        "name": "Lost",
        "system_key": "lost",
        "stage_type": "lost",
        "probability": 0,
        "color": "#EF4444",
        "order": 3,
    },
    {
        "name": "Cancelled",
        "system_key": "cancelled",
        "stage_type": "lost",
        "probability": 0,
        "color": "#6B7280",
        "order": 4,
    },
)

DEFAULT_LOST_REASONS = (
    "Price",
    "Competitor",
    "No response",
    "Not a fit",
    "Other",
)

LEGACY_STAGE_KEYS = ("in_progress", "on_hold", "won", "lost", "cancelled")


def seed_company_deal_pipeline(company):
    """Create the default pipeline, system stages, and lost reasons if missing."""
    from settings.models import DealLostReason, DealPipeline, DealStage

    if not company or not getattr(company, "pk", None):
        return None

    pipeline = (
        DealPipeline.objects.filter(company=company, is_default=True).order_by("id").first()
    )
    if pipeline is None:
        pipeline = DealPipeline.objects.create(
            company=company,
            name=DEFAULT_PIPELINE_NAME,
            order=0,
            is_default=True,
            is_active=True,
        )

    for row in DEFAULT_STAGES:
        DealStage.objects.get_or_create(
            pipeline=pipeline,
            system_key=row["system_key"],
            defaults={
                "name": row["name"],
                "stage_type": row["stage_type"],
                "probability": row["probability"],
                "color": row["color"],
                "order": row["order"],
                "is_active": True,
            },
        )

    for index, name in enumerate(DEFAULT_LOST_REASONS):
        DealLostReason.objects.get_or_create(
            company=company,
            name=name,
            defaults={"order": index, "is_active": True},
        )
    return pipeline


def map_company_legacy_deals(company):
    """Point deals that have no pipeline stage at the seeded system stage."""
    from crm.deals.legacy import legacy_stage_for
    from crm.models import Deal

    pipeline = seed_company_deal_pipeline(company)
    if pipeline is None:
        return 0
    stages = {
        stage.system_key: stage
        for stage in pipeline.stages.filter(system_key__isnull=False)
    }
    fallback = stages.get("in_progress")
    updated = 0
    pending = Deal.objects.filter(company=company, pipeline_stage__isnull=True)
    for deal in pending.iterator():
        stage = stages.get((deal.stage or "").lower()) or fallback
        if stage is None:
            continue
        Deal.objects.filter(pk=deal.pk).update(
            pipeline_id=pipeline.id,
            pipeline_stage_id=stage.id,
            stage=legacy_stage_for(stage),
        )
        updated += 1
    return updated
