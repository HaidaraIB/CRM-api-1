"""
End-to-end: CRM compiler → internal API → CRM-pbx node applier → Asterisk → apply-result.

Opt-in (needs Docker and the sibling CRM-pbx checkout with its venv):

    docker compose -f ../CRM-pbx/deploy/dev/docker-compose.yml up -d --build
    LOOP_PBX_E2E=1 .venv/Scripts/python.exe -m pytest tests/test_telephony_e2e.py -n 0

It provisions the same two tenants as CRM-pbx's dev sample through the real
models and compiler, runs ``python -m loop_pbx.applier --once`` against a live
Django server, then runs CRM-pbx's ``scripts/dev_smoke.py`` (ARI + cross-tenant
dialplan checks) against what the compiler produced.
"""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from telephony.models import DeviceType, PbxInboundRoute, PbxOutboundRoute, PbxProvisioningJob, PbxTenant
from telephony.services import provisioning
from tests.telephony_fixtures import *  # noqa: F401,F403

PBX_REPO = Path(__file__).resolve().parents[2] / "CRM-pbx"
PBX_PY = PBX_REPO / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
CONTAINER = "loop-pbx-dev-asterisk-1"

pytestmark = [
    pytest.mark.skipif(not os.environ.get("LOOP_PBX_E2E"), reason="set LOOP_PBX_E2E=1 (needs Docker + CRM-pbx)"),
    # These tests share one dev Asterisk, its runtime config dir and fixed SIP
    # ports; two at once corrupt each other (and can stall the run). Serial only.
    pytest.mark.skipif(
        bool(os.environ.get("LOOP_PBX_E2E") and os.environ.get("PYTEST_XDIST_WORKER")),
        reason="PBX end-to-end tests must run serially: add -n 0",
    ),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture(autouse=True)
def _no_pbx_env_file(monkeypatch):
    """The applier and worker inherit this: a production-shaped CRM-pbx/.env must not leak in."""
    monkeypatch.setenv("LOOP_ENV_FILE", "none")


def _run(args, env=None, timeout=120):
    return subprocess.run(args, cwd=PBX_REPO, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout)


def test_compiled_config_goes_live_on_asterisk(
    live_server, tmp_path, company, other_company, make_trunk, make_number, make_extension
):
    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    from telephony.services.provisioning import register_node

    node, node_token, _ = register_node(name="dev-node")

    # Tenant A = the dev sample's tdev4test; tenant B = tacme2pbx (same ext 100, no trunk).
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    PbxTenant.objects.create(company=other_company, realm_key="acme2pbx", home_node=node)
    ext100 = make_extension(company, "100", secret="dev-only-100-secret", display_name="Agent 100", device_type=DeviceType.WEBRTC)
    make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101", device_type=DeviceType.DESK_PHONE)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"))
    did = make_number(company, trunk, "+9647700000001")
    PbxInboundRoute.objects.create(company=company, number=did, extension=ext100)
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}], trunk=trunk, caller_id=did)
    make_extension(other_company, "100", secret="dev-only-acme-100-secret", display_name="Acme 100", device_type=DeviceType.DESK_PHONE)

    for c in (company, other_company):
        provisioning.request_job(provisioning.ensure_tenant(c), PbxProvisioningJob.Kind.PROVISION)

    # Fresh node runtime (the dev compose mounts runtime/loop/active into Asterisk).
    active = PBX_REPO / "deploy" / "dev" / "runtime" / "loop" / "active"
    shutil.rmtree(active.parent / "releases", ignore_errors=True)
    active.mkdir(parents=True, exist_ok=True)
    for p in active.iterdir():
        p.unlink()

    applier = _run(
        [str(PBX_PY), "-m", "loop_pbx.applier", "--once"],
        env={
            "LOOP_CRM_INTERNAL_URL": live_server.url,
            "LOOP_NODE_ID": str(node.id),
            "LOOP_NODE_TOKEN": node_token,
            "LOOP_ASTERISK_LOOP_DIR": str(active.parent),
            "LOOP_STATE_DIR": str(tmp_path),
            "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
        },
    )
    assert applier.returncode == 0, applier.stdout + applier.stderr

    for tenant in PbxTenant.objects.all():
        assert tenant.status == PbxTenant.Status.ACTIVE, (tenant.tag, tenant.last_apply_error, applier.stderr)
        assert tenant.applied_version == tenant.config_version == 1

    smoke = _run([str(PBX_PY), "scripts/dev_smoke.py"])
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr


# ---------------------------------------------------------------------------
# Phase 2: real calls → worker → internal events API → PbxCall + lead timeline
# ---------------------------------------------------------------------------

DEVPHONE = ["docker", "compose", "-f", str(PBX_REPO / "deploy" / "dev" / "docker-compose.yml"), "--profile", "tools",
            "run", "--rm", "-T", "devphone", "python", "-u", "/tools/sip_phone.py"]


def _container_ip() -> str:
    return subprocess.check_output(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", CONTAINER], text=True
    ).strip()


def _fresh_runtime() -> Path:
    active = PBX_REPO / "deploy" / "dev" / "runtime" / "loop" / "active"
    shutil.rmtree(active.parent / "releases", ignore_errors=True)
    active.mkdir(parents=True, exist_ok=True)
    for p in active.iterdir():
        p.unlink()
    return active


def _devphones(*arg_lists, timeout=60):
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    procs = [subprocess.Popen(DEVPHONE + args, cwd=PBX_REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
             for args in arg_lists]
    return [p.communicate(timeout=timeout)[0].decode(errors="replace") for p in procs]


def _wait_for(predicate, timeout=30.0):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def test_real_calls_reach_the_crm(live_server, tmp_path, company, employee_user, make_trunk, make_number, make_extension):
    """Inbound + outbound calls on the dev Asterisk end up as PbxCalls and lead timeline entries."""
    from crm.models import Client, ClientCall
    from telephony.models import PbxCall, PbxExtension
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    desk = make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                          device_type=DeviceType.DESK_PHONE, user=employee_user)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxInboundRoute.objects.create(company=company, number=did, extension=desk)
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    lead = Client.objects.create(name="Real Lead", company=company, priority="low", type="cold",
                                 phone_number="+9647801234567", assigned_to=employee_user)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
        **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
    })
    assert applier.returncode == 0, applier.stderr
    assert PbxTenant.objects.get(company=company).status == PbxTenant.Status.ACTIVE

    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        phone = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071"]
        carrier = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080", "--from-user", "+9647801234567"]
        # Inbound: the carrier calls the DID, desk phone 101 answers and hangs up.
        _devphones(
            [*phone, "--mode", "answer", "--answer-after", "1", "--hangup-after", "2", "--lifetime", "12"],
            [*carrier, "--dial", "+9647700000001", "--dial-after", "3", "--lifetime", "10"],
        )
        # Outbound: desk phone 101 calls the lead; the carrier answers.
        _devphones(
            [*phone, "--dial", "+9647801234567", "--dial-after", "3", "--hangup-after", "2", "--lifetime", "12"],
            [*carrier, "--mode", "answer", "--answer-after", "1", "--lifetime", "12"],
        )
        assert _wait_for(lambda: PbxCall.objects.filter(status="answered").count() == 2), list(
            PbxCall.objects.values_list("direction", "status")
        )
        assert _wait_for(lambda: ClientCall.objects.filter(source="pbx").count() == 2)
    finally:
        worker.terminate()
        log = worker.communicate(timeout=10)[0].decode(errors="replace")

    inbound = PbxCall.objects.get(direction="inbound")
    outbound = PbxCall.objects.get(direction="outbound")
    assert inbound.answered_by == desk and inbound.did == "+9647700000001" and inbound.trunk == trunk
    assert outbound.extension == desk and outbound.to_party == "+9647801234567" and outbound.trunk == trunk
    assert inbound.crm_contact == lead and outbound.crm_contact == lead
    assert {c.notes.split(" · ")[1] for c in ClientCall.objects.filter(client=lead, source="pbx")} == {"Incoming", "Outgoing"}
    assert inbound.legs.count() == 2 and outbound.legs.count() == 2
    # Presence: the desk phone registered and unregistered during the run.
    assert PbxExtension.objects.get(pk=desk.pk).registered_changed_at is not None, log


