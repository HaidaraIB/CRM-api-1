"""
Phase 5 SaaS controls: recording retention and the recording storage quota,
admin recording deletion, usage rollups and GET /telephony/usage/, and the
CanManageTelephony limited-admin flag on the platform API.
"""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from crm.models import Client, ClientCall
from settings.models import SystemAuditLog
from telephony.models import PbxCall, PbxRecording, PbxTenant, PbxUsageDaily, RecordingMode
from telephony.services import recordings, usage
from telephony.services.compiler import compile_tenant
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.RECORDING_STORAGE_BACKEND = "local"
    return Path(tmp_path) / "pbx_recordings"


@pytest.fixture
def live(tenant_a, company, recording_allowed):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(
        status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1, recording_mode=RecordingMode.EXTERNAL,
    )
    tenant_a.refresh_from_db()
    return tenant_a


def _call(tenant, *, started=None, direction="inbound", status="answered", billsec=90, linked=None):
    started = started or timezone.now()
    return PbxCall.objects.create(
        id=uuid.uuid4(), company_id=tenant.company_id, tenant=tenant, linked_id=linked or str(uuid.uuid4()),
        direction=direction, status=status, started_at=started, ended_at=started + timedelta(seconds=billsec + 5),
        billsec_s=billsec, duration_s=billsec + 5,
    )


def _recording(tenant, call, media, *, age_days=0, size=1000):
    rec_id = uuid.uuid4()
    key = f"telephony/{tenant.company_id}/2026/10/{call.id}/{rec_id}.wav"
    path = media / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    rec = PbxRecording.objects.create(
        id=rec_id, company_id=tenant.company_id, tenant=tenant, call=call, call_uuid=call.id, storage_key=key,
        size_bytes=size, sha256="a" * 64, duration_s=10, format="wav",
        started_at=timezone.now() - timedelta(days=age_days),
    )
    return rec, path


# ---- retention -----------------------------------------------------------------------


def test_retention_deletes_old_recordings_object_first(live, media):
    old_call, new_call = _call(live), _call(live)
    lead = Client.objects.create(name="L", company_id=live.company_id, priority="low", type="cold", phone_number="+9647801234567")
    ClientCall.objects.create(client=lead, pbx_call=old_call, recording_status="ready", source="pbx")
    old, old_path = _recording(live, old_call, media, age_days=100)
    new, new_path = _recording(live, new_call, media, age_days=10)

    call_command("purge_pbx_recordings")
    assert not PbxRecording.objects.filter(pk=old.pk).exists() and not old_path.exists()
    assert PbxRecording.objects.filter(pk=new.pk).exists() and new_path.exists()
    assert ClientCall.objects.get(pbx_call=old_call).recording_status == "deleted"
    log = SystemAuditLog.objects.get(action="telephony.recordings_purged")
    assert log.metadata["deleted"] == 1 and log.metadata["retention_days"] == 90


def test_retention_follows_each_tenants_setting_and_legal_hold(live, media, tenant_b, other_company):
    PbxTenant.objects.filter(pk=live.pk).update(recording_retention_days=7)
    held = PbxTenant.objects.get(pk=tenant_b.pk)
    PbxTenant.objects.filter(pk=held.pk).update(recording_legal_hold=True, recording_retention_days=7)
    held.refresh_from_db()
    mine, _ = _recording(live, _call(live), media, age_days=8)
    theirs, theirs_path = _recording(held, _call(held), media, age_days=400)
    report = recordings.purge_expired()
    assert report == {live.company_id: {"deleted": 1, "bytes": 1000, "failed": 0}}
    assert not PbxRecording.objects.filter(pk=mine.pk).exists()
    assert PbxRecording.objects.filter(pk=theirs.pk).exists() and theirs_path.exists()


def test_dry_run_deletes_nothing(live, media):
    rec, path = _recording(live, _call(live), media, age_days=100)
    assert recordings.purge_expired(dry_run=True) == {live.company_id: {"deleted": 1, "bytes": 1000, "failed": 0}}
    assert PbxRecording.objects.filter(pk=rec.pk).exists() and path.exists()


def test_storage_error_keeps_the_row_for_the_next_run(live, media, monkeypatch):
    rec, _ = _recording(live, _call(live), media, age_days=100)

    def boom(_rec):
        raise recordings.StorageError("R2 down")

    monkeypatch.setattr(recordings, "delete_object", boom)
    assert recordings.purge_expired() == {live.company_id: {"deleted": 0, "bytes": 0, "failed": 1}}
    assert PbxRecording.objects.filter(pk=rec.pk).exists()


