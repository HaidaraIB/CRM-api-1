"""Provisioning state machine + internal node API (CRM-pbx contract §2, §5)."""

import gzip
import io
import json
import tarfile
from unittest import mock

import pytest
from rest_framework.test import APIClient

from telephony import contract
from telephony.models import PbxArtifact, PbxNode, PbxProvisioningJob as Job, PbxTenant
from telephony.services import provisioning
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db


def node_client(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def desired(node):
    resp = node_client(node.plain_token).get(f"/api/v1/internal/telephony/nodes/{node.id}/desired-state")
    assert resp.status_code == 200, resp.content
    return json.loads(resp.content)


def report(node, **payload):
    body = {"finished_at": "2026-10-14T09:00:07Z", **payload}
    return node_client(node.plain_token).post(
        f"/api/v1/internal/telephony/nodes/{node.id}/apply-result", body, format="json"
    )


def provision(company):
    tenant = provisioning.ensure_tenant(company)
    job = provisioning.request_job(tenant, Job.Kind.PROVISION)
    job.refresh_from_db()
    tenant.refresh_from_db()
    return tenant, job


def unpack(blob):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()}


# ---- state machine ---------------------------------------------------------


def test_provision_compiles_v1_and_waits_for_node(company, pbx_node, make_extension):
    make_extension(company, "100")
    tenant, job = provision(company)

    assert tenant.home_node_id == pbx_node.id
    assert tenant.status == PbxTenant.Status.PENDING
    assert tenant.config_version == 1 and tenant.applied_version is None
    assert job.status == Job.Status.AWAITING_APPLY and job.target_version == 1
    artifact = PbxArtifact.objects.get(tenant=tenant, version=1)
    assert artifact.manifest["tenant"] == tenant.tag and artifact.manifest["company_id"] == company.id
    assert artifact.manifest["schema"] == "loop.pbx.artifact/2"
    # Stored encrypted, not as the raw tarball.
    assert "tdev4test" not in artifact.blob_encrypted and tenant.tag not in artifact.blob_encrypted


def test_full_cycle_desired_state_artifact_apply(company, pbx_node, make_extension):
    make_extension(company, "100")
    tenant, job = provision(company)

    state = desired(pbx_node)
    assert state["node_id"] == str(pbx_node.id)
    [entry] = state["tenants"]
    assert entry["tenant"] == tenant.tag and entry["version"] == 1

    resp = node_client(pbx_node.plain_token).get(entry["artifact_path"])
    assert resp.status_code == 200 and resp["Content-Type"] == "application/gzip"
    blob = resp.content
    assert contract.sha256_hex(blob) == entry["artifact_sha256"]
    members = unpack(blob)
    assert set(members) == {"manifest.json", f"pjsip_{tenant.tag}.conf", f"extensions_{tenant.tag}.conf"}

    assert report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1,
                  asterisk_version="20.6.0").status_code == 200
    tenant.refresh_from_db()
    job.refresh_from_db()
    pbx_node.refresh_from_db()
    assert tenant.status == PbxTenant.Status.ACTIVE and tenant.applied_version == 1
    assert job.status == Job.Status.SUCCEEDED
    assert pbx_node.asterisk_version == "20.6.0" and pbx_node.last_seen_at is not None


def test_apply_result_is_idempotent(company, pbx_node):
    tenant, _ = provision(company)
    first = report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1)
    again = report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1)
    assert json.loads(first.content) == {"status": "recorded"}
    assert json.loads(again.content) == {"status": "duplicate"}


def test_failed_first_apply_marks_tenant_failed(company, pbx_node):
    tenant, job = provision(company)
    report(pbx_node, tenant=tenant.tag, version=1, status="rolled_back", running_version=None,
           error={"code": "reload_failed", "detail": "boom"})
    tenant.refresh_from_db()
    job.refresh_from_db()
    assert tenant.status == PbxTenant.Status.FAILED and job.status == Job.Status.FAILED
    assert "reload_failed" in tenant.last_apply_error


