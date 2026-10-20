"""
Phase 4 routing API: business hours, IVR menus, queues, and inbound routes that
use them (spec section 6 /ivr, /queues, /queues/{id}/members). Destinations are
typed and company-scoped; a target still in use cannot be deleted (409 in_use);
every change recompiles the tenant.
"""

import pytest

from conftest import api_body
from telephony.models import (
    PbxBusinessHours,
    PbxExtension,
    PbxInboundRoute,
    PbxIvrMenu,
    PbxQueue,
    PbxTenant,
)
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"
OFFICE = {"name": "Office", "timezone": "Asia/Baghdad", "rules": [{"days": [6, 0, 1, 2, 3], "start": "09:00", "end": "17:00"}]}


def _code(resp):
    return resp.json()["error"]["code"]


def _fields(resp):
    """Field names in the rendered envelope; nested errors (options.digit) count for their parent too."""
    error = resp.json()["error"]
    keys = set(error.get("fields") or {}) | set(error.get("details") or {})
    return keys | {k.split(".", 1)[0] for k in keys}


@pytest.fixture
def setup(company, tenant_a, make_trunk, make_number, make_extension):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1)
    trunk = make_trunk(company)
    return {
        "tenant": tenant_a,
        "ext100": make_extension(company, "100", display_name="Agent 100"),
        "ext101": make_extension(company, "101", display_name="Desk 101"),
        "did": make_number(company, trunk, "+9647700000001"),
    }


def _version(tenant):
    tenant.refresh_from_db()
    return tenant.config_version


# ---- business hours ------------------------------------------------------------------


def test_business_hours_crud_and_recompile(admin_client, setup):
    before = _version(setup["tenant"])
    resp = admin_client.post(f"{BASE}/business-hours/", {**OFFICE, "closed_dates": ["2026-12-25", "2026-12-25"]}, format="json")
    assert resp.status_code == 201, resp.content
    body = api_body(resp)
    assert body["rules"] == [{"days": [0, 1, 2, 3, 6], "start": "09:00", "end": "17:00"}]
    assert body["closed_dates"] == ["2026-12-25"]
    assert _version(setup["tenant"]) == before + 1


@pytest.mark.parametrize("change,field", [
    ({"timezone": "Mars/Olympus"}, "timezone"),
    ({"timezone": "Asia/Baghdad;Hangup()"}, "timezone"),
    ({"rules": []}, "rules"),
    ({"rules": [{"days": [7], "start": "09:00", "end": "17:00"}]}, "rules"),
    ({"rules": [{"days": [1, 1], "start": "09:00", "end": "17:00"}]}, "rules"),
    ({"rules": [{"days": [1], "start": "17:00", "end": "09:00"}]}, "rules"),
    ({"rules": [{"days": [1], "start": "9am", "end": "17:00"}]}, "rules"),
    ({"closed_dates": ["25/12/2026"]}, "closed_dates"),
    ({"closed_dates": ["2026-02-30"]}, "closed_dates"),
    ({"name": ""}, "name"),
])
def test_business_hours_validation(admin_client, setup, change, field):
    resp = admin_client.post(f"{BASE}/business-hours/", {**OFFICE, **change}, format="json")
    assert resp.status_code == 400 and field in _fields(resp), resp.content


def test_business_hours_names_are_unique_per_company(admin_client, setup):
    assert admin_client.post(f"{BASE}/business-hours/", OFFICE, format="json").status_code == 201
    assert admin_client.post(f"{BASE}/business-hours/", OFFICE, format="json").status_code == 400


# ---- queues ----------------------------------------------------------------------------


def _queue(client, setup, **kw):
    body = {"name": "Sales", "strategy": "ringall", "member_timeout_s": 15, "max_wait_s": 60,
            "overflow_type": "extension", "overflow_extension": str(setup["ext101"].pk),
            "members": [{"extension": str(setup["ext100"].pk)}, {"extension": str(setup["ext101"].pk), "penalty": 2}]}
    body.update(kw)
    return client.post(f"{BASE}/queues/", body, format="json")


