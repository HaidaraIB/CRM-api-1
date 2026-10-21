"""
Node migration (POST /admin/telephony/tenants/<company>/migrate-node/) and the rest
of the platform console API: tenant settings, the number verification queue,
fraud alerts and node health.
"""

import base64
import uuid

import pytest
from rest_framework.test import APIClient

from telephony.models import (
    PbxArtifact,
    PbxExtension,
    PbxFraudAlert,
    PbxGatewayPeer,
    PbxNode,
    PbxProvisioningJob,
    PbxTenant,
    PbxTrunk,
)
from telephony.services import provisioning
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/telephony"


def node_client(node):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {node.plain_token}")
    return client


@pytest.fixture
def two_nodes(pbx_node, worker):
    """pbx_node is the tenant's home; the new node's worker is the fake worker."""
    new, token, _ = provisioning.register_node(name="pbx-2", control_url=worker.url)
    new.plain_token = token
    return pbx_node, new


@pytest.fixture
def homed(company, tenant_a, make_extension, employee_user):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE)
    provisioning.request_recompile_for_company(company)
    tenant_a.refresh_from_db()
    PbxTenant.objects.filter(pk=tenant_a.pk).update(applied_version=tenant_a.config_version)
    ext = make_extension(company, "100", user=employee_user)
    PbxExtension.objects.filter(pk=ext.pk).update(agent_status="paused")
    tenant_a.refresh_from_db()
    return tenant_a


def _gateway(company, node, ip="10.78.0.2"):
    trunk = PbxTrunk.objects.create(company=company, name="GW", kind="gsm_gateway", host=ip,
                                    allowed_sources=[f"{ip}/32"], status=PbxTrunk.Status.READY, health="ready")
    return PbxGatewayPeer.objects.create(company=company, trunk=trunk, node=node, tunnel_ip=ip,
                                         status=PbxGatewayPeer.Status.VERIFIED,
                                         public_key=base64.b64encode(b"g" * 32).decode())


