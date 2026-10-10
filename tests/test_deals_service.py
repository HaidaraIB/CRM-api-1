"""DealService: transitions, tenant checks, pricing, quota."""

from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError

from crm.deals.exceptions import DealServiceError
from crm.deals.services import DealService
from crm.models import Client, Deal


def _lead(company, name="Lead"):
    return Client.objects.create(name=name, company=company, priority="low", type="cold")


@pytest.mark.django_db
def test_create_maps_legacy_stage_onto_pipeline(company, admin_user):
    lead = _lead(company)
    deal = DealService(admin_user).create(
        {"client": lead, "employee": admin_user, "stage": "won", "value": 100}
    )
    assert deal.pipeline_stage.system_key == "won"
    assert deal.pipeline_stage.stage_type == "won"
    assert deal.stage == "won"
    assert deal.won_at is not None
    assert deal.closed_by_id == admin_user.id


@pytest.mark.django_db
def test_lost_endpoint_requires_reason_legacy_write_does_not(company, admin_user):
    lead = _lead(company)
    service = DealService(admin_user)
    deal = service.create({"client": lead, "stage": "in_progress"})
    lost = deal.pipeline.stages.get(system_key="lost")
    with pytest.raises(DealServiceError):
        service.move_to_stage(deal, lost, require_lost_reason=True)
    updated = service.update(deal, {"stage": "lost"})
    assert updated.pipeline_stage.system_key == "lost"
    assert updated.lost_reason_id is None


@pytest.mark.django_db
def test_mark_lost_stores_reason(company, admin_user):
    from settings.models import DealLostReason

    lead = _lead(company)
    service = DealService(admin_user)
    deal = service.create({"client": lead, "stage": "in_progress"})
    reason = DealLostReason.objects.filter(company=company).first()
    updated = service.mark_lost(deal, lost_reason=reason, lost_note="Too expensive")
    assert updated.pipeline_stage.stage_type == "lost"
    assert updated.lost_reason_id == reason.id
    assert updated.lost_note == "Too expensive"
    assert updated.events.filter(event_type="lost").exists()


@pytest.mark.django_db
def test_rejects_client_from_another_company(company, other_company, admin_user):
    foreign = _lead(other_company, name="Foreign")
    with pytest.raises(DealServiceError):
        DealService(admin_user).create({"client": foreign, "stage": "in_progress"})


@pytest.mark.django_db
def test_line_items_derive_value(company, admin_user):
    lead = _lead(company)
    service = DealService(admin_user)
    deal = service.create({"client": lead, "value": 1, "discount_percentage": 10})
    service.add_line_item(
        deal,
        {"item_type": "custom", "name": "Fee", "unit_price": Decimal("100"), "quantity": 1},
    )
    service.add_line_item(
        deal,
        {"item_type": "custom", "name": "Extra", "unit_price": Decimal("50"), "quantity": 2},
    )
    deal.refresh_from_db()
    # 100 + 100, then 10% deal discount.
    assert deal.value == Decimal("180.00")


@pytest.mark.django_db
def test_quota_locks_create(company, admin_user, subscription, plan):
    plan.limits = {"max_deals": 0}
    plan.save(update_fields=["limits"])
    lead = _lead(company)
    with pytest.raises(ValidationError):
        DealService(admin_user).create({"client": lead, "stage": "in_progress"})
    assert Deal.objects.filter(company=company).count() == 0