# ---------------------------------------------------------------------------
# Phase 3: click-to-call: CRM API → worker control API → ARI originate → agent → lead
# ---------------------------------------------------------------------------


def test_click_to_call_rings_the_agent_then_the_lead(
    live_server, tmp_path, company, employee_user, make_trunk, make_number, make_extension, telephony_subscription,
):
    import hashlib
    import socket
    import time

    from rest_framework.test import APIClient

    from crm.models import Client, ClientCall
    from telephony.models import PbxCall
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        control_port = s.getsockname()[1]
    node, node_token, crm_token = register_node(name="dev-node", control_url=f"http://127.0.0.1:{control_port}")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                   device_type=DeviceType.DESK_PHONE, user=employee_user)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    lead = Client.objects.create(name="C2C Lead", company=company, priority="low", type="cold",
                                 phone_number="+9647801234567", assigned_to=employee_user)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
        **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
    })
    assert applier.returncode == 0, applier.stderr
    assert PbxTenant.objects.get(company=company).status == PbxTenant.Status.ACTIVE

    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off",
             "LOOP_CONTROL_BIND": f"127.0.0.1:{control_port}", "LOOP_REDIS_URL": "redis://127.0.0.1:6380/0",
             "LOOP_CRM_TOKEN_SHA256": hashlib.sha256(crm_token.encode()).hexdigest()},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    phones = [
        subprocess.Popen(DEVPHONE + ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret",
                                     "--bind-port", "5071", "--mode", "answer", "--answer-after", "1",
                                     "--hangup-after", "3", "--lifetime", "20"],
                         cwd=PBX_REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT),
        subprocess.Popen(DEVPHONE + ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080",
                                     "--mode", "answer", "--answer-after", "1", "--lifetime", "20"],
                         cwd=PBX_REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT),
    ]
    client = APIClient()
    client.force_authenticate(employee_user)
    try:
        # The agent's desk phone must be registered before the node will ring it.
        deadline = time.time() + 15
        while True:
            resp = client.post("/api/v1/telephony/calls/", {"destination": "+964 780 123 4567",
                                                            "crm_contact_id": lead.pk}, format="json")
            if resp.status_code == 202 or time.time() > deadline:
                break
            assert resp.json().get("error", resp.json()).get("code") in ("phone_not_registered",), resp.content
            time.sleep(1)
        assert resp.status_code == 202, resp.content
        call_id = (resp.json().get("data") or resp.json())["id"]
        assert _wait_for(lambda: PbxCall.objects.get(pk=call_id).status == "answered", timeout=30), (
            PbxCall.objects.get(pk=call_id).status
        )
        assert _wait_for(lambda: ClientCall.objects.filter(pbx_call_id=call_id).exists())
    finally:
        phone_logs = [p.communicate(timeout=30)[0].decode(errors="replace") for p in phones]
        worker.terminate()
        log = worker.communicate(timeout=10)[0].decode(errors="replace")

    call = PbxCall.objects.get(pk=call_id)
    assert (call.origin, call.direction, call.linked_id) == ("crm", "outbound", call_id), log
    assert call.crm_contact == lead and call.trunk == trunk and call.to_party == "+9647801234567"
    assert call.billsec_s is not None and call.billsec_s <= call.duration_s
    assert ClientCall.objects.get(pbx_call=call).client == lead
    # The agent's phone got the call id it can resolve through the API; the lead saw the verified DID.
    assert f"X-Loop-Call-Id {call_id}" in phone_logs[0], phone_logs[0]
    assert "<sip:+9647700000001@" in phone_logs[1], phone_logs[1]


# ---------------------------------------------------------------------------
# Phase 2: recordings: MixMonitor → spool → worker → S3 → CRM → presigned playback
# ---------------------------------------------------------------------------

S3_ENDPOINT = "http://127.0.0.1:9000"  # dev compose "s3" (SeaweedFS standing in for R2)
S3_BUCKET = "loop-pbx-recordings"
S3_KEY, S3_SECRET = "loopdevaccess", "dev-only-s3-secret"
SPOOL = PBX_REPO / "deploy" / "dev" / "runtime" / "recordings" / "loop"


def _rms(wav_bytes: bytes) -> float:
    import array
    import io
    import math
    import wave

    with wave.open(io.BytesIO(wav_bytes)) as w:
        samples = array.array("h", w.readframes(w.getnframes()))
    return math.sqrt(sum(s * s for s in samples) / max(1, len(samples)))