def test_migration_drains_the_old_node_and_homes_the_new(superadmin_client, company, homed, two_nodes, worker):
    old, new = two_nodes
    peer = _gateway(company, old)
    before = homed.config_version
    resp = superadmin_client.post(f"{ADMIN}/tenants/{company.id}/migrate-node/", {"node": str(new.pk)}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()["data"]
    assert body["gateways_moved"] == 1 and body["job"]["kind"] == "migrate"
    homed.refresh_from_db()
    assert homed.home_node == new and homed.draining_node == old and homed.draining_version == before + 1
    assert homed.config_version == before + 2 and homed.applied_version is None

    # Old node: only the remove artifact, which it may fetch; the new node: the fresh apply.
    old_state = node_client(old).get(f"/api/v1/internal/telephony/nodes/{old.id}/desired-state").json()
    [drain] = old_state["tenants"]
    assert drain["version"] == before + 1 and drain["tenant"] == homed.tag
    assert PbxArtifact.objects.get(tenant=homed, version=before + 1).action == "remove"
    assert node_client(old).get(drain["artifact_path"]).status_code == 200
    third, token, _ = provisioning.register_node(name="pbx-3")
    third.plain_token = token
    assert node_client(third).get(drain["artifact_path"].replace(str(old.id), str(third.id))).status_code == 404
    new_state = node_client(new).get(f"/api/v1/internal/telephony/nodes/{new.id}/desired-state").json()
    assert [t["version"] for t in new_state["tenants"]] == [before + 2]

    # Gateways follow: the new node's peer list, unverified until reconfigured on site.
    assert new_state["gateway_peers"] == [{"public_key": peer.public_key, "allowed_ip": "10.78.0.2/32"}]
    assert old_state["gateway_peers"] == []
    peer.refresh_from_db()
    assert peer.node == new and peer.status == "enrolled"
    assert PbxTrunk.objects.get(pk=peer.trunk_id).status == "pending"

    # Paused agents re-pushed to the new node (its astdb starts empty).
    assert [(b["extension"], b["status"]) for p, _, b in worker.requests if p == "/v1/agents/status"] == [("100", "paused")]

    # The old node reports the removal: the drain is over.
    resp = node_client(old).post(f"/api/v1/internal/telephony/nodes/{old.id}/apply-result", {
        "tenant": homed.tag, "version": before + 1, "status": "applied", "running_version": before + 1,
        "finished_at": "2026-10-16T09:00:00Z"}, format="json")
    assert resp.status_code == 200
    homed.refresh_from_db()
    assert homed.draining_node is None and homed.draining_version is None
    assert node_client(old).get(f"/api/v1/internal/telephony/nodes/{old.id}/desired-state").json()["tenants"] == []

    # The new node applies: active again.
    node_client(new).post(f"/api/v1/internal/telephony/nodes/{new.id}/apply-result", {
        "tenant": homed.tag, "version": before + 2, "status": "applied", "running_version": before + 2,
        "finished_at": "2026-10-16T09:00:05Z"}, format="json")
    homed.refresh_from_db()
    assert homed.status == "active" and homed.applied_version == before + 2
    # ... and the paused agent is pushed again, now that the new node knows the tenant.
    assert [(b["extension"], b["status"]) for p, _, b in worker.requests if p == "/v1/agents/status"] == [
        ("100", "paused"), ("100", "paused")]


def test_draining_node_may_still_report_calls(homed, two_nodes, superadmin_client, company):
    old, new = two_nodes
    superadmin_client.post(f"{ADMIN}/tenants/{company.id}/migrate-node/", {"node": str(new.pk)}, format="json")
    ev = {"event_id": str(uuid.uuid4()), "type": "agent.status_changed", "tenant": homed.tag,
          "occurred_at": "2026-10-16T09:00:00Z", "seq": 1, "data": {"extension": "100", "registered": True}}
    body = {"node_id": str(old.id), "batch_id": str(uuid.uuid4()), "sent_at": "2026-10-16T09:00:01Z", "events": [ev]}
    assert node_client(old).post("/api/v1/internal/telephony/events", body, format="json").json()["accepted"]
    third, token, _ = provisioning.register_node(name="pbx-3")
    third.plain_token = token
    resp = node_client(third).post("/api/v1/internal/telephony/events",
                                   {**body, "node_id": str(third.id), "events": [{**ev, "event_id": str(uuid.uuid4())}]},
                                   format="json")
    assert resp.json()["rejected"][0]["code"] == "tenant_not_on_node"


@pytest.mark.parametrize("case, code", [
    ("same", "same_node"), ("draining", "migration_in_progress"), ("offline", "node_not_active"), ("full", "node_full"),
])
def test_migration_refusals(superadmin_client, company, homed, two_nodes, case, code):
    old, new = two_nodes
    target = new
    if case == "same":
        target = old
    elif case == "draining":
        PbxTenant.objects.filter(pk=homed.pk).update(draining_node=old, draining_version=1)
    elif case == "offline":
        PbxNode.objects.filter(pk=new.pk).update(status=PbxNode.Status.OFFLINE)
    elif case == "full":
        PbxNode.objects.filter(pk=new.pk).update(max_tenants=0)
    resp = superadmin_client.post(f"{ADMIN}/tenants/{company.id}/migrate-node/", {"node": str(target.pk)}, format="json")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == code


def test_repush_sends_the_outbound_block_too(superadmin_client, company, homed, two_nodes, worker):
    old, new = two_nodes
    PbxTenant.objects.filter(pk=homed.pk).update(home_node=new, outbound_suspended=True)
    body = superadmin_client.post(f"{ADMIN}/tenants/{company.id}/repush/").json()["data"]
    assert body == {"paused_agents": 1, "failed_agents": [], "outbound": "blocked"}
    assert [b["status"] for p, _, b in worker.requests if p == "/v1/tenants/outbound"] == ["blocked"]


# ---- tenant settings ------------------------------------------------------------------------


def test_fraud_limits_and_legal_hold(superadmin_client, company, homed):
    version = homed.config_version
    resp = superadmin_client.patch(f"{ADMIN}/tenants/{company.id}/",
                                   {"fraud_limits": {"max_calls_per_hour": 20}, "recording_legal_hold": True}, format="json")
    assert resp.status_code == 200, resp.content
    body = resp.json()["data"]
    assert body["effective_fraud_limits"]["max_calls_per_hour"] == 20 and body["recording_legal_hold"] is True
    homed.refresh_from_db()
    assert homed.config_version == version + 1  # the manifest carries the limits
    resp = superadmin_client.patch(f"{ADMIN}/tenants/{company.id}/", {"fraud_limits": {"max_calls_per_hour": -1}},
                                   format="json")
    assert resp.status_code == 400


# ---- number verification queue ------------------------------------------------------------


def test_number_queue(superadmin_client, company, other_company, make_trunk, make_number):
    trunk, other_trunk = make_trunk(company), make_trunk(other_company)
    pending = make_number(company, trunk, "+9647700000001", verified=False)
    make_number(other_company, other_trunk, "+9647700000002")
    rows = superadmin_client.get(f"{ADMIN}/numbers/?verified=false").json()["data"]
    rows = rows["results"] if isinstance(rows, dict) else rows
    assert [r["e164"] for r in rows] == ["+9647700000001"]
    assert rows[0]["company_name"] == company.name and rows[0]["trunk_kind"] == "sip"
    assert superadmin_client.post(f"{ADMIN}/numbers/{pending.pk}/verify/", {"verified": True}, format="json").status_code == 200
    rows = superadmin_client.get(f"{ADMIN}/numbers/?verified=false").json()["data"]
    assert (rows["results"] if isinstance(rows, dict) else rows) == []


# ---- fraud alerts and health ----------------------------------------------------------------


def test_fraud_alert_list_and_resolve(superadmin_client, company, homed):
    alert = PbxFraudAlert.objects.create(company=company, tenant=homed, event_id=uuid.uuid4(), rule="calls_per_hour",
                                         severity="critical", action_taken="outbound_suspended", observed=130,
                                         threshold=120, occurred_at="2026-10-16T09:00:00Z")
    rows = superadmin_client.get(f"{ADMIN}/fraud-alerts/?open=true").json()["data"]
    rows = rows["results"] if isinstance(rows, dict) else rows
    assert [r["id"] for r in rows] == [str(alert.pk)]
    resp = superadmin_client.post(f"{ADMIN}/fraud-alerts/{alert.pk}/resolve/", {"note": "Customer campaign"}, format="json")
    assert resp.status_code == 200 and resp.json()["data"]["resolved_at"]
    rows = superadmin_client.get(f"{ADMIN}/fraud-alerts/?open=true").json()["data"]
    assert (rows["results"] if isinstance(rows, dict) else rows) == []


def test_health_reports_each_worker(superadmin_client, two_nodes, worker):
    PbxNode.objects.filter(pk=two_nodes[0].pk).update(control_url="")
    body = superadmin_client.get(f"{ADMIN}/health/").json()["data"]
    by_name = {n["name"]: n for n in body["nodes"]}
    assert by_name["pbx-2"]["worker"] is not None and by_name["pbx-2"]["worker"]["ari"] is True
    assert by_name[two_nodes[0].name]["worker"] is None  # no control URL: unreachable
    assert set(body) == {"nodes", "open_fraud_alerts", "numbers_to_verify", "failed_tenants"}


@pytest.mark.parametrize("path", ["numbers/", "fraud-alerts/", "health/"])
def test_console_is_for_the_platform_only(admin_client, path):
    assert admin_client.get(f"{ADMIN}/{path}").status_code == 403


def test_console_creates_a_node_with_its_gateway_link(superadmin_client):
    """The admin console posts every node field (it used to 500: register_node took only four)."""
    import base64 as b64

    key = b64.b64encode(b"k" * 32).decode()
    resp = superadmin_client.post(f"{ADMIN}/nodes/", {
        "name": "pbx-ui", "region": "iq", "control_url": "http://10.77.0.3:7070", "status": "active", "max_tenants": 50,
        "gateway_wg_public_key": key, "gateway_wg_endpoint": "pbx-ui.example.com:51821", "gateway_wg_address": "10.79.0.1",
    }, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()["data"]
    assert body["node_token"].startswith("lpn_") and body["gateway_wg_endpoint"] == "pbx-ui.example.com:51821"
    node = PbxNode.objects.get(name="pbx-ui")
    assert node.gateway_wg_public_key == key and str(node.gateway_wg_address) == "10.79.0.1" and node.max_tenants == 50


@pytest.mark.parametrize("field, value", [
    ("gateway_wg_public_key", "short"), ("gateway_wg_endpoint", "not a host"), ("control_url", "ftp://x"),
    ("max_tenants", 0),
])
def test_node_fields_are_validated(superadmin_client, field, value):
    resp = superadmin_client.post(f"{ADMIN}/nodes/", {"name": "pbx-bad", field: value}, format="json")
    assert resp.status_code == 400, resp.content