# ---- recording storage quota --------------------------------------------------------------


def _limit(telephony_plan, nbytes):
    telephony_plan.limits = {**telephony_plan.limits, "max_pbx_recording_bytes": nbytes}
    telephony_plan.save(update_fields=["limits"])


def test_full_storage_pauses_recording_until_space_is_freed(live, media, telephony_plan, telephony_subscription, admin_client):
    _limit(telephony_plan, 1500)
    first, _ = _recording(live, _call(live), media)
    assert recordings.enforce_quota(live) is False  # 1000 of 1500 bytes
    second, _ = _recording(live, _call(live), media)
    version = live.config_version
    assert recordings.enforce_quota(live) is True
    live.refresh_from_db()
    assert live.recording_paused_quota and live.config_version == version + 1
    assert "MixMonitor" not in compile_tenant(live).files[f"extensions_t{live.realm_key}.conf"]
    assert SystemAuditLog.objects.filter(action="telephony.recording_quota_paused").exists()
    body = admin_client.get(f"{BASE}/settings/").json()["data"]
    assert body["recording_paused_quota"] is True and body["recording_bytes_used"] == 2000
    assert body["recording_bytes_limit"] == 1500

    # The admin deletes one: under the limit again, recording resumes.
    assert admin_client.delete(f"{BASE}/recordings/{second.pk}/").status_code == 204
    live.refresh_from_db()
    assert not live.recording_paused_quota and live.config_version == version + 2
    assert "MixMonitor" in compile_tenant(live).files[f"extensions_t{live.realm_key}.conf"]


def test_ingest_checks_the_quota(live, media, telephony_plan, telephony_subscription, pbx_node):
    _limit(telephony_plan, 1000)
    call = _call(live)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {pbx_node.plain_token}")
    rec_id = str(uuid.uuid4())
    resp = client.post("/api/v1/internal/telephony/recordings", {"node_id": str(pbx_node.id), "recordings": [{
        "recording_id": rec_id, "tenant": live.tag, "call_id": str(call.id),
        "storage_key": f"telephony/{live.company_id}/2026/10/{call.id}/{rec_id}.wav",
        "bytes": 1200, "sha256": "b" * 64, "duration_s": 12, "format": "wav", "started_at": "2026-10-14T09:00:00Z",
    }]}, format="json")
    assert resp.status_code == 200 and resp.json()["accepted"] == [rec_id]
    live.refresh_from_db()
    assert live.recording_paused_quota


# ---- deleting a recording -------------------------------------------------------------------


def test_delete_recording_permissions(live, media, admin_client, employee_client, other_admin_client, tenant_b):
    rec, path = _recording(live, _call(live), media)
    assert employee_client.delete(f"{BASE}/recordings/{rec.pk}/").status_code == 403
    assert other_admin_client.delete(f"{BASE}/recordings/{rec.pk}/").status_code == 404
    PbxTenant.objects.filter(pk=live.pk).update(recording_legal_hold=True)
    resp = admin_client.delete(f"{BASE}/recordings/{rec.pk}/")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "legal_hold"
    PbxTenant.objects.filter(pk=live.pk).update(recording_legal_hold=False)
    assert admin_client.delete(f"{BASE}/recordings/{rec.pk}/").status_code == 204
    assert not path.exists()
    assert SystemAuditLog.objects.get(action="telephony.recording_deleted").metadata["recording_id"] == str(rec.pk)


# ---- settings -------------------------------------------------------------------------------


@pytest.mark.parametrize("days, ok", [(6, False), (7, True), (3650, True), (3651, False)])
def test_retention_setting_range(live, admin_client, days, ok):
    resp = admin_client.patch(f"{BASE}/settings/", {"recording_retention_days": days}, format="json")
    assert (resp.status_code == 200) is ok, resp.content
    if ok:
        live.refresh_from_db()
        assert live.recording_retention_days == days and live.config_version == 1  # no recompile for retention


def test_legal_hold_is_not_the_tenants_to_change(live, admin_client):
    admin_client.patch(f"{BASE}/settings/", {"recording_legal_hold": True}, format="json")
    live.refresh_from_db()
    assert live.recording_legal_hold is False


