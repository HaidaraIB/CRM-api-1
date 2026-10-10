"""Deal pipeline settings and legacy stage mapping."""

import pytest
from rest_framework import status

from conftest import api_body
from crm.models import Client, Deal
from settings.deal_pipeline_defaults import map_company_legacy_deals
from settings.models import DealPipeline, DealStage


@pytest.mark.django_db
def test_company_gets_default_pipeline(company):
    pipeline = DealPipeline.objects.get(company=company, is_default=True)
    keys = set(pipeline.stages.values_list("system_key", flat=True))
    assert keys == {"in_progress", "on_hold", "won", "lost", "cancelled"}


@pytest.mark.django_db
def test_map_legacy_stage(company, admin_user):
    lead = Client.objects.create(name="L", company=company, priority="low", type="cold")
    deal = Deal.objects.create(client=lead, company=company, employee=admin_user, stage="cancelled")
    assert deal.pipeline_stage_id is None
    map_company_legacy_deals(company)
    deal.refresh_from_db()
    assert deal.pipeline_stage.system_key == "cancelled"
    assert deal.stage == "cancelled"


@pytest.mark.django_db
def test_reorder_stages_and_guard_delete(authenticated_admin, company, admin_user):
    pipeline = DealPipeline.objects.get(company=company, is_default=True)
    stages = list(pipeline.stages.order_by("order", "id"))
    flipped = list(reversed([stage.id for stage in stages]))
    response = authenticated_admin.post(
        "/api/v1/settings/deal-stages/reorder/",
        {"pipeline": pipeline.id, "ordered_ids": flipped},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    first = DealStage.objects.get(pk=flipped[0])
    assert first.order == 0

    listed = api_body(authenticated_admin.get("/api/v1/settings/deal-pipelines/"))
    assert listed["count"] >= 1
    assert listed["results"][0]["is_default"] is True

    lead = Client.objects.create(name="Busy", company=company, priority="low", type="cold")
    in_progress = pipeline.stages.get(system_key="in_progress")
    on_hold = pipeline.stages.get(system_key="on_hold")
    Deal.objects.create(
        client=lead,
        company=company,
        employee=admin_user,
        stage="in_progress",
        pipeline=pipeline,
        pipeline_stage=in_progress,
    )
    blocked = authenticated_admin.delete(f"/api/v1/settings/deal-stages/{in_progress.id}/")
    assert blocked.status_code == status.HTTP_400_BAD_REQUEST
    moved = authenticated_admin.delete(
        f"/api/v1/settings/deal-stages/{in_progress.id}/?move_to={on_hold.id}"
    )
    assert moved.status_code == status.HTTP_204_NO_CONTENT
    deal = Deal.objects.get(client=lead)
    assert deal.pipeline_stage_id == on_hold.id
    assert deal.stage == "on_hold"

    only = authenticated_admin.delete(f"/api/v1/settings/deal-pipelines/{pipeline.id}/")
    assert only.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_delete_default_pipeline_promotes_replacement(authenticated_admin, company):
    default_pipeline = DealPipeline.objects.get(company=company, is_default=True)
    other = DealPipeline.objects.create(company=company, name="Other", order=1)

    response = authenticated_admin.delete(f"/api/v1/settings/deal-pipelines/{default_pipeline.id}/")
    assert response.status_code == status.HTTP_204_NO_CONTENT

    assert not DealPipeline.objects.filter(pk=default_pipeline.pk).exists()
    other.refresh_from_db()
    assert other.is_default is True
