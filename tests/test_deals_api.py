"""Deal API: filters, summary, bulk, timeline, legacy writes, role scoping."""

import pytest
from rest_framework import status

from accounts.models import SupervisorPermission, User
from conftest import api_body
from crm.models import Client, Deal


def _lead(company, name="Lead"):
    return Client.objects.create(name=name, company=company, priority="low", type="cold")


@pytest.mark.django_db
def test_legacy_stage_write_and_filters(authenticated_admin, company, admin_user):
    lead = _lead(company, "Filter Lead")
    created = authenticated_admin.post(
        "/api/v1/deals/",
        {"client": lead.id, "employee": admin_user.id, "stage": "on_hold", "value": "250.00"},
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    body = api_body(created)
    assert body["stage"] == "on_hold"
    assert body["pipeline_stage_name"] == "On hold"
    assert body["lead_id"] == lead.id
    assert body["deal_client_name"] == "Filter Lead"

    other = _lead(company, "Other")
    Deal.objects.create(client=other, company=company, employee=admin_user, stage="won", value=10)

    listed = authenticated_admin.get("/api/v1/deals/?stage=on_hold&value_min=100")
    assert listed.status_code == status.HTTP_200_OK
    results = api_body(listed)["results"]
    assert len(results) == 1
    assert results[0]["id"] == body["id"]


@pytest.mark.django_db
def test_summary_bulk_and_timeline(authenticated_admin, company, admin_user, employee_user):
    lead = _lead(company)
    created = api_body(
        authenticated_admin.post(
            "/api/v1/deals/",
            {"client": lead.id, "employee": admin_user.id, "stage": "in_progress", "value": "80"},
            format="json",
        )
    )
    deal_id = created["id"]
    summary = api_body(authenticated_admin.get("/api/v1/deals/summary/"))
    assert summary["open_count"] >= 1
    assert any(row["stage_type"] == "open" and row["count"] >= 1 for row in summary["stages"])

    won = next(row for row in summary["stages"] if row["stage_type"] == "won")
    bulk = authenticated_admin.post(
        "/api/v1/deals/bulk/",
        {"ids": [deal_id], "action": "move", "pipeline_stage": won["id"]},
        format="json",
    )
    assert bulk.status_code == status.HTTP_200_OK
    assert api_body(bulk)["updated"] == 1
    deal = Deal.objects.get(pk=deal_id)
    assert deal.stage == "won"

    reopened = authenticated_admin.post(f"/api/v1/deals/{deal_id}/reopen/", {}, format="json")
    assert reopened.status_code == status.HTTP_200_OK
    assert api_body(reopened)["stage_type"] == "open"

    note = authenticated_admin.post(
        f"/api/v1/deals/{deal_id}/notes/",
        {"text": "Called the client"},
        format="json",
    )
    assert note.status_code == status.HTTP_201_CREATED
    timeline = api_body(authenticated_admin.get(f"/api/v1/deals/{deal_id}/timeline/"))
    assert any(event["event_type"] == "note" and event["new_value"] == "Called the client" for event in timeline)

    assigned = authenticated_admin.post(
        "/api/v1/deals/bulk/",
        {"ids": [deal_id], "action": "assign", "employee": employee_user.id},
        format="json",
    )
    assert assigned.status_code == status.HTTP_200_OK
    deal.refresh_from_db()
    assert deal.employee_id == employee_user.id


@pytest.mark.django_db
def test_lost_requires_reason_and_line_item_total(authenticated_admin, company, admin_user):
    from settings.models import DealLostReason

    lead = _lead(company)
    deal = api_body(
        authenticated_admin.post(
            "/api/v1/deals/",
            {"client": lead.id, "stage": "in_progress", "value": "10"},
            format="json",
        )
    )
    lost = authenticated_admin.post(f"/api/v1/deals/{deal['id']}/lost/", {}, format="json")
    assert lost.status_code == status.HTTP_400_BAD_REQUEST
    reason = DealLostReason.objects.filter(company=company).first()
    ok = authenticated_admin.post(
        f"/api/v1/deals/{deal['id']}/lost/",
        {"lost_reason": reason.id, "lost_note": "Budget"},
        format="json",
    )
    assert ok.status_code == status.HTTP_200_OK
    assert api_body(ok)["stage_type"] == "lost"

    reopened = api_body(authenticated_admin.post(f"/api/v1/deals/{deal['id']}/reopen/", {}, format="json"))
    item = authenticated_admin.post(
        f"/api/v1/deals/{deal['id']}/line-items/",
        {"item_type": "custom", "name": "Consulting", "unit_price": "40", "quantity": "2"},
        format="json",
    )
    assert item.status_code == status.HTTP_201_CREATED
    detail = api_body(authenticated_admin.get(f"/api/v1/deals/{reopened['id']}/"))
    assert detail["value"] == "80.00"
    assert len(detail["line_items"]) == 1


@pytest.mark.django_db
def test_employee_and_supervisor_scoping(api_client, company, admin_user, employee_user, subscription):
    lead = _lead(company)
    mine = Deal.objects.create(client=lead, company=company, employee=employee_user, stage="in_progress", value=5)
    Deal.objects.create(client=lead, company=company, employee=admin_user, stage="in_progress", value=9)

    api_client.force_authenticate(user=employee_user)
    employee_list = api_body(api_client.get("/api/v1/deals/"))
    assert employee_list["count"] == 1
    assert employee_list["results"][0]["id"] == mine.id

    supervisor = User.objects.create_user(
        username="deals_supervisor",
        email="deals_supervisor@test.com",
        password="testpass123",
        company=company,
        role="supervisor",
    )
    SupervisorPermission.objects.create(user=supervisor, is_active=True, can_manage_deals=False)
    api_client.force_authenticate(user=supervisor)
    hidden = api_body(api_client.get("/api/v1/deals/"))
    assert hidden["count"] == 0

    SupervisorPermission.objects.filter(user=supervisor).update(can_manage_deals=True)
    supervisor = User.objects.get(pk=supervisor.pk)
    api_client.force_authenticate(user=supervisor)
    visible = api_body(api_client.get("/api/v1/deals/"))
    assert visible["count"] == 2