def test_recorded_calls_reach_object_storage_and_play_back(
    live_server, tmp_path, settings, company, admin_user, employee_user, make_trunk, make_number,
    make_extension, telephony_subscription, recording_allowed,
):
    import hashlib
    import urllib.request

    from rest_framework.test import APIClient

    from crm.models import Client, ClientCall
    from telephony.models import PbxCall, PbxRecording
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node,
                             recording_mode="external", recording_notice="ar_en")
    desk = make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                          device_type=DeviceType.DESK_PHONE, user=employee_user)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxInboundRoute.objects.create(company=company, number=did, extension=desk)
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    lead = Client.objects.create(name="Recorded Lead", company=company, priority="low", type="cold",
                                 phone_number="+9647801234567", assigned_to=employee_user)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    shutil.rmtree(SPOOL, ignore_errors=True)
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
        **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
    })
    assert applier.returncode == 0, applier.stderr
    s3_env = {"LOOP_RECORDING_STORE": "s3", "LOOP_RECORDING_SPOOL": str(SPOOL), "LOOP_S3_ENDPOINT": S3_ENDPOINT,
              "LOOP_S3_BUCKET": S3_BUCKET, "LOOP_S3_REGION": "auto", "LOOP_S3_ACCESS_KEY_ID": S3_KEY,
              "LOOP_S3_SECRET_ACCESS_KEY": S3_SECRET}
    bucket = _run([str(PBX_PY), "-c", "import os; from loop_pbx.worker.__main__ import recording_store; "
                   "recording_store().create_bucket()"], env=s3_env)
    assert bucket.returncode == 0, bucket.stderr

    since = subprocess.check_output(["docker", "exec", CONTAINER, "date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], text=True).strip()
    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, **s3_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        phone = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071",
                 "--rtp-tone", "440"]
        carrier = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080",
                   "--from-user", "+9647801234567", "--rtp-tone", "1000"]
        # Inbound: the caller hears the notice, then desk 101 answers and talks for 3 s.
        _devphones(
            [*phone, "--mode", "answer", "--answer-after", "1", "--hangup-after", "3", "--lifetime", "14"],
            [*carrier, "--dial", "+9647700000001", "--dial-after", "3", "--lifetime", "12"],
        )
        # Outbound: the lead answers, hears the notice (Dial U()), then the call is bridged.
        _devphones(
            [*phone, "--dial", "+9647801234567", "--dial-after", "3", "--hangup-after", "4", "--lifetime", "14"],
            [*carrier, "--mode", "answer", "--answer-after", "1", "--lifetime", "14"],
        )
        assert _wait_for(lambda: PbxRecording.objects.count() == 2, timeout=40), list(
            PbxCall.objects.values_list("direction", "status", "recorded")
        )
    finally:
        worker.terminate()
        log = worker.communicate(timeout=10)[0].decode(errors="replace")

    calls = {c.direction: c for c in PbxCall.objects.all()}
    assert set(calls) == {"inbound", "outbound"} and all(c.status == "answered" and c.recorded for c in calls.values()), log
    for rec in PbxRecording.objects.select_related("call"):
        assert rec.call is not None and rec.company_id == company.id and rec.format == "wav"
        assert rec.storage_key.startswith(f"telephony/{company.id}/") and str(rec.call_id) in rec.storage_key
        assert 2 <= rec.duration_s <= 5, rec.duration_s  # bridged audio only: no ringing, no notice
    assert set(ClientCall.objects.filter(client=lead, source="pbx").values_list("recording_status", flat=True)) == {"ready"}
    # The spool copy is gone once the CRM accepted the report.
    assert not [p for p in SPOOL.rglob("*") if p.is_file()], list(SPOOL.rglob("*"))

    # The notice played to the outside party on both calls.
    import re

    asterisk_log = re.sub(r"\x1b\[[0-9;]*m", "", subprocess.check_output(
        ["docker", "logs", "--since", since, CONTAINER], text=True, stderr=subprocess.STDOUT, errors="replace"))
    assert asterisk_log.count("Playing 'loop/recording-notice-ar") >= 2, asterisk_log[-3000:]
    assert asterisk_log.count("Playing 'loop/recording-notice-en") >= 2
    assert "Playback failed" not in asterisk_log

    # Playback: the CRM presigns a GET against the same bucket; the audio is the uploaded file and not silence.
    # The CRM's recordings storage on the same bucket, configured as for R2 (region auto, path-style).
    from object_storage.config import R2Settings, build_storages

    settings.STORAGES = build_storages(media="local", recordings="r2", r2=R2Settings(
        endpoint_url=S3_ENDPOINT, access_key_id=S3_KEY, secret_access_key=S3_SECRET, recordings_bucket=S3_BUCKET,
    ))
    client = APIClient()
    client.force_authenticate(admin_user)
    for rec in PbxRecording.objects.all():
        resp = client.post(f"/api/v1/telephony/recordings/{rec.id}/access/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        url = (body.get("data") or body)["url"]
        audio = urllib.request.urlopen(url, timeout=10).read()
        assert len(audio) == rec.size_bytes and hashlib.sha256(audio).hexdigest() == rec.sha256
        assert _rms(audio) > 500, "recording is silent"


# ---------------------------------------------------------------------------
# Phase 3: agent status (worker device state + compiled check) and the apply-now nudge
# ---------------------------------------------------------------------------


def _ari_device_state(name: str) -> str:
    import base64
    import json
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:8088/ari/deviceStates/{name}", headers={
        "Authorization": "Basic " + base64.b64encode(b"loop-worker:dev-only-ari-secret").decode()})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())["state"]


def test_paused_agent_is_not_rung_and_compiles_land_on_a_nudge(
    live_server, tmp_path, settings, company, employee_user, admin_user, make_trunk, make_number, make_extension,
    telephony_subscription,
):
    import hashlib
    import socket
    import time

    from rest_framework.test import APIClient

    from telephony.models import PbxCall
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    settings.TELEPHONY_APPLY_NUDGE = True
    ip = _container_ip()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        control_port = s.getsockname()[1]
    node, node_token, crm_token = register_node(name="dev-node", control_url=f"http://127.0.0.1:{control_port}")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    desk = make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                          device_type=DeviceType.DESK_PHONE, user=employee_user)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxInboundRoute.objects.create(company=company, number=did, extension=desk)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    # A long-running applier that polls only every 60 s: anything faster is the nudge.
    applier = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.applier"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_POLL_SECONDS": "60",
             "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off",
             "LOOP_CONTROL_BIND": f"127.0.0.1:{control_port}", "LOOP_REDIS_URL": "redis://127.0.0.1:6380/0",
             "LOOP_CRM_TOKEN_SHA256": hashlib.sha256(crm_token.encode()).hexdigest()},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    agent = APIClient()
    agent.force_authenticate(employee_user)
    admin = APIClient()
    admin.force_authenticate(admin_user)
    device = "Stasis:tdev4test_101-agent"
    try:
        assert _wait_for(lambda: PbxTenant.objects.get(company=company).status == PbxTenant.Status.ACTIVE, 30)
        assert _wait_for(lambda: agent.get("/api/v1/telephony/me/").status_code == 200, 10)

        # Pause: the node's device state flips, and a call to the DID is answered busy unrung.
        resp = agent.put("/api/v1/telephony/agents/me/status/", {"status": "paused"}, format="json")
        assert resp.status_code == 200, resp.content
        assert _ari_device_state(device) == "BUSY"
        phone = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071"]
        carrier = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080",
                   "--from-user", "+9647801234567"]
        phone_log, carrier_log = _devphones(
            [*phone, "--mode", "answer", "--lifetime", "9"],
            [*carrier, "--dial", "+9647700000001", "--dial-after", "3", "--lifetime", "7"],
        )
        assert "call failed 486" in carrier_log, carrier_log
        assert "INVITE <-" not in phone_log, phone_log
        assert _wait_for(lambda: PbxCall.objects.filter(direction="inbound").exists(), 15)
        assert _wait_for(lambda: PbxCall.objects.get(direction="inbound").status == "busy", 15), (
            PbxCall.objects.values_list("status", flat=True))

        # Available again: the next call rings and is answered.
        assert agent.put("/api/v1/telephony/agents/me/status/", {"status": "available"}, format="json").status_code == 200
        assert _ari_device_state(device) == "NOT_INUSE"
        phone_log, _ = _devphones(
            [*phone, "--mode", "answer", "--answer-after", "1", "--hangup-after", "1", "--lifetime", "10"],
            [*carrier, "--dial", "+9647700000001", "--dial-after", "3", "--lifetime", "8"],
        )
        assert "200 OK ->" in phone_log, phone_log
        assert _wait_for(lambda: PbxCall.objects.filter(status="answered").count() == 1, 15)

        # A config change reaches the node on the nudge, well inside the 60 s poll.
        tenant = PbxTenant.objects.get(company=company)
        started = time.monotonic()
        resp = admin.post("/api/v1/telephony/extensions/", {"number": "150", "device_type": "desk_phone"}, format="json")
        assert resp.status_code == 201, resp.content
        tenant.refresh_from_db()
        target = tenant.config_version
        assert target > tenant.applied_version
        assert _wait_for(lambda: PbxTenant.objects.get(pk=tenant.pk).applied_version == target, 20)
        took = time.monotonic() - started
        assert took < 15, took
    finally:
        for proc in (worker, applier):
            proc.terminate()
        for proc in (worker, applier):
            proc.communicate(timeout=10)
        try:
            # Never leave the shared dev Asterisk with a paused agent.
            import base64
            import urllib.request

            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:8088/ari/deviceStates/{device}?deviceState=NOT_INUSE", method="PUT",
                headers={"Authorization": "Basic " + base64.b64encode(b"loop-worker:dev-only-ari-secret").decode()}),
                timeout=5)
        except Exception:
            pass
    print(f"nudged compile applied in {took:.1f}s")


