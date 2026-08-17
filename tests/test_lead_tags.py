"""Lead tags: per-tenant settings CRUD, company scoping, OR filtering, timeline events."""
import pytest

from conftest import api_body
from crm.models import Client, ClientEvent
from settings.models import Tag


@pytest.fixture
def tag_vip(company, db):
    return Tag.objects.create(name="VIP", company=company, color="#ff0000")


@pytest.fixture
def tag_hot(company, db):
    return Tag.objects.create(name="Hot", company=company, color="#00ff00")


@pytest.fixture
def other_company_tag(other_company, db):
    return Tag.objects.create(name="Foreign", company=other_company)


def _make_lead(company, name="Lead", assigned_to=None):
    return Client.objects.create(
        name=name,
        company=company,
        priority="medium",
        type="fresh",
        assigned_to=assigned_to,
    )


# --- Settings CRUD ---------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_create_tag_scoped_to_own_company(authenticated_admin, company):
    response = authenticated_admin.post(
        "/api/v1/settings/tags/",
        {
            "name": "VIP",
            "description": "High value",
            "color": "#ff0000",
            "company": company.id,
        },
        format="json",
    )
    assert response.status_code == 201, getattr(response, "data", response.content)
    body = api_body(response)
    assert body["name"] == "VIP"
    # company is assigned server-side from the requesting user, not the payload
    assert Tag.objects.get(id=body["id"]).company_id == company.id


@pytest.mark.django_db
def test_tag_create_ignores_spoofed_company(authenticated_admin, company, other_company):
    """perform_create always overrides company with the requesting user's own."""
    response = authenticated_admin.post(
        "/api/v1/settings/tags/",
        {"name": "Spoof", "company": other_company.id},
        format="json",
    )
    assert response.status_code == 201, getattr(response, "data", response.content)
    assert Tag.objects.get(name="Spoof").company_id == company.id


@pytest.mark.django_db
def test_tag_list_excludes_other_companies(authenticated_admin, tag_vip, other_company_tag):
    response = authenticated_admin.get("/api/v1/settings/tags/")
    assert response.status_code == 200
    body = api_body(response)
    results = body["results"] if isinstance(body, dict) and "results" in body else body
    names = {row["name"] for row in results}
    assert names == {"VIP"}


@pytest.mark.django_db
def test_tag_name_unique_per_company(authenticated_admin, tag_vip):
    response = authenticated_admin.post(
        "/api/v1/settings/tags/",
        {"name": "VIP", "company": tag_vip.company_id},
        format="json",
    )
    assert response.status_code == 400, getattr(response, "data", response.content)


@pytest.mark.django_db
def test_employee_cannot_create_tag(authenticated_employee, company):
    response = authenticated_employee.post(
        "/api/v1/settings/tags/",
        {"name": "Nope", "company": company.id},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_employee_can_read_tags(authenticated_employee, tag_vip):
    response = authenticated_employee.get("/api/v1/settings/tags/")
    assert response.status_code == 200


# --- Assigning tags to leads ----------------------------------------------

@pytest.mark.django_db
def test_create_lead_with_tags(authenticated_admin, company, tag_vip, tag_hot):
    response = authenticated_admin.post(
        "/api/v1/clients/",
        {
            "name": "Tagged Lead",
            "company": company.id,
            "priority": "medium",
            "type": "fresh",
            "tags": [tag_vip.id, tag_hot.id],
        },
        format="json",
    )
    assert response.status_code == 201, getattr(response, "data", response.content)
    lead = Client.objects.get(id=api_body(response)["id"])
    assert set(lead.tags.values_list("name", flat=True)) == {"VIP", "Hot"}


@pytest.mark.django_db
def test_patch_lead_tags_replaces_set(authenticated_admin, company, tag_vip, tag_hot):
    lead = _make_lead(company)
    lead.tags.add(tag_vip)

    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/", {"tags": [tag_hot.id]}, format="json"
    )
    assert response.status_code == 200, getattr(response, "data", response.content)
    assert list(lead.tags.values_list("name", flat=True)) == ["Hot"]