def test_queue_with_members(admin_client, setup):
    resp = _queue(admin_client, setup)
    assert resp.status_code == 201, resp.content
    body = api_body(resp)
    assert [(m["number"], m["penalty"]) for m in body["members"]] == [("100", 0), ("101", 2)]
    queue = PbxQueue.objects.get()
    assert queue.overflow_extension == setup["ext101"] and queue.overflow_ivr is None

    # PUT /queues/{id}/members/ replaces the membership (spec section 6).
    resp = admin_client.put(f"{BASE}/queues/{queue.pk}/members/", {"members": [{"extension": str(setup["ext101"].pk)}]},
                            format="json")
    assert resp.status_code == 200, resp.content
    assert [m["number"] for m in api_body(resp)["members"]] == ["101"]


@pytest.mark.parametrize("change,field", [
    ({"overflow_type": "queue"}, "overflow_type"),
    ({"overflow_type": "ivr", "overflow_extension": None}, "overflow_ivr"),
    ({"strategy": "loudest"}, "strategy"),
    ({"member_timeout_s": 2}, "member_timeout_s"),
    ({"max_wait_s": 7200}, "max_wait_s"),
    ({"members": [{"extension": "00000000-0000-4000-8000-000000000000"}]}, "members"),
])
def test_queue_validation(admin_client, setup, change, field):
    resp = _queue(admin_client, setup, **change)
    assert resp.status_code == 400 and field in _fields(resp), resp.content


def test_queue_members_must_be_unique(admin_client, setup):
    same = str(setup["ext100"].pk)
    resp = _queue(admin_client, setup, members=[{"extension": same}, {"extension": same}])
    assert resp.status_code == 400 and "members" in _fields(resp)


def test_queue_cannot_use_another_tenants_extension(admin_client, setup, other_company, make_extension):
    foreign = make_extension(other_company, "100")
    resp = _queue(admin_client, setup, members=[{"extension": str(foreign.pk)}])
    assert resp.status_code == 400
    resp = _queue(admin_client, setup, overflow_extension=str(foreign.pk))
    assert resp.status_code == 400
    queue = PbxQueue.objects.create(company=setup["tenant"].company, name="Q")
    resp = admin_client.put(f"{BASE}/queues/{queue.pk}/members/", {"members": [{"extension": str(foreign.pk)}]}, format="json")
    assert resp.status_code == 400 and not queue.members.exists()


# ---- IVR menus -------------------------------------------------------------------------


def _ivr(client, setup, queue=None, **kw):
    body = {
        "name": "Main", "prompt": "loop/ivr-welcome-ar", "timeout_s": 5, "max_retries": 2,
        "timeout_type": "extension", "timeout_extension": str(setup["ext101"].pk),
        "options": [
            {"digit": "1", "destination_type": "extension", "extension": str(setup["ext100"].pk)},
            {"digit": "0", "destination_type": "hangup"},
        ],
    }
    if queue is not None:
        body["options"].append({"digit": "2", "destination_type": "queue", "queue": str(queue.pk)})
    body.update(kw)
    return client.post(f"{BASE}/ivr-menus/", body, format="json")


def test_ivr_menu_with_options(admin_client, setup):
    _queue(admin_client, setup)
    resp = _ivr(admin_client, setup, queue=PbxQueue.objects.get())
    assert resp.status_code == 201, resp.content
    options = {o["digit"]: o for o in api_body(resp)["options"]}
    assert set(options) == {"0", "1", "2"}
    assert options["0"]["extension"] is None and options["2"]["queue"] == str(PbxQueue.objects.get().pk)

    # Options are replaced as a whole on update.
    menu = PbxIvrMenu.objects.get()
    resp = admin_client.patch(f"{BASE}/ivr-menus/{menu.pk}/",
                              {"options": [{"digit": "#", "destination_type": "hangup"}]}, format="json")
    assert resp.status_code == 200, resp.content
    assert [o.digit for o in menu.options.all()] == ["#"]