# ---------------------------------------------------------------------------
# Phase 4: business hours -> IVR -> queue, configured through the API
# ---------------------------------------------------------------------------


def test_ivr_queue_and_business_hours_live(
    live_server, tmp_path, company, employee_user, admin_user, make_trunk, make_number, make_extension,
    telephony_subscription,
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from rest_framework.test import APIClient

    from telephony.models import PbxCall
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)
    desk = make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                          device_type=DeviceType.DESK_PHONE, user=employee_user)
    offline = make_extension(company, "100", secret="dev-only-100-secret", display_name="Agent 100",
                             device_type=DeviceType.DESK_PHONE)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")

    admin = APIClient()
    admin.force_authenticate(admin_user)

    def api(method, path, body=None):
        resp = getattr(admin, method)(f"/api/v1/telephony/{path}", body, format="json")
        assert resp.status_code in (200, 201), resp.content
        return resp.json().get("data", resp.json())

    hours = api("post", "business-hours/", {"name": "Always", "timezone": "Asia/Baghdad",
                                            "rules": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}]})
    queue = api("post", "queues/", {"name": "Sales", "member_timeout_s": 15, "max_wait_s": 30,
                                    "members": [{"extension": str(offline.pk)}, {"extension": str(desk.pk)}]})
    menu = api("post", "ivr-menus/", {"name": "Main", "prompt": "loop/ivr-welcome-en", "timeout_s": 4,
                                      "timeout_type": "queue", "timeout_queue": queue["id"],
                                      "options": [{"digit": "1", "destination_type": "queue", "queue": queue["id"]}]})
    route = api("post", "inbound-routes/", {"number": str(did.pk), "destination_type": "ivr", "ivr": menu["id"],
                                            "business_hours": hours["id"], "closed_type": "extension",
                                            "closed_extension": str(offline.pk)})

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier_env = {**node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent),
                   "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx"}

    def apply():
        result = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env=applier_env)
        assert result.returncode == 0, result.stderr
        tenant = PbxTenant.objects.get(company=company)
        assert tenant.applied_version == tenant.config_version, tenant.last_apply_error

    apply()
    queues = subprocess.check_output(["docker", "exec", CONTAINER, "asterisk", "-rx", "queue show tdev4test_q_"
                                      + queue["id"][:8]], text=True)
    assert "Local/101@tdev4test-agent" in queues and "Local/100@tdev4test-agent" in queues, queues

    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    phone = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071"]
    caller = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080",
              "--from-user", "+9647801234567", "--dial", "+9647700000001", "--dial-after", "3"]
    try:
        # Open: the IVR answers, key 1 sends the caller to the queue, desk 101 answers (100 is offline).
        phone_log, caller_log = _devphones(
            [*phone, "--mode", "answer", "--answer-after", "1", "--hangup-after", "2", "--lifetime", "22"],
            [*caller, "--dtmf", "1", "--dtmf-after", "2", "--rtp-tone", "1000", "--lifetime", "20"],
        )
        assert "DTMF -> 1" in caller_log and "200 OK ->" in phone_log, (caller_log, phone_log)
        assert _wait_for(lambda: PbxCall.objects.filter(status="answered").exists(), 20)
        answered = PbxCall.objects.get(status="answered")
        assert answered.direction == "inbound" and answered.answered_by == desk
        assert {leg.endpoint for leg in answered.legs.all()} == {"tdev4test_trk_1a2b3c4d", "tdev4test_101"}

        # Closed today (in the tenant's timezone): the closed destination, offline ext 100, fails at once.
        today = datetime.now(ZoneInfo("Asia/Baghdad")).date().isoformat()
        api("patch", f"business-hours/{hours['id']}/", {"closed_dates": [today]})
        apply()
        phone_log, caller_log = _devphones(
            [*phone, "--mode", "answer", "--lifetime", "10"],
            [*caller, "--lifetime", "8"],
        )
        assert "INVITE <-" not in phone_log, phone_log  # the queue never rang desk 101
        assert "answered" not in caller_log, caller_log  # the IVR would have answered
        assert _wait_for(lambda: PbxCall.objects.count() == 2, 15)
        closed = PbxCall.objects.exclude(pk=answered.pk).get()
        assert _wait_for(lambda: PbxCall.objects.get(pk=closed.pk).is_terminal, 15)
        closed.refresh_from_db()
        # Dialling an extension with no registered device creates no channel, so the only leg is the caller's.
        assert closed.status == "failed", (closed.status, list(closed.legs.values_list("endpoint", "disposition")))
    finally:
        worker.terminate()
        worker.communicate(timeout=10)
    assert route["destination_type"] == "ivr"


# ---------------------------------------------------------------------------
# Phase 4: GSM gateway enrollment -> node peer -> qualify -> verify -> loss
# ---------------------------------------------------------------------------

FAKE_WG = r"""
import json, sys
log = sys.argv[1]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[2:]) + "\n")
"""