# ---- usage ----------------------------------------------------------------------------------


def test_rollup_and_usage_report(live, media, admin_client, company):
    now = timezone.now()
    _call(live, direction="outbound", billsec=61)  # 2 billed minutes
    _call(live, direction="outbound", billsec=30)  # 1
    _call(live, direction="inbound", status="missed", billsec=0)
    _call(live, direction="internal", billsec=20)
    _call(live, direction="inbound", started=now - timedelta(days=3), billsec=40)
    _recording(live, _call(live, direction="inbound", billsec=10), media, size=5000)

    call_command("rollup_pbx_usage", "--days", "5")
    today = PbxUsageDaily.objects.get(company=company, day=timezone.localdate())
    assert (today.calls_inbound, today.calls_outbound, today.calls_internal) == (2, 2, 1)
    assert today.calls_missed == 1 and today.outbound_minutes == 3 and today.talk_seconds == 61 + 30 + 20 + 10
    assert today.recordings == 1 and today.recording_bytes == 5000
    earlier = PbxUsageDaily.objects.get(company=company, day=timezone.localdate() - timedelta(days=3))
    assert earlier.calls_inbound == 1
    call_command("rollup_pbx_usage", "--days", "5")  # idempotent
    assert PbxUsageDaily.objects.filter(company=company).count() == 5

    body = admin_client.get(f"{BASE}/usage/").json()["data"]
    assert len(body["days"]) == 30 and body["days"][-1]["calls_outbound"] == 2
    assert body["extensions"]["limit"] == 5 and body["trunks"]["limit"] == 2
    assert body["recording_bytes"]["used"] == 5000
    assert set(body["minutes"]) == {"used", "limit"}


def test_usage_reports_recent_limit_hits(live, admin_client, pbx_node):
    """quota.warning from the node (a live plan limit refused a call) shows on the Usage tab."""
    from tests.test_telephony_calls import T0, Node

    node = Node(pbx_node)
    # Event times are offsets from the fixed T0; keep them an hour ago so they stay inside the 7-day window.
    recent = int((timezone.now() - T0).total_seconds()) - 3600
    warning = lambda quota, limit, at: node.event(  # noqa: E731
        "quota.warning", live, {"quota": quota, "limit": limit, "observed": limit + 1}, at=recent + at)
    result = node.push(warning("max_pbx_concurrent_calls", 5, 0), warning("max_pbx_concurrent_calls", 5, 600),
                       warning("max_pbx_cps", 1, 30))
    assert len(result["accepted"]) == 3 and not result["rejected"]
    body = admin_client.get(f"{BASE}/usage/").json()["data"]
    assert body["concurrent_calls"]["hits_7d"] == 2 and body["concurrent_calls"]["last_hit_at"]
    assert body["cps"]["hits_7d"] == 1
    # A malformed warning is rejected, not stored.
    bad = node.push(node.event("quota.warning", live, {"quota": "max_pbx_minutes", "limit": 1, "observed": 2}))
    assert len(bad["rejected"]) == 1


def test_usage_is_admin_only(live, employee_client):
    assert employee_client.get(f"{BASE}/usage/").status_code == 403


# ---- CanManageTelephony ---------------------------------------------------------------------


def _limited_admin(**flags):
    from accounts.models import LimitedAdmin, User

    user = User.objects.create_user(username=f"la_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@x.test",
                                    password="x" * 12)
    LimitedAdmin.objects.create(user=user, is_active=True, **flags)
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_tenant_managers_can_look_but_not_change_telephony(pbx_node):
    client = _limited_admin(can_manage_tenants=True)
    assert client.get("/api/v1/admin/telephony/nodes/").status_code == 200
    assert client.post("/api/v1/admin/telephony/nodes/", {"name": "pbx-2"}, format="json").status_code == 403


def test_telephony_managers_can_change_it(pbx_node):
    client = _limited_admin(can_manage_telephony=True)
    assert client.get("/api/v1/admin/telephony/nodes/").status_code == 200
    assert client.post("/api/v1/admin/telephony/nodes/", {"name": "pbx-2"}, format="json").status_code == 201


def test_other_limited_admins_get_nothing(pbx_node):
    client = _limited_admin(can_manage_content=True)
    assert client.get("/api/v1/admin/telephony/nodes/").status_code == 403