def test_failed_later_apply_keeps_tenant_active_on_old_version(company, pbx_node, make_extension):
    tenant, _ = provision(company)
    report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1)
    make_extension(company, "101")
    provisioning.request_recompile_for_company(company)
    tenant.refresh_from_db()
    assert tenant.config_version == 2

    report(pbx_node, tenant=tenant.tag, version=2, status="rolled_back", running_version=1,
           error={"code": "post_reload_check_failed", "detail": "tdev"})
    tenant.refresh_from_db()
    assert tenant.status == PbxTenant.Status.ACTIVE and tenant.applied_version == 1
    assert "post_reload_check_failed" in tenant.last_apply_error


def test_old_version_report_never_moves_applied_backwards(company, pbx_node):
    tenant, _ = provision(company)
    provisioning.request_recompile_for_company(company)
    report(pbx_node, tenant=tenant.tag, version=2, status="applied", running_version=2)
    report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1)
    tenant.refresh_from_db()
    assert tenant.applied_version == 2


def test_new_compile_supersedes_waiting_job(company, pbx_node):
    tenant, first = provision(company)
    second = provisioning.request_recompile_for_company(company)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == Job.Status.SUPERSEDED
    assert second.status == Job.Status.AWAITING_APPLY and second.target_version == 2


def test_pending_jobs_coalesce(company, pbx_node, settings):
    settings.TELEPHONY_QUEUE_ENABLED = True
    tenant = provisioning.ensure_tenant(company)
    with mock.patch("django_q.tasks.async_task") as enqueue:
        j1 = provisioning.request_job(tenant, Job.Kind.PROVISION)
        j2 = provisioning.request_recompile_for_company(company)
    assert j1.pk == j2.pk
    assert Job.objects.filter(tenant=tenant).count() == 1
    # Queued on the dedicated telephony cluster, not the main queue.
    assert enqueue.call_args.kwargs["cluster"] == "telephony"
    assert enqueue.call_args.args[0] == "telephony.tasks.run_provisioning_job"


def test_run_job_twice_publishes_once(company, pbx_node):
    tenant, job = provision(company)
    provisioning.run_job(job.pk)
    provisioning.run_job(job.pk)
    assert PbxArtifact.objects.filter(tenant=tenant).count() == 1


def test_no_node_capacity_fails_cleanly(company):
    tenant, job = provision(company)
    assert job.status == Job.Status.FAILED and "no_node_available" in job.last_error
    assert tenant.status == PbxTenant.Status.FAILED and tenant.config_version == 0
    assert not PbxArtifact.objects.exists()


def test_draining_node_gets_no_new_tenants(company, pbx_node):
    PbxNode.objects.update(status=PbxNode.Status.DRAINING)
    _, job = provision(company)
    assert job.status == Job.Status.FAILED


def test_least_loaded_node_is_picked(company, other_company, pbx_node):
    from telephony.services.provisioning import register_node

    second, _, _ = register_node(name="pbx-2")
    provision(company)  # pbx-1 and pbx-2 both empty -> pbx-1 by name
    tenant_b, _ = provision(other_company)
    assert tenant_b.home_node_id == second.id


def test_suspend_then_resume(company, pbx_node):
    tenant, _ = provision(company)
    report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1)

    provisioning.request_job(tenant, Job.Kind.SUSPEND)
    tenant.refresh_from_db()
    assert tenant.outbound_suspended and tenant.config_version == 2
    assert PbxArtifact.objects.get(tenant=tenant, version=2).manifest["outbound"] == "suspended"
    report(pbx_node, tenant=tenant.tag, version=2, status="applied", running_version=2)
    tenant.refresh_from_db()
    assert tenant.status == PbxTenant.Status.SUSPENDED

    provisioning.request_job(tenant, Job.Kind.RESUME)
    report(pbx_node, tenant=tenant.tag, version=3, status="applied", running_version=3)
    tenant.refresh_from_db()
    assert tenant.status == PbxTenant.Status.ACTIVE and not tenant.outbound_suspended


def test_deprovision_publishes_remove_and_drops_from_desired_state(company, pbx_node):
    tenant, _ = provision(company)
    report(pbx_node, tenant=tenant.tag, version=1, status="applied", running_version=1)
    provisioning.request_job(tenant, Job.Kind.DEPROVISION)
    artifact = PbxArtifact.objects.get(tenant=tenant, version=2)
    assert artifact.action == "remove" and artifact.manifest["files"] == []
    assert [t["version"] for t in desired(pbx_node)["tenants"]] == [2]

    report(pbx_node, tenant=tenant.tag, version=2, status="applied", running_version=2)
    tenant.refresh_from_db()
    assert tenant.status == PbxTenant.Status.DEPROVISIONED
    assert desired(pbx_node)["tenants"] == []