def test_gateway_enrollment_and_reachability_live(
    live_server, tmp_path, company, admin_user, make_extension, telephony_subscription,
):
    import base64
    import json
    import time

    from rest_framework.test import APIClient

    from telephony.models import PbxNode, PbxTrunk
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxNode.objects.filter(pk=node.pk).update(gateway_wg_public_key=base64.b64encode(b"n" * 32).decode(),
                                              gateway_wg_endpoint="pbx-dev.example.com:51821")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    make_extension(company, "101", secret="dev-only-101-secret", device_type=DeviceType.DESK_PHONE)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    admin = APIClient()
    admin.force_authenticate(admin_user)
    resp = admin.post("/api/v1/telephony/gateways/", {"name": "Dinstar"}, format="json")
    assert resp.status_code == 201, resp.content
    gw = resp.json().get("data", resp.json())
    gateway_key = base64.b64encode(os.urandom(32)).decode()
    resp = APIClient().post("/api/v1/public/telephony/gateways/enroll/",
                            {"token": gw["enrollment_token"], "public_key": gateway_key}, format="json")
    assert resp.status_code == 200, resp.content

    # The real applier adds the peer on wg-gw (a recording stand-in for the wg binary in dev).
    wg_script, wg_log = tmp_path / "fake_wg.py", tmp_path / "wg.log"
    wg_script.write_text(FAKE_WG, encoding="utf-8")
    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier_env = {**node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent),
                   "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
                   "LOOP_WG_CMD": f"{PBX_PY.as_posix()} {wg_script.as_posix()} {wg_log.as_posix()}"}
    result = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env=applier_env)
    assert result.returncode == 0, result.stderr
    assert wg_log.exists(), result.stdout + result.stderr
    calls = [json.loads(line) for line in wg_log.read_text(encoding="utf-8").splitlines()]
    assert ["set", "wg-gw", "peer", gateway_key, "allowed-ips", "10.78.0.2/32"] in calls, calls

    # In dev the "gateway" is the devphone carrier: point its trunk at it, as the tunnel would.
    PbxTrunk.objects.filter(pk=gw["trunk"]).update(host=ip, port=5080, allowed_sources=[f"{ip}/32"])
    provisioning.request_recompile_for_company(company)
    result = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env=applier_env)
    assert result.returncode == 0, result.stderr
    tenant = PbxTenant.objects.get(company=company)
    assert tenant.applied_version == tenant.config_version, tenant.last_apply_error

    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    gateway = subprocess.Popen(DEVPHONE + ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080",
                                           "--lifetime", "40"], cwd=PBX_REPO, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # Qualified every 30 s: the node reports the gateway reachable, and verify then passes.
        assert _wait_for(lambda: PbxTrunk.objects.get(pk=gw["trunk"]).health == "ready", 45)
        resp = admin.post(f"/api/v1/telephony/gateways/{gw['id']}/verify/")
        assert resp.status_code == 200, resp.content
        assert PbxTrunk.objects.get(pk=gw["trunk"]).status == PbxTrunk.Status.READY
        # The gateway's link goes away: the node reports it down.
        gateway.communicate(timeout=60)
        started = time.monotonic()
        assert _wait_for(lambda: PbxTrunk.objects.get(pk=gw["trunk"]).health == "down", 50), (
            PbxTrunk.objects.get(pk=gw["trunk"]).health)
        print(f"gateway loss reported after {time.monotonic() - started:.0f}s")
        # Spec §12: the loss alerts the company's admins (push + inbox, telephony/services/alerts.py).
        from notifications.models import Notification

        assert _wait_for(lambda: Notification.objects.filter(user=admin_user, type="pbx_trunk_down").exists(), 15)
    finally:
        if gateway.poll() is None:
            gateway.kill()
        worker.terminate()
        worker.communicate(timeout=10)


# ---------------------------------------------------------------------------
# Phase 4: a custom recorded greeting, shipped in the artifact, heard by the caller
# ---------------------------------------------------------------------------


def _tone_wav(hz: float, seconds: float) -> bytes:
    import io
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / 8000)))
                                 for i in range(int(seconds * 8000))))
    return buf.getvalue()


def test_custom_greeting_is_heard_by_the_caller(
    live_server, tmp_path, company, admin_user, make_trunk, make_number, make_extension, telephony_subscription,
):
    import io
    import re

    from rest_framework.test import APIClient

    from telephony import contract
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)
    make_extension(company, "101", secret="dev-only-101-secret", device_type=DeviceType.DESK_PHONE)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")

    admin = APIClient()
    admin.force_authenticate(admin_user)
    upload = io.BytesIO(_tone_wav(1500, 4.0))
    upload.name = "welcome.wav"
    resp = admin.post("/api/v1/telephony/sounds/", {"name": "Welcome", "file": upload}, format="multipart")
    assert resp.status_code == 201, resp.content
    sound = resp.json()["data"]
    resp = admin.post("/api/v1/telephony/ivr-menus/", {"name": "Main", "greeting": sound["id"], "timeout_s": 2,
                                                         "max_retries": 0}, format="json")
    assert resp.status_code == 201, resp.content
    menu = resp.json()["data"]
    resp = admin.post("/api/v1/telephony/inbound-routes/", {"number": str(did.pk), "destination_type": "ivr",
                                                            "ivr": menu["id"]}, format="json")
    assert resp.status_code == 201, resp.content

    active = _fresh_runtime()
    applier_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                   "LOOP_STATE_DIR": str(tmp_path), "LOOP_ASTERISK_LOOP_DIR": str(active.parent),
                   "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx"}

    def apply():
        result = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env=applier_env)
        assert result.returncode == 0, result.stderr
        tenant = PbxTenant.objects.get(company=company)
        assert tenant.applied_version == tenant.config_version, tenant.last_apply_error

    apply()
    # The node holds the exact bytes the CRM stored (verified against the manifest's sha256).
    shipped = active / contract.sound_filename("dev4test", sound["id"])
    from telephony.models import PbxSound

    assert shipped.read_bytes() == bytes(PbxSound.objects.get(pk=sound["id"]).audio)

    caller = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080",
              "--from-user", "+9647801234567", "--dial", "+9647700000001", "--dial-after", "1",
              "--listen-tone", "1500", "--lifetime", "8"]

    def heard(min_seconds: float) -> float:
        [log] = _devphones(caller)
        m = re.search(r"RX audio: ([0-9.]+) s, 1500 Hz share ([0-9.]+)", log)
        assert m and float(m.group(1)) >= min_seconds, log
        return float(m.group(2))

    custom = heard(3.5)  # the 4 s greeting
    assert custom > 0.6, custom

    # Back to the platform prompt (a 1000 Hz placeholder in dev): the 1500 Hz greeting is gone.
    assert admin.patch(f"/api/v1/telephony/ivr-menus/{menu['id']}/", {"greeting": None,
                       "prompt": "loop/ivr-welcome-en"}, format="json").status_code == 200
    apply()
    assert not shipped.exists()
    platform = heard(0.5)  # the 0.8 s dev placeholder
    assert platform < 0.2, platform
    print(f"1500 Hz share: custom greeting {custom:.2f}, platform prompt {platform:.2f}")


# ---------------------------------------------------------------------------
# Phase 4: trunk test (spec section 6): SIP OPTIONS from the node to the live contact
# ---------------------------------------------------------------------------