@pytest.mark.django_db
def test_lead_response_exposes_tags_detail(authenticated_admin, company, tag_vip):
    lead = _make_lead(company)
    lead.tags.add(tag_vip)

    body = api_body(authenticated_admin.get(f"/api/v1/clients/{lead.id}/"))
    assert body["tags"] == [tag_vip.id]
    assert body["tags_detail"][0]["name"] == "VIP"
    assert body["tags_detail"][0]["color"] == "#ff0000"


@pytest.mark.django_db
def test_cross_company_tag_rejected(authenticated_admin, company, other_company_tag):
    lead = _make_lead(company)
    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/", {"tags": [other_company_tag.id]}, format="json"
    )
    assert response.status_code == 400, getattr(response, "data", response.content)
    assert lead.tags.count() == 0


# --- Filtering -------------------------------------------------------------

@pytest.mark.django_db
def test_filter_by_tag_ids_is_or_and_deduplicated(
    authenticated_admin, company, tag_vip, tag_hot
):
    both = _make_lead(company, "Both")
    both.tags.set([tag_vip, tag_hot])
    only_hot = _make_lead(company, "OnlyHot")
    only_hot.tags.set([tag_hot])
    _make_lead(company, "Untagged")

    body = api_body(
        authenticated_admin.get(f"/api/v1/clients/?tags={tag_vip.id},{tag_hot.id}")
    )
    results = body["results"] if isinstance(body, dict) and "results" in body else body
    ids = [row["id"] for row in results]
    # "Both" matches both tags but must appear exactly once
    assert sorted(ids) == sorted([both.id, only_hot.id])


@pytest.mark.django_db
def test_filter_by_tag_names(authenticated_admin, company, tag_vip, tag_hot):
    vip_lead = _make_lead(company, "VipLead")
    vip_lead.tags.set([tag_vip])
    hot_lead = _make_lead(company, "HotLead")
    hot_lead.tags.set([tag_hot])

    body = api_body(authenticated_admin.get("/api/v1/clients/?tags=VIP"))
    results = body["results"] if isinstance(body, dict) and "results" in body else body
    assert [row["id"] for row in results] == [vip_lead.id]


@pytest.mark.django_db
def test_no_tag_filter_returns_all(authenticated_admin, company, tag_vip):
    tagged = _make_lead(company, "Tagged")
    tagged.tags.set([tag_vip])
    untagged = _make_lead(company, "Untagged")

    body = api_body(authenticated_admin.get("/api/v1/clients/"))
    results = body["results"] if isinstance(body, dict) and "results" in body else body
    assert {row["id"] for row in results} == {tagged.id, untagged.id}


# --- Timeline --------------------------------------------------------------

@pytest.mark.django_db
def test_tag_change_logs_single_timeline_event(
    authenticated_admin, company, tag_vip, tag_hot
):
    lead = _make_lead(company)
    lead.tags.set([tag_vip])

    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/", {"tags": [tag_hot.id]}, format="json"
    )
    assert response.status_code == 200, getattr(response, "data", response.content)

    events = list(ClientEvent.objects.filter(client=lead, event_type="tags_change"))
    assert len(events) == 1
    assert events[0].old_value == "VIP"
    assert events[0].new_value == "Hot"
    assert events[0].notes == "tags_updated:+Hot|-VIP"


@pytest.mark.django_db
def test_unchanged_tags_logs_no_event(authenticated_admin, company, tag_vip):
    lead = _make_lead(company)
    lead.tags.set([tag_vip])

    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/", {"tags": [tag_vip.id]}, format="json"
    )
    assert response.status_code == 200, getattr(response, "data", response.content)
    assert ClientEvent.objects.filter(client=lead, event_type="tags_change").count() == 0


@pytest.mark.django_db
def test_patch_without_tags_key_leaves_tags_untouched(
    authenticated_admin, company, tag_vip
):
    lead = _make_lead(company)
    lead.tags.set([tag_vip])

    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/", {"notes": "hello"}, format="json"
    )
    assert response.status_code == 200, getattr(response, "data", response.content)
    assert list(lead.tags.values_list("name", flat=True)) == ["VIP"]
    assert ClientEvent.objects.filter(client=lead, event_type="tags_change").count() == 0
