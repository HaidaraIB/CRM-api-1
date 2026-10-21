"""
POST /telephony/trunks/{id}/test/ (spec section 6): the home node's SIP OPTIONS
probe (contract section 6) plus the CRM's own checks of the trunk's routing.
"""

import pytest
from django.core.cache import cache

from telephony import contract
from telephony.models import PbxInboundRoute, PbxOutboundRoute, PbxTenant, PbxTrunk
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"
PROBED = {"trunk": "", "contact": "sip:198.51.100.7:5060", "transport": "udp", "reachable": True,
          "status_code": 200, "rtt_ms": 23}


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()


@pytest.fixture
def setup(company, tenant_a, worker, make_trunk, make_number, make_extension):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=3, config_version=3)
    tenant_a.refresh_from_db()
    trunk = make_trunk(company)
    worker.replies["/v1/trunks/probe"] = (200, {**PROBED, "trunk": contract.trunk_endpoint(tenant_a.realm_key, trunk.id)})
    return {"tenant": tenant_a, "trunk": trunk, "ext": make_extension(company, "100")}


def _checks(resp):
    return {c["id"]: c for c in resp.json()["data"]["checks"]}


def _test(client, trunk):
    return client.post(f"{BASE}/trunks/{trunk.pk}/test/")


def test_probe_asks_the_home_node_for_this_trunk(admin_client, setup, worker):
    resp = _test(admin_client, setup["trunk"])
    assert resp.status_code == 200, resp.content
    [(path, headers, sent)] = worker.requests
    assert path == "/v1/trunks/probe"
    assert sent["tenant"] == setup["tenant"].tag
    assert sent["trunk"] == contract.trunk_endpoint(setup["tenant"].realm_key, setup["trunk"].id)
    assert set(sent) == {"request_id", "tenant", "trunk"}  # the node probes its own config, never a given address
    options = _checks(resp)["options"]
    assert options["status"] == "ok" and options["data"]["rtt_ms"] == 23 and options["data"]["status_code"] == 200
    trunk = PbxTrunk.objects.get(pk=setup["trunk"].pk)
    assert trunk.health == "ready" and trunk.health_changed_at is not None


def test_full_report_for_a_routed_trunk(admin_client, setup, company, make_number):
    trunk = setup["trunk"]
    did = make_number(company, trunk, "+9647700000001")
    spare = make_number(company, trunk, "+9647700000002")
    pending = make_number(company, trunk, "+9647700000003", verified=False)
    PbxInboundRoute.objects.create(company=company, number=did, extension=setup["ext"])
    PbxOutboundRoute.objects.create(company=company, name="Mobiles", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    resp = _test(admin_client, trunk)
    body = resp.json()["data"]
    checks = _checks(resp)
    assert body["ok"] is True
    assert checks["config"] == {"id": "config", "status": "ok", "data": {"version": 3}}
    assert checks["register"]["status"] == "skipped"
    assert checks["identify"]["status"] == "ok"
    assert checks["numbers"]["status"] == "warning" and checks["numbers"]["data"]["unverified"] == [pending.e164]
    assert checks["inbound"]["status"] == "warning" and checks["inbound"]["data"]["unrouted"] == [spare.e164]
    assert checks["outbound"]["status"] == "ok" and checks["outbound"]["data"]["routes"] == ["Mobiles"]


def test_unreachable_trunk_fails_and_is_marked_down(admin_client, setup, worker):
    worker.replies["/v1/trunks/probe"] = (200, {**PROBED, "reachable": False, "detail": "no answer"})
    for key in ("status_code", "rtt_ms"):
        worker.replies["/v1/trunks/probe"][1].pop(key)
    resp = _test(admin_client, setup["trunk"])
    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is False
    assert _checks(resp)["options"]["status"] == "failed"
    trunk = PbxTrunk.objects.get(pk=setup["trunk"].pk)
    assert trunk.health == "down" and trunk.health_reason == "no answer"


@pytest.mark.parametrize("reply, reason", [
    ((422, {"code": "unknown_trunk"}), "not_on_node"),
    ((503, {"code": "ari_unavailable"}), "pbx_unavailable"),
])
def test_node_refusals_are_reported_as_failed_checks(admin_client, setup, worker, reply, reason):
    worker.replies["/v1/trunks/probe"] = reply
    options = _checks(_test(admin_client, setup["trunk"]))["options"]
    assert options == {"id": "options", "status": "failed", "data": {"reason": reason}}


def test_trunk_without_sources_or_routes(admin_client, setup):
    PbxTrunk.objects.filter(pk=setup["trunk"].pk).update(allowed_sources=[])
    PbxTenant.objects.filter(pk=setup["tenant"].pk).update(config_version=4)
    checks = _checks(_test(admin_client, setup["trunk"]))
    assert checks["identify"]["status"] == "failed"
    assert checks["config"]["status"] == "warning" and checks["config"]["data"]["reason"] == "applying"
    assert checks["numbers"]["data"]["reason"] == "none"
    assert checks["outbound"]["data"]["reason"] == "none"


def test_disabled_trunk_is_not_probed(admin_client, setup, worker):
    PbxTrunk.objects.filter(pk=setup["trunk"].pk).update(status=PbxTrunk.Status.DISABLED)
    checks = _checks(_test(admin_client, setup["trunk"]))
    assert checks["config"]["status"] == "failed" and checks["options"]["status"] == "skipped"
    assert worker.requests == []


def test_tests_are_throttled_per_trunk(admin_client, setup):
    assert _test(admin_client, setup["trunk"]).status_code == 200
    resp = _test(admin_client, setup["trunk"])
    assert resp.status_code == 429 and resp.json()["error"]["code"] == "too_many_tests"


def test_not_provisioned(admin_client, setup):
    PbxTenant.objects.filter(pk=setup["tenant"].pk).update(status=PbxTenant.Status.PENDING)
    resp = _test(admin_client, setup["trunk"])
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "not_provisioned"


def test_another_tenants_trunk_is_404_and_never_probed(admin_client, setup, worker, other_company, make_trunk):
    foreign = make_trunk(other_company)
    assert _test(admin_client, foreign).status_code == 404
    assert worker.requests == []


def test_admin_only(employee_client, setup):
    assert _test(employee_client, setup["trunk"]).status_code == 403