@pytest.mark.parametrize("change,field", [
    ({"prompt": "/etc/passwd"}, "prompt"),
    ({"prompt": "loop/ivr-default,1)"}, "prompt"),
    ({"timeout_s": 0}, "timeout_s"),
    ({"max_retries": 9}, "max_retries"),
    ({"timeout_type": "queue"}, "timeout_queue"),
    ({"options": [{"digit": "12", "destination_type": "hangup"}]}, "options"),
    ({"options": [{"digit": "1", "destination_type": "hangup"}, {"digit": "1", "destination_type": "hangup"}]}, "options"),
    ({"options": [{"digit": "1", "destination_type": "extension"}]}, "options"),
])
def test_ivr_validation(admin_client, setup, change, field):
    resp = _ivr(admin_client, setup, **change)
    assert resp.status_code == 400 and field in _fields(resp), resp.content


def test_ivr_options_cannot_point_at_another_tenant(admin_client, setup, other_company):
    foreign = PbxQueue.objects.create(company=other_company, name="B queue")
    resp = _ivr(admin_client, setup, options=[{"digit": "1", "destination_type": "queue", "queue": str(foreign.pk)}])
    assert resp.status_code == 400 and not PbxIvrMenu.objects.filter(company=setup["tenant"].company).exists()


# ---- inbound routes with destinations and hours ------------------------------------------


def test_inbound_route_to_ivr_with_business_hours(admin_client, setup):
    hours = api_body(admin_client.post(f"{BASE}/business-hours/", OFFICE, format="json"))
    menu = api_body(_ivr(admin_client, setup))
    resp = admin_client.post(f"{BASE}/inbound-routes/", {
        "number": str(setup["did"].pk), "destination_type": "ivr", "ivr": menu["id"],
        "business_hours": hours["id"], "closed_type": "extension", "closed_extension": str(setup["ext101"].pk),
    }, format="json")
    assert resp.status_code == 201, resp.content
    route = PbxInboundRoute.objects.get()
    assert (route.destination_type, route.extension, route.closed_extension) == ("ivr", None, setup["ext101"])

    from telephony.services.compiler import compile_tenant

    text = compile_tenant(setup["tenant"]).files["extensions_tdev4test.conf"]
    assert f"Goto(tdev4test-ivr-{menu['id'][:8]},s,1)" in text and "sub-hours-" in text

    # Dropping the hours clears the closed destination.
    resp = admin_client.patch(f"{BASE}/inbound-routes/{route.pk}/", {"business_hours": None}, format="json")
    assert resp.status_code == 200, resp.content
    route.refresh_from_db()
    assert (route.closed_type, route.closed_extension) == ("hangup", None)


def test_inbound_route_needs_a_real_destination(admin_client, setup):
    resp = admin_client.post(f"{BASE}/inbound-routes/", {"number": str(setup["did"].pk), "destination_type": "hangup"},
                             format="json")
    assert resp.status_code == 400 and "destination_type" in _fields(resp)
    resp = admin_client.post(f"{BASE}/inbound-routes/", {"number": str(setup["did"].pk), "destination_type": "queue"},
                             format="json")
    assert resp.status_code == 400 and "queue" in _fields(resp)


def test_target_in_use_cannot_be_deleted(admin_client, setup):
    _queue(admin_client, setup)
    queue = PbxQueue.objects.get()
    menu = api_body(_ivr(admin_client, setup, queue=queue))
    resp = admin_client.delete(f"{BASE}/queues/{queue.pk}/")
    assert resp.status_code == 409 and _code(resp) == "in_use"
    assert PbxQueue.objects.filter(pk=queue.pk).exists()
    resp = admin_client.delete(f"{BASE}/extensions/{setup['ext101'].pk}/")  # the queue's overflow and the IVR timeout
    assert resp.status_code == 409 and _code(resp) == "in_use"
    assert PbxExtension.objects.filter(pk=setup["ext101"].pk).exists()
    # Once nothing points at it, it goes.
    assert admin_client.delete(f"{BASE}/ivr-menus/{menu['id']}/").status_code == 204
    assert admin_client.delete(f"{BASE}/queues/{queue.pk}/").status_code == 204


def test_routing_is_admin_only(employee_client, setup):
    for path in ("business-hours", "ivr-menus", "queues"):
        assert employee_client.get(f"{BASE}/{path}/").status_code == 403
    assert employee_client.post(f"{BASE}/business-hours/", OFFICE, format="json").status_code == 403
    assert not PbxBusinessHours.objects.exists()
