"""Reports hub API: datetime periods in the company timezone, history-based metrics, scoping."""
from datetime import datetime, timedelta, timezone as dt_tz

import pytest

from conftest import api_body
from crm.models import Client
from reports.models import LeadAssignmentPeriod, LeadStatusTransition
from settings.company_defaults import seed_company_settings
from settings.models import LeadStatus

BASE = "/api/v1/reports/v2"


def utc(*a):
    return datetime(*a, tzinfo=dt_tz.utc)


@pytest.fixture
def rco(company):
    seed_company_settings(company)
    company.timezone = "Asia/Baghdad"  # UTC+3
    company.response_sla_minutes = 15
    company.save()
    return company


@pytest.fixture
def st(rco):
    return list(LeadStatus.objects.filter(company=rco, is_active=True).order_by("id"))


def lead(company, status, created, assignee=None, **kw):
    c = Client.objects.create(
        name="L",
        company=company,
        status=status,
        assigned_to=assignee,
        assigned_at=created if assignee else None,
        type="fresh",
        priority="low",
        **kw,
    )
    Client.objects.filter(pk=c.pk).update(created_at=created)
    LeadStatusTransition.objects.filter(client=c).update(changed_at=created)
    LeadAssignmentPeriod.objects.filter(client=c).update(assigned_at=created)
    c.refresh_from_db()
    return c


def move(c, to, at, by=None):
    prev = c.status
    LeadStatusTransition.objects.create(
        company=c.company,
        client=c,
        from_status=prev,
        to_status=to,
        from_status_name=prev.name,
        to_status_name=to.name,
        changed_at=at,
        changed_by=by,
        seconds_in_previous=600,
    )
    Client.objects.filter(pk=c.pk).update(status=to)
    c.refresh_from_db()


def respond(c, seconds, business=None, kind="call"):
    p = LeadAssignmentPeriod.objects.get(client=c, ended_at__isnull=True)
    LeadAssignmentPeriod.objects.filter(pk=p.pk).update(
        first_response_at=p.assigned_at + timedelta(seconds=seconds),
        first_response_kind=kind,
        response_seconds=seconds,
        business_response_seconds=business if business is not None else seconds,
    )


def get(client, section, **params):
    return client.get(f"{BASE}/{section}/", params)


@pytest.mark.django_db
class TestParams:
    def test_requires_start_and_end(self, authenticated_admin, rco):
        assert authenticated_admin.get(f"{BASE}/overview/").status_code == 400

    def test_end_before_start_and_too_long(self, authenticated_admin, rco):
        assert get(authenticated_admin, "overview", start="2026-10-05", end="2026-10-01").status_code == 400
        assert get(authenticated_admin, "overview", start="2020-01-01", end="2026-10-01").status_code == 400
        assert get(authenticated_admin, "overview", start="garbage", end="2026-10-01").status_code == 400
        assert get(authenticated_admin, "overview", start="2026-10-01", end="2026-10-02", lead_types="x").status_code == 400

    def test_all_sections_respond(self, authenticated_admin, rco):
        for s in ("overview", "leads", "status-journey", "response-time", "employees", "sources", "calls", "deals"):
            r = get(authenticated_admin, s, start="2026-10-01T00:00", end="2026-10-31T23:59")
            assert r.status_code == 200, (s, r.content)
            meta = api_body(r)["meta"]
            assert meta["timezone"] == "Asia/Baghdad" and "data_quality" in meta

    def test_permissions(self, authenticated_employee, rco):
        assert get(authenticated_employee, "overview", start="2026-10-01", end="2026-10-02").status_code == 403