def test_trunk_test_probes_the_configured_contact_live(
    live_server, tmp_path, company, admin_user, make_trunk, make_number, make_extension, telephony_subscription,
):
    """
    The worker reads the trunk's contact from Asterisk (ARI dynamic config) and pings
    it. In dev the worker runs on the host, which cannot route to the container
    network, so the trunk points at a devphone answering on the host's loopback.
    """
    import hashlib
    import socket
    import time

    from django.core.cache import cache
    from rest_framework.test import APIClient

    from telephony.models import PbxTrunk
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ports = []
    for _ in range(2):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            ports.append(s.getsockname()[1])
    control_port, sip_port = ports
    node, node_token, crm_token = register_node(name="dev-node", control_url=f"http://127.0.0.1:{control_port}")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    make_extension(company, "101", secret="dev-only-101-secret", device_type=DeviceType.DESK_PHONE)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host="127.0.0.1", port=sip_port,
                       allowed_sources=["127.0.0.1/32"])
    make_number(company, trunk, "+9647700000001")
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
        **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
    })
    assert applier.returncode == 0, applier.stderr
    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off",
             "LOOP_CONTROL_BIND": f"127.0.0.1:{control_port}", "LOOP_REDIS_URL": "redis://127.0.0.1:6380/0",
             "LOOP_CRM_TOKEN_SHA256": hashlib.sha256(crm_token.encode()).hexdigest()},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    carrier = subprocess.Popen(
        [str(PBX_PY), "devtools/sip_phone.py", "carrier", "--bind-ip", "127.0.0.1", "--bind-port", str(sip_port),
         "--lifetime", "20"], cwd=PBX_REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    admin = APIClient()
    admin.force_authenticate(admin_user)

    def test_trunk():
        cache.clear()  # the per-trunk throttle
        resp = admin.post(f"/api/v1/telephony/trunks/{trunk.pk}/test/")
        assert resp.status_code == 200, resp.content
        return {c["id"]: c for c in resp.json()["data"]["checks"]}

    try:
        assert _wait_for(lambda: _port_open(control_port), 15), "worker control API did not start"
        checks = test_trunk()
        options = checks["options"]
        assert options["status"] == "ok", options
        assert options["data"]["contact"] == f"sip:127.0.0.1:{sip_port}" and options["data"]["status_code"] == 200
        assert PbxTrunk.objects.get(pk=trunk.pk).health == "ready"
        print(f"trunk probe answered in {options['data']['rtt_ms']} ms")

        carrier.kill()
        carrier.communicate(timeout=10)
        started = time.monotonic()
        checks = test_trunk()
        assert checks["options"]["status"] == "failed" and checks["options"]["data"]["reason"] == "no_answer", checks
        assert time.monotonic() - started < 8
        assert PbxTrunk.objects.get(pk=trunk.pk).health == "down"
    finally:
        if carrier.poll() is None:
            carrier.kill()
        worker.terminate()
        worker.communicate(timeout=10)


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
# Phase 5: the node's fraud detector blocks a tenant; a platform resume lifts it
# ---------------------------------------------------------------------------


def _redis_clear(tag: str) -> None:
    import redis

    r = redis.Redis(host="127.0.0.1", port=6380)
    for key in r.scan_iter(f"loop:*{tag}*"):
        r.delete(key)


def _ari_set(device: str, state: str) -> None:
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:8088/ari/deviceStates/{device}?deviceState={state}", method="PUT",
                                 headers={"Authorization": "Basic bG9vcC13b3JrZXI6ZGV2LW9ubHktYXJpLXNlY3JldA=="})
    urllib.request.urlopen(req, timeout=5).read()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_fraud_detector_blocks_and_resume_lifts_it_live(
    live_server, tmp_path, company, superadmin_client, make_trunk, make_number, make_extension, telephony_subscription,
):
    import hashlib

    from telephony.models import PbxFraudAlert
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    control_port = _free_port()
    node, node_token, crm_token = register_node(name="dev-node", control_url=f"http://127.0.0.1:{control_port}")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node,
                             fraud_limits={"max_calls_per_hour": 1, "action": "block"})
    make_extension(company, "101", secret="dev-only-101-secret", device_type=DeviceType.DESK_PHONE)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)
    _redis_clear("tdev4test")
    _ari_set("Stasis:tdev4test-outbound", "NOT_INUSE")

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier_env = {**node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent),
                   "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx"}

    def apply():
        result = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env=applier_env)
        assert result.returncode == 0, result.stderr
        tenant = PbxTenant.objects.get(company=company)
        assert tenant.applied_version == tenant.config_version, tenant.last_apply_error

    apply()
    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off",
             "LOOP_CONTROL_BIND": f"127.0.0.1:{control_port}", "LOOP_REDIS_URL": "redis://127.0.0.1:6380/0",
             "LOOP_CRM_TOKEN_SHA256": hashlib.sha256(crm_token.encode()).hexdigest()},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    desk = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071",
            "--dial", "+9647801234567", "--dial-after", "2"]
    lead = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080", "--mode", "answer",
            "--answer-after", "1"]
    try:
        assert _wait_for(lambda: _port_open(control_port), 15), "worker control API did not start"
        # 1st outbound call of the hour: within the limit (1 per hour).
        desk_log, _ = _devphones([*desk, "--hangup-after", "2", "--lifetime", "9"], [*lead, "--lifetime", "9"])
        assert "answered" in desk_log, desk_log
        # 2nd: over the limit. The node blocks the tenant and cuts the call: the detector
        # runs on call.created, so it usually ends before the trunk is even dialled.
        desk_log, _ = _devphones([*desk, "--hangup-after", "15", "--lifetime", "12"], [*lead, "--lifetime", "12"])
        assert "BYE <-" in desk_log or "call failed" in desk_log, desk_log  # long before the phone's own 15 s
        assert _ari_device_state("Stasis:tdev4test-outbound") == "BUSY"
        assert _wait_for(lambda: PbxFraudAlert.objects.filter(rule="calls_per_hour").exists(), 15)
        tenant = PbxTenant.objects.get(company=company)
        assert tenant.outbound_suspended and tenant.outbound_blocked_on_node, tenant.suspended_reason
        # 3rd: refused by the dialplan's block check, even before the suspend is applied.
        desk_log, _ = _devphones([*desk, "--lifetime", "8"], [*lead, "--lifetime", "8"])
        assert "call failed" in desk_log and "answered" not in desk_log, desk_log

        # The operator resumes: the node lifts its block first, then the config is recompiled.
        resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/resume/")
        assert resp.status_code == 202, resp.content
        assert _ari_device_state("Stasis:tdev4test-outbound") == "NOT_INUSE"
        apply()
        desk_log, _ = _devphones([*desk, "--hangup-after", "2", "--lifetime", "9"], [*lead, "--lifetime", "9"])
        assert "answered" in desk_log, desk_log  # one alert per rule per hour: no second block
    finally:
        worker.terminate()
        worker.communicate(timeout=10)
        _ari_set("Stasis:tdev4test-outbound", "NOT_INUSE")
        _redis_clear("tdev4test")