def test_compile_error_fails_job_without_publishing(company, pbx_node, make_extension):
    from telephony.models import PbxExtension

    make_extension(company, "100")
    PbxExtension.objects.update(number="0100")  # bypasses serializer validation
    tenant, job = provision(company)
    assert job.status == Job.Status.FAILED and "compile_failed" in job.last_error
    assert not PbxArtifact.objects.exists()


# ---- internal API auth -----------------------------------------------------


def test_internal_api_requires_node_token(pbx_node):
    url = f"/api/v1/internal/telephony/nodes/{pbx_node.id}/desired-state"
    assert APIClient().get(url).status_code in (401, 403)
    assert node_client("lpn_not-a-real-token").get(url).status_code == 401
    # A user JWT-style bearer is not a node token either.
    assert node_client("eyJhbGciOi.fake.jwt").get(url).status_code in (401, 403)


def test_node_cannot_read_another_nodes_state_or_artifacts(company, pbx_node):
    from telephony.services.provisioning import register_node

    tenant, _ = provision(company)
    other, other_token, _ = register_node(name="pbx-2")
    c = node_client(other_token)
    assert c.get(f"/api/v1/internal/telephony/nodes/{pbx_node.id}/desired-state").status_code == 404
    assert c.get(f"/api/v1/internal/telephony/nodes/{other.id}/desired-state").json()["tenants"] == []
    # Even on its own URL prefix, it cannot fetch an artifact of a tenant homed elsewhere.
    assert c.get(f"/api/v1/internal/telephony/nodes/{other.id}/artifacts/{tenant.tag}/1/").status_code == 404
    resp = c.post(
        f"/api/v1/internal/telephony/nodes/{other.id}/apply-result",
        {"tenant": tenant.tag, "version": 1, "status": "applied", "finished_at": "2026-10-14T09:00:07Z"},
        format="json",
    )
    assert resp.status_code == 404
    tenant.refresh_from_db()
    assert tenant.applied_version is None


def test_rotated_node_token_replaces_old(company, pbx_node):
    old = pbx_node.plain_token
    new, _ = provisioning.rotate_node_tokens(pbx_node)
    url = f"/api/v1/internal/telephony/nodes/{pbx_node.id}/desired-state"
    assert node_client(old).get(url).status_code == 401
    assert node_client(new).get(url).status_code == 200


def test_apply_result_payload_is_validated(company, pbx_node):
    tenant, _ = provision(company)
    bad = report(pbx_node, tenant=tenant.tag, version=1, status="applied", error={"code": "x"})
    assert bad.status_code == 400
    bad = report(pbx_node, tenant="42", version=1, status="applied")
    assert bad.status_code == 400


def test_node_accept_headers_are_honoured(company, pbx_node):
    """The applier sends these exact Accept headers (CRM-pbx loop_pbx/applier/crm.py)."""
    tenant, _ = provision(company)
    c = node_client(pbx_node.plain_token)
    state = c.get(f"/api/v1/internal/telephony/nodes/{pbx_node.id}/desired-state", HTTP_ACCEPT="application/json")
    assert state.status_code == 200
    path = json.loads(state.content)["tenants"][0]["artifact_path"]
    art = c.get(path, HTTP_ACCEPT="application/gzip")
    assert art.status_code == 200 and art["Content-Type"] == "application/gzip"
    res = c.post(
        f"/api/v1/internal/telephony/nodes/{pbx_node.id}/apply-result",
        {"tenant": tenant.tag, "version": 1, "status": "applied", "finished_at": "2026-10-14T09:00:07Z"},
        format="json",
        HTTP_ACCEPT="application/json",
    )
    assert res.status_code == 200


def test_internal_responses_use_contract_shape_not_envelope(pbx_node):
    state = desired(pbx_node)
    assert set(state) == {"node_id", "generated_at", "tenants", "gateway_peers"}  # contract section 5