@pytest.mark.django_db
class TestPeriods:
    def test_datetime_boundaries_use_company_timezone(self, authenticated_admin, rco, st, employee_user):
        # 21:30 UTC on Oct 5 is 00:30 on Oct 6 in Baghdad.
        lead(rco, st[0], utc(2026, 10, 5, 21, 30), employee_user)
        day6 = api_body(get(authenticated_admin, "overview", start="2026-10-06", end="2026-10-06"))
        day5 = api_body(get(authenticated_admin, "overview", start="2026-10-05", end="2026-10-05"))
        assert day6["current"]["new_leads"] == 1 and day5["current"]["new_leads"] == 0
        # Offset-less datetimes are also company-local, and the end is exclusive.
        tight = api_body(get(authenticated_admin, "overview", start="2026-10-06T00:30", end="2026-10-06T00:31"))
        miss = api_body(get(authenticated_admin, "overview", start="2026-10-06T00:00", end="2026-10-06T00:30"))
        assert tight["current"]["new_leads"] == 1 and miss["current"]["new_leads"] == 0

    def test_compare_previous_period(self, authenticated_admin, rco, st, employee_user):
        lead(rco, st[0], utc(2026, 10, 10, 9), employee_user)
        lead(rco, st[0], utc(2026, 10, 3, 9), employee_user)
        lead(rco, st[0], utc(2026, 10, 4, 9), employee_user)
        body = api_body(get(authenticated_admin, "overview", start="2026-10-08", end="2026-10-14", compare="previous"))
        assert body["current"]["new_leads"] == 1 and body["previous"]["new_leads"] == 2

    def test_series_is_zero_filled(self, authenticated_admin, rco, st, employee_user):
        lead(rco, st[0], utc(2026, 10, 10, 9), employee_user)
        body = api_body(get(authenticated_admin, "leads", start="2026-10-08", end="2026-10-14", granularity="day"))
        assert len(body["series"]) == 7 and sum(b["new_leads"] for b in body["series"]) == 1


@pytest.mark.django_db
class TestStatusHistory:
    def test_status_as_of_end_uses_history_not_current(self, authenticated_admin, rco, st, employee_user):
        c = lead(rco, st[0], utc(2026, 10, 1, 9), employee_user)
        move(c, st[1], utc(2026, 10, 20, 9))
        early = api_body(get(authenticated_admin, "leads", start="2026-10-01", end="2026-10-10"))
        late = api_body(get(authenticated_admin, "leads", start="2026-10-01", end="2026-10-31"))
        assert {s["status"]: s["count"] for s in early["status_as_of_end"]} == {st[0].name: 1}
        assert {s["status"]: s["count"] for s in late["status_as_of_end"]} == {st[1].name: 1}

    def test_journey_matrix_time_in_status_and_paths(self, authenticated_admin, rco, st, employee_user):
        for _ in range(2):
            c = lead(rco, st[0], utc(2026, 10, 2, 9), employee_user)
            move(c, st[1], utc(2026, 10, 3, 9))
            move(c, st[2], utc(2026, 10, 4, 9))
        body = api_body(get(authenticated_admin, "status-journey", start="2026-10-01", end="2026-10-31"))
        m = {(r["from"], r["to"]): r["count"] for r in body["matrix"]}
        assert m[(st[0].name, st[1].name)] == 2 and m[(st[1].name, st[2].name)] == 2
        assert body["leads_with_changes"] == 2 and body["transitions"] == 4
        assert body["common_paths"][0]["leads"] == 2
        assert body["common_paths"][0]["path"] == " → ".join([st[0].name, st[1].name, st[2].name])
        assert body["sankey"]["links"] and body["time_in_status"]

    def test_per_lead_journey_endpoint(self, authenticated_admin, rco, st, employee_user):
        c = lead(rco, st[0], utc(2026, 10, 2, 9), employee_user)
        move(c, st[1], utc(2026, 10, 3, 9))
        body = api_body(authenticated_admin.get(f"{BASE}/leads/{c.id}/journey/"))
        assert [s["status"] for s in body["status_path"]] == [st[0].name, st[1].name]
        assert body["status_path"][0]["seconds_in_status"] == 86400
        assert len(body["assignments"]) == 1

    def test_lead_journey_is_tenant_scoped(self, authenticated_admin, other_company):
        seed_company_settings(other_company)
        s = LeadStatus.objects.filter(company=other_company).first()
        theirs = Client.objects.create(name="T", company=other_company, status=s, type="fresh", priority="low")
        assert authenticated_admin.get(f"{BASE}/leads/{theirs.id}/journey/").status_code == 404