def test_emergency_number_bypasses_a_suspension_live(
    live_server, tmp_path, settings, company, admin_user, make_trunk, make_number, make_extension, telephony_subscription,
):
    """
    Acceptance #10: a tenant whose outbound calling is suspended (billing; no outbound
    routes compiled) and blocked on the node (the outbound switch BUSY) still reaches
    emergency services: desk 101 dials 112, the carrier gets it from the verified DID,
    while an ordinary mobile number is refused. The CRM records it as outbound and
    alerts the admins.
    """
    from notifications.models import Notification
    from telephony.models import PbxCall
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    settings.TELEPHONY_EMERGENCY_NUMBERS = ["104", "115", "122", "112", "911"]
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node, outbound_suspended=True,
                             suspended_for_billing=True, suspended_reason="Subscription ended")
    make_extension(company, "101", secret="dev-only-101-secret", device_type=DeviceType.DESK_PHONE, user=admin_user)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
        **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
    })
    assert applier.returncode == 0, applier.stderr
    assert PbxTenant.objects.get(company=company).status == PbxTenant.Status.SUSPENDED
    _ari_set("Stasis:tdev4test-outbound", "BUSY")  # the node's immediate block, as after a fraud alert
    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    desk = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071",
            "--dial-after", "2"]
    carrier = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080", "--mode", "answer",
               "--answer-after", "1"]
    try:
        # An ordinary number: no route while suspended (default reject).
        desk_log, carrier_log = _devphones([*desk, "--dial", "+9647801234567", "--lifetime", "8"],
                                           [*carrier, "--lifetime", "8"])
        assert "call failed" in desk_log and "INVITE <-" not in carrier_log, desk_log
        # The emergency number: out the tenant's trunk with the verified DID, answered.
        desk_log, carrier_log = _devphones([*desk, "--dial", "112", "--hangup-after", "3", "--lifetime", "12"],
                                           [*carrier, "--lifetime", "12"])
        assert "answered" in desk_log, desk_log
        assert "INVITE <-" in carrier_log and "+9647700000001" in carrier_log, carrier_log
        assert _wait_for(lambda: PbxCall.objects.filter(to_party="112", status="answered").exists(), 20), list(
            PbxCall.objects.values_list("direction", "to_party", "status"))
    finally:
        worker.terminate()
        worker.communicate(timeout=10)
        _ari_set("Stasis:tdev4test-outbound", "NOT_INUSE")

    call = PbxCall.objects.get(to_party="112")
    assert call.direction == "outbound" and call.emergency_notified_at is not None
    assert Notification.objects.filter(user=admin_user, type="pbx_emergency_call").exists()


# ---------------------------------------------------------------------------
# Phase 5: migrate-node: the old node drains, the new one applies, state re-pushed
# ---------------------------------------------------------------------------


# Like FAKE_WG, but remembers the peers per state file, so "wg show" lists them and a
# node that loses a gateway removes it (one file per node).
FAKE_WG_STATEFUL = r"""
import json, sys
from pathlib import Path
log = Path(sys.argv[1])
state = log.with_suffix(".peers.json")
peers = json.loads(state.read_text()) if state.exists() else {}
args = sys.argv[2:]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:1] == ["show"]:
    for key, ip in peers.items():
        print(key + "\t" + ip)
elif args[:1] == ["set"] and args[-1] == "remove":
    peers.pop(args[3], None)
elif args[:1] == ["set"]:
    peers[args[3]] = args[5]
state.write_text(json.dumps(peers))
"""


def test_migrate_node_live(live_server, tmp_path, company, superadmin_client, make_extension, telephony_subscription):
    """
    Two node registrations take turns on the one dev Asterisk: the old node's
    applier removes the tenant, then the new node's applies it. The new node's
    worker gets the paused agent re-pushed (a real node's astdb starts empty).
    """
    import base64
    import hashlib
    import json

    from telephony.models import PbxExtension, PbxGatewayPeer, PbxNode, PbxTrunk
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    old, old_token, _ = register_node(name="pbx-old")
    control_port = _free_port()
    new, new_token, new_crm_token = register_node(name="pbx-new", control_url=f"http://127.0.0.1:{control_port}")
    PbxNode.objects.filter(pk__in=[old.pk, new.pk]).update(
        gateway_wg_public_key=base64.b64encode(b"n" * 32).decode(), gateway_wg_endpoint="pbx.example.com:51821")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=old)
    ext = make_extension(company, "100", secret="dev-only-100-secret", device_type=DeviceType.DESK_PHONE)
    PbxExtension.objects.filter(pk=ext.pk).update(agent_status="paused")
    gw_trunk = PbxTrunk.objects.create(company=company, name="GW", kind="gsm_gateway", host="10.78.0.2",
                                       allowed_sources=["10.78.0.2/32"], status=PbxTrunk.Status.READY)
    gw_key = base64.b64encode(b"g" * 32).decode()
    PbxGatewayPeer.objects.create(company=company, trunk=gw_trunk, node=old, tunnel_ip="10.78.0.2",
                                  status=PbxGatewayPeer.Status.VERIFIED, public_key=gw_key)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)
    _ari_set("Stasis:tdev4test_100-agent", "NOT_INUSE")

    active = _fresh_runtime()
    wg_script = tmp_path / "fake_wg.py"
    wg_script.write_text(FAKE_WG_STATEFUL, encoding="utf-8")

    def applier(node, token, name):
        state = tmp_path / name
        state.mkdir(exist_ok=True)
        log = tmp_path / f"{name}-wg.log"
        result = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
            "LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": token,
            "LOOP_STATE_DIR": str(state), "LOOP_ASTERISK_LOOP_DIR": str(active.parent),
            "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
            "LOOP_WG_CMD": f"{PBX_PY.as_posix()} {wg_script.as_posix()} {log.as_posix()}"})
        assert result.returncode == 0, result.stderr
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return [c for c in calls if c[:1] == ["set"]]

    def stamp(context):
        out = subprocess.run(["docker", "exec", CONTAINER, "asterisk", "-rx", f"dialplan show loopcfg@{context}"],
                             capture_output=True, text=True).stdout
        return out

    wg_old = applier(old, old_token, "old")
    tenant = PbxTenant.objects.get(company=company)
    assert tenant.status == "active" and tenant.applied_version == tenant.config_version
    assert ["set", "wg-gw", "peer", gw_key, "allowed-ips", "10.78.0.2/32"] in wg_old
    first = tenant.config_version

    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, "LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(new.id),
             "LOOP_NODE_TOKEN": new_token, "LOOP_STATE_DIR": str(tmp_path / "new"), "LOOP_ARI_PASSWORD": "dev-only-ari-secret",
             "LOOP_RECORDING_STORE": "off", "LOOP_CONTROL_BIND": f"127.0.0.1:{control_port}",
             "LOOP_REDIS_URL": "redis://127.0.0.1:6380/0",
             "LOOP_CRM_TOKEN_SHA256": hashlib.sha256(new_crm_token.encode()).hexdigest()},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        (tmp_path / "new").mkdir(exist_ok=True)
        assert _wait_for(lambda: _port_open(control_port), 15), "worker control API did not start"
        resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/migrate-node/",
                                      {"node": str(new.pk)}, format="json")
        assert resp.status_code == 202, resp.content

        # The old node removes the tenant and says so; its gateway peer goes.
        wg_old = applier(old, old_token, "old")
        tenant = PbxTenant.objects.get(company=company)
        assert tenant.draining_node is None, tenant.last_apply_error
        assert "NoOp" not in stamp("tdev4test-internal")  # the tenant's config is gone from the node
        assert ["set", "wg-gw", "peer", gw_key, "remove"] in wg_old, wg_old

        # The new node applies the tenant and adds the gateway's peer.
        wg_new = applier(new, new_token, "new")
        tenant = PbxTenant.objects.get(company=company)
        assert tenant.status == "active" and tenant.applied_version == tenant.config_version == first + 2
        assert f"NoOp({first + 2})" in stamp("tdev4test-internal")
        assert ["set", "wg-gw", "peer", gw_key, "allowed-ips", "10.78.0.2/32"] in wg_new
        # Once the new node reported the move applied, the paused agent was re-pushed to
        # its worker (which refuses state for tenants it has not applied yet).
        assert _wait_for(lambda: _ari_device_state("Stasis:tdev4test_100-agent") == "BUSY", 10)
    finally:
        worker.terminate()
        worker.communicate(timeout=10)
        _ari_set("Stasis:tdev4test_100-agent", "NOT_INUSE")


