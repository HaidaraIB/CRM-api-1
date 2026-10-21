"""
Fraud (contract section 6, fraud): limits shipped in the manifest, fraud.alert
ingest, the automatic suspend, and the node's outbound switch on suspend/resume.
"""

import uuid

import pytest
from rest_framework.test import APIClient

from settings.models import SystemAuditLog
from telephony.models import PbxArtifact, PbxFraudAlert, PbxTenant
from telephony.services import fraud, provisioning
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db


@pytest.fixture
def live(tenant_a, worker):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1)
    tenant_a.refresh_from_db()
    return tenant_a


def push(node, tenant, data, event_id=None):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {node.plain_token}")
    ev = {"event_id": event_id or str(uuid.uuid4()), "type": "fraud.alert", "tenant": tenant.tag,
          "occurred_at": "2026-10-16T09:00:00Z", "seq": 1, "data": data}
    resp = client.post("/api/v1/internal/telephony/events", {"node_id": str(node.id), "batch_id": str(uuid.uuid4()),
                                                             "sent_at": "2026-10-16T09:00:01Z", "events": [ev]},
                       format="json")
    assert resp.status_code == 200, resp.content
    return resp.json()


BLOCKED = {"rule": "calls_per_hour", "severity": "critical", "action_taken": "outbound_suspended",
           "observed": 121, "threshold": 120, "destination": "+447700900123"}


def test_manifest_carries_the_effective_limits(live, settings):
    settings.TELEPHONY_FRAUD_DEFAULTS = {**settings.TELEPHONY_FRAUD_DEFAULTS, "max_calls_per_hour": 120}
    PbxTenant.objects.filter(pk=live.pk).update(fraud_limits={"max_calls_per_hour": 30, "action": "alert"})
    provisioning.request_recompile_for_company(live.company)
    live.refresh_from_db()
    manifest = PbxArtifact.objects.get(tenant=live, version=live.config_version).manifest
    assert manifest["fraud"]["max_calls_per_hour"] == 30 and manifest["fraud"]["action"] == "alert"
    assert manifest["fraud"]["home_prefixes"] == settings.TELEPHONY_FRAUD_DEFAULTS["home_prefixes"]


def test_blocking_alert_suspends_the_tenant(live, pbx_node):
    result = push(pbx_node, live, BLOCKED)
    assert len(result["accepted"]) == 1
    alert = PbxFraudAlert.objects.get()
    assert (alert.rule, alert.action_taken, alert.observed, alert.destination) == (
        "calls_per_hour", "outbound_suspended", 121.0, "+447700900123")
    live.refresh_from_db()
    assert live.outbound_suspended and live.outbound_blocked_on_node
    assert live.suspended_reason.startswith("Fraud alert")
    manifest = PbxArtifact.objects.get(tenant=live, version=live.config_version).manifest
    assert manifest["outbound"] == "suspended"
    assert SystemAuditLog.objects.filter(action="telephony.fraud_suspended").exists()


def test_alert_only_rule_suspends_nothing(live, pbx_node):
    push(pbx_node, live, {**BLOCKED, "severity": "warning", "action_taken": "none"})
    live.refresh_from_db()
    assert not live.outbound_suspended and PbxFraudAlert.objects.count() == 1


def test_replayed_alert_is_stored_once(live, pbx_node):
    event_id = str(uuid.uuid4())
    push(pbx_node, live, BLOCKED, event_id)
    assert push(pbx_node, live, BLOCKED, event_id)["duplicates"] == [event_id]
    assert PbxFraudAlert.objects.count() == 1


@pytest.mark.parametrize("data", [
    {**BLOCKED, "rule": "too_many_kittens"},
    {**BLOCKED, "action_taken": "drop_everything"},
    {**BLOCKED, "destination": "not-a-number"},
])
def test_invalid_alerts_are_rejected(live, pbx_node, data):
    result = push(pbx_node, live, data)
    assert len(result["rejected"]) == 1 and PbxFraudAlert.objects.count() == 0


def test_alert_for_a_tenant_on_another_node_is_rejected(live, other_company, tenant_b, pbx_node):
    from telephony.services.provisioning import register_node

    other, token, _ = register_node(name="pbx-other")
    other.plain_token = token
    PbxTenant.objects.filter(pk=tenant_b.pk).update(home_node=other)
    result = push(pbx_node, PbxTenant.objects.get(pk=tenant_b.pk), BLOCKED)
    assert result["rejected"][0]["code"] == "tenant_not_on_node" and not PbxFraudAlert.objects.exists()


# ---- the node's outbound switch -----------------------------------------------------------


def test_platform_suspend_blocks_on_the_node(live, worker, superadmin_client, company):
    resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/suspend/", {"reason": "unpaid"},
                                  format="json")
    assert resp.status_code == 202
    [(path, _, body)] = [r for r in worker.requests if r[0] == "/v1/tenants/outbound"]
    assert body["tenant"] == live.tag and body["status"] == "blocked"
    live.refresh_from_db()
    assert live.outbound_suspended and live.outbound_blocked_on_node


def test_suspend_still_recompiles_when_the_node_is_unreachable(live, worker, superadmin_client, company):
    worker.replies["/v1/tenants/outbound"] = (503, {"code": "ari_unavailable"})
    resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/suspend/", {}, format="json")
    assert resp.status_code == 202
    live.refresh_from_db()
    assert live.outbound_suspended and not live.outbound_blocked_on_node


def test_resume_lifts_the_node_block_first(live, worker, superadmin_client, company, pbx_node):
    push(pbx_node, live, BLOCKED)
    worker.replies["/v1/tenants/outbound"] = (503, {"code": "ari_unavailable"})
    resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/resume/")
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "pbx_unavailable"
    live.refresh_from_db()
    assert live.outbound_suspended  # nothing changed

    del worker.replies["/v1/tenants/outbound"]
    resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/resume/")
    assert resp.status_code == 202
    assert [b["status"] for p, _, b in worker.requests if p == "/v1/tenants/outbound"] == ["enabled", "enabled"]
    live.refresh_from_db()
    assert not live.outbound_suspended and not live.outbound_blocked_on_node


# ---- limits ----------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, ok", [
    ({"max_calls_per_hour": 30}, True),
    ({"max_international_share": 0.2, "home_prefixes": ["+964", "+1"]}, True),
    ({"premium_prefixes": ["+88213", "+979"]}, True),
    ({"max_calls_per_hour": 0}, False),
    ({"max_international_share": 1.5}, False),
    ({"home_prefixes": ["964"]}, False),
    ({"action": "explode"}, False),
    ({"surprise": 1}, False),
])
def test_validate_limits(raw, ok):
    if ok:
        assert fraud.validate_limits(raw) == {k: (float(v) if k == "max_international_share" else v) for k, v in raw.items()}
    else:
        with pytest.raises(fraud.InvalidLimits):
            fraud.validate_limits(raw)