@pytest.mark.django_db
class TestResponseTime:
    def test_buckets_sla_and_employee_rows(self, authenticated_admin, rco, st, employee_user, admin_user):
        a = lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        b = lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        lead(rco, st[0], utc(2026, 10, 5, 9), admin_user)
        lead(rco, st[0], utc(2026, 10, 5, 9), admin_user)
        respond(a, 120)  # within the 15 minute SLA
        respond(b, 3 * 3600, business=600)  # slow wall-clock, but only 10 business minutes
        body = api_body(get(authenticated_admin, "response-time", start="2026-10-05", end="2026-10-06"))
        emp = next(e for e in body["employees"] if e["user_id"] == employee_user.id)
        assert emp["assignments"] == 2 and emp["responded"] == 2 and emp["within_sla_pct"] == 100.0
        assert emp["wall"]["median"] == round((120 + 3 * 3600) / 2)
        assert emp["business"]["median"] == 360
        adm = next(e for e in body["employees"] if e["user_id"] == admin_user.id)
        assert adm["pending"] == 2 and adm["responded"] == 0
        biz = {x["bucket"]: x["count"] for x in body["distribution"]["business"]}
        wall = {x["bucket"]: x["count"] for x in body["distribution"]["wall"]}
        assert biz["lt_5m"] == 1 and biz["5_15m"] == 1 and biz["no_response"] == 2
        assert wall["lt_5m"] == 1 and wall["1_4h"] == 1
        assert body["summary"]["pending"] == 2 and len(body["oldest_pending"]) == 2
        assert body["by_kind"][0] == {"kind": "call", "count": 2}
        assert sum(cell["count"] for day in body["heatmap"] for cell in day) == 4

    def test_overview_within_sla_counts_breached_unanswered(self, authenticated_admin, rco, st, employee_user):
        a = lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        respond(a, 60)
        lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)  # unanswered for days -> breached
        body = api_body(get(authenticated_admin, "overview", start="2026-10-05", end="2026-10-06"))
        assert body["current"]["within_sla_pct"] == 50.0
        assert body["current"]["leads_worked"] == 1 and body["current"]["assignments"] == 2

    def test_user_filter(self, authenticated_admin, rco, st, employee_user, admin_user):
        lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        lead(rco, st[0], utc(2026, 10, 5, 9), admin_user)
        body = api_body(
            get(authenticated_admin, "response-time", start="2026-10-05", end="2026-10-06", user_ids=str(employee_user.id))
        )
        assert [e["user_id"] for e in body["employees"]] == [employee_user.id]


@pytest.mark.django_db
class TestIsolationAndDrilldown:
    def test_other_tenants_data_never_leaks(self, authenticated_admin, rco, st, employee_user, other_company):
        seed_company_settings(other_company)
        s = LeadStatus.objects.filter(company=other_company).first()
        Client.objects.create(name="Theirs", company=other_company, status=s, type="fresh", priority="low")
        lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        far = dict(start="2020-01-01", end="2021-12-31")
        far = dict(start="2026-01-01", end="2026-12-31")
        body = api_body(get(authenticated_admin, "overview", **far))
        assert body["current"]["new_leads"] == 1
        names = [r["name"] for r in api_body(authenticated_admin.get(f"{BASE}/lead-list/", far))["results"]]
        assert "Theirs" not in names

    def test_lead_list_drilldown_by_bucket_and_status(self, authenticated_admin, rco, st, employee_user):
        fast = lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        slow = lead(rco, st[0], utc(2026, 10, 5, 9), employee_user)
        respond(fast, 60)
        respond(slow, 2 * 3600)
        move(slow, st[1], utc(2026, 10, 5, 12))
        base = dict(start="2026-10-05", end="2026-10-06")
        r = api_body(authenticated_admin.get(f"{BASE}/lead-list/", {**base, "metric": "response_bucket", "bucket": "1_4h"}))
        assert [x["id"] for x in r["results"]] == [slow.id]
        r = api_body(
            authenticated_admin.get(f"{BASE}/lead-list/", {**base, "metric": "status_entered", "status_id": st[1].id})
        )
        assert [x["id"] for x in r["results"]] == [slow.id]
        assert r["results"][0]["status_path"][-1] == st[1].name
        r = api_body(authenticated_admin.get(f"{BASE}/lead-list/", {**base, "page_size": 1}))
        assert r["count"] == 2 and len(r["results"]) == 1