# ---------------------------------------------------------------------------
# Spec section 12 acceptance on the real node, and the Phase 6 missed-call push
# ---------------------------------------------------------------------------


def test_acceptance_rejections_and_missed_call_live(
    live_server, tmp_path, company, employee_user, make_trunk, make_number, make_extension,
):
    """
    Spec §12: a wrong DID, an untrusted trunk source and a forged caller id are rejected by the
    compiled config on a real Asterisk; then an unanswered call becomes a missed-call push (Phase 6).
    """
    from notifications.models import Notification
    from telephony.models import PbxCall
    from telephony.services.provisioning import register_node

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    ip = _container_ip()
    node, node_token, _ = register_node(name="dev-node")
    PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=node)
    desk = make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                          device_type=DeviceType.DESK_PHONE, user=employee_user)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"), host=ip, port=5080,
                       allowed_sources=[f"{ip}/32"])
    did = make_number(company, trunk, "+9647700000001")
    PbxInboundRoute.objects.create(company=company, number=did, extension=desk)
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    provisioning.request_job(provisioning.ensure_tenant(company), PbxProvisioningJob.Kind.PROVISION)

    active = _fresh_runtime()
    node_env = {"LOOP_CRM_INTERNAL_URL": live_server.url, "LOOP_NODE_ID": str(node.id), "LOOP_NODE_TOKEN": node_token,
                "LOOP_STATE_DIR": str(tmp_path)}
    applier = _run([str(PBX_PY), "-m", "loop_pbx.applier", "--once"], env={
        **node_env, "LOOP_ASTERISK_LOOP_DIR": str(active.parent), "LOOP_ASTERISK_CMD": f"docker exec {CONTAINER} asterisk -rx",
    })
    assert applier.returncode == 0, applier.stderr

    worker = subprocess.Popen(
        [str(PBX_PY), "-m", "loop_pbx.worker"], cwd=PBX_REPO,
        env={**os.environ, **node_env, "LOOP_ARI_PASSWORD": "dev-only-ari-secret", "LOOP_RECORDING_STORE": "off"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    phone = ["phone", "--user", "tdev4test_101", "--password", "dev-only-101-secret", "--bind-port", "5071"]
    carrier = ["carrier", "--server", f"{ip}:5060", "--bind-ip", ip, "--bind-port", "5080", "--from-user", "+9647801234567"]
    try:
        # 1. A DID the tenant does not own, from its own trusted trunk: rejected, desk 101 never rings.
        desk_log, carrier_log = _devphones(
            [*phone, "--mode", "answer", "--lifetime", "10"],
            [*carrier, "--dial", "+9647799999999", "--dial-after", "3", "--lifetime", "8"],
        )
        assert "INVITE <-" not in desk_log and "answered" not in carrier_log, (desk_log, carrier_log)

        # 2. The tenant's DID from an address its trunk does not allow (the host, through Docker's
        #    port forward): no endpoint matches, nothing reaches the tenant.
        before = PbxCall.objects.count()
        desk_proc = subprocess.Popen(DEVPHONE + [*phone, "--mode", "answer", "--lifetime", "12"], cwd=PBX_REPO, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stranger = subprocess.run(
            [str(PBX_PY), "-u", "devtools/sip_phone.py", "carrier", "--server", "127.0.0.1:5060", "--bind-ip", "127.0.0.1",
             "--bind-port", "5091", "--from-user", "+9647801234567", "--dial", "+9647700000001", "--dial-after", "3",
             "--lifetime", "8"],
            cwd=PBX_REPO, capture_output=True, text=True, timeout=40,
        )
        desk_log = desk_proc.communicate(timeout=40)[0].decode(errors="replace")
        assert "INVITE <-" not in desk_log and "answered" not in stranger.stdout, (desk_log, stranger.stdout)
        import time

        time.sleep(3)  # let the worker flush anything it saw
        assert PbxCall.objects.count() == before, list(PbxCall.objects.values_list("direction", "status", "from_party"))

        # 3a. A forged From number: desk 101 is not identified at all (401), the lead never rings.
        desk_log, carrier_log = _devphones(
            [*phone, "--from-user", "+12025550123", "--dial", "+9647801234567", "--dial-after", "3", "--lifetime", "10"],
            [*carrier, "--mode", "answer", "--answer-after", "1", "--lifetime", "10"],
        )
        assert "call failed 401" in desk_log and "INVITE <-" not in carrier_log, (desk_log, carrier_log)
        # 3b. A forged display name on its own endpoint: the call goes out, but the lead sees only the
        #     verified DID, as name and number (the dialplan sets CALLERID(all)).
        desk_log, carrier_log = _devphones(
            [*phone, "--from-name", "+12025550123", "--dial", "+9647801234567", "--dial-after", "3",
             "--hangup-after", "2", "--lifetime", "12"],
            [*carrier, "--mode", "answer", "--answer-after", "1", "--lifetime", "12"],
        )
        received = [line for line in carrier_log.splitlines() if "INVITE <-" in line]
        assert received and '"+9647700000001" <sip:+9647700000001@' in received[0], carrier_log
        assert "+12025550123" not in carrier_log

        # 4. An inbound call nobody answers: the caller gives up while desk 101 rings (Phase 6 push).
        desk_log, carrier_log = _devphones(
            [*phone, "--mode", "ignore", "--lifetime", "16"],
            [*carrier, "--dial", "+9647700000001", "--dial-after", "3", "--cancel-after", "5", "--lifetime", "14"],
        )
        assert "INVITE <-" in desk_log, desk_log
        missed = {"missed", "no_answer", "abandoned"}
        assert _wait_for(lambda: PbxCall.objects.filter(direction="inbound", status__in=missed).exists()), list(
            PbxCall.objects.values_list("direction", "status"))
        call = PbxCall.objects.get(direction="inbound", status__in=missed)
        assert _wait_for(lambda: Notification.objects.filter(user=employee_user, type="pbx_call_missed").exists())
        note = Notification.objects.get(user=employee_user, type="pbx_call_missed")
        assert note.data["call_id"] == str(call.pk) and note.data["phone"] == "+9647801234567"
    finally:
        worker.terminate()
        worker.communicate(timeout=10)

    outbound = PbxCall.objects.get(direction="outbound")
    assert outbound.extension == desk and outbound.status == "answered"
