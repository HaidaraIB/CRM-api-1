"""
Click-to-call and the browser softphone's endpoints (Phase 3; CRM-pbx contract
sections 4 and 6).

The node's worker is a real HTTP server on 127.0.0.1 that records what the CRM
sent and answers like ``loop_pbx/worker/control.py``; the live originate through
Asterisk is in tests/test_telephony_e2e.py.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import pytest

from conftest import api_body
from telephony.models import DeviceType, PbxCall, PbxOutboundRoute, PbxTenant
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"
LEAD_PHONE = "+9647801234567"


@pytest.fixture
def setup(company, tenant_a, worker, employee_user, make_trunk, make_number, make_extension):
    """Live tenant; employee owns WebRTC ext 100; a verified DID and an Iraq-mobile route."""
    from crm.models import Client

    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1)
    ext = make_extension(company, "100", device_type=DeviceType.WEBRTC, user=employee_user)
    trunk = make_trunk(company)
    did = make_number(company, trunk, "+9647700000001")
    PbxOutboundRoute.objects.create(company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}],
                                    trunk=trunk, caller_id=did)
    lead = Client.objects.create(name="Lead", company=company, priority="low", type="cold",
                                 phone_number=LEAD_PHONE, assigned_to=employee_user)
    return {"ext": ext, "trunk": trunk, "did": did, "lead": lead, "tenant": tenant_a}


def _code(resp):
    """The client-visible error code, from the rendered envelope (not response.data)."""
    return resp.json()["error"]["code"]


def _call(client, destination=LEAD_PHONE, headers=None, **fields):
    return client.post(f"{BASE}/calls/", {"destination": destination, **fields}, format="json", **(headers or {}))


# ---- placing a call ----------------------------------------------------------------


def test_click_to_call_queues_the_call_and_asks_the_node(employee_client, setup, worker, pbx_node):
    resp = _call(employee_client, crm_contact_id=setup["lead"].pk)
    assert resp.status_code == 202, resp.content
    body = api_body(resp)
    call = PbxCall.objects.get(pk=body["id"])
    assert (call.status, call.direction, call.origin) == ("queued", "outbound", "crm")
    assert call.extension == setup["ext"] and call.crm_contact == setup["lead"] and call.trunk == setup["trunk"]
    assert call.to_party == LEAD_PHONE and call.from_party == "100"

    [(path, headers, sent)] = worker.requests
    assert path == "/v1/calls/originate"
    from integrations.encryption import decrypt_token

    assert headers["Authorization"] == f"Bearer {decrypt_token(pbx_node.crm_token_encrypted)}"
    uuid.UUID(sent.pop("request_id"))
    assert sent == {
        "call_id": str(call.id), "tenant": "tdev4test", "extension": "100", "destination": LEAD_PHONE,
        "caller_id": "+9647700000001", "crm_contact_id": setup["lead"].pk, "ring_timeout_s": 30,
        "max_duration_s": 7200, "limits": {"max_concurrent_calls": 5, "max_cps": 1},
    }
    _validate_against_contract("originate_request", {**sent, "request_id": str(uuid.uuid4())})


def _validate_against_contract(defn, instance):
    """Same schema the worker validates against (copied shape check, no jsonschema dependency here)."""
    schema = json.loads((Path(__file__).resolve().parents[2] / "CRM-pbx" / "contract" / "schemas" /
                         "originate.schema.json").read_text(encoding="utf-8")) if (
        Path(__file__).resolve().parents[2] / "CRM-pbx").exists() else None
    if schema is None:
        return
    spec = schema["$defs"][defn]
    assert set(instance) <= set(spec["properties"]) and set(spec["required"]) <= set(instance)


@pytest.mark.parametrize("raw", ["+964 780 123 4567", "00964-780-123-4567", "(+964) 7801234567"])
def test_destination_is_normalised(employee_client, setup, worker, raw):
    assert _call(employee_client, raw).status_code == 202
    assert worker.originates()[0]["destination"] == LEAD_PHONE


@pytest.mark.parametrize("raw", ["07801234567", "abc", "", "+1"])
def test_invalid_destination(employee_client, setup, worker, raw):
    resp = _call(employee_client, raw)
    assert resp.status_code == 400 and _code(resp) == "invalid_destination"
    assert worker.requests == [] and not PbxCall.objects.exists()


def test_an_extensions_own_sim_is_the_caller_id(employee_client, setup, worker, company, make_trunk, make_number):
    sim_trunk = make_trunk(company, name="GSM", host="192.0.2.20", allowed_sources=["192.0.2.20/32"])
    sim = make_number(company, sim_trunk, "+9647700000099")
    setup["ext"].outbound_number = sim
    setup["ext"].save(update_fields=["outbound_number"])
    resp = _call(employee_client)
    assert resp.status_code == 202, resp.content
    call = PbxCall.objects.get(pk=api_body(resp)["id"])
    assert call.trunk_id == sim_trunk.id
    assert worker.requests[0][2]["caller_id"] == "+9647700000099"


def test_destination_without_a_route_is_refused(employee_client, setup, worker):
    resp = _call(employee_client, "+447700900123")
    assert resp.status_code == 403 and _code(resp) == "destination_not_allowed"
    assert worker.requests == []


def test_route_with_unverified_caller_id_is_refused(employee_client, setup, worker):
    setup["did"].verified_at = None
    setup["did"].save()
    assert _code(_call(employee_client)) == "destination_not_allowed"
    assert worker.requests == []


def test_disabled_route_is_not_an_allowlist_entry(employee_client, setup, worker):
    PbxOutboundRoute.objects.update(enabled=False)
    assert _code(_call(employee_client)) == "destination_not_allowed"


def test_user_without_extension(admin_client, setup, worker):
    resp = _call(admin_client)
    assert resp.status_code == 403 and _code(resp) == "no_extension"


def test_suspended_or_unprovisioned_tenant(employee_client, setup, worker):
    PbxTenant.objects.update(outbound_suspended=True)
    assert _code(_call(employee_client)) == "outbound_suspended"
    PbxTenant.objects.update(outbound_suspended=False, status=PbxTenant.Status.PENDING)
    assert _code(_call(employee_client)) == "not_provisioned"
    assert worker.requests == []


def test_emergency_numbers_are_always_placed(employee_client, setup, worker, settings, telephony_plan, company):
    """The browser softphone's only way to emergency services: no suspension, allowlist or cap blocks it."""
    from subscriptions.entitlements import get_month_period_start
    from subscriptions.models import CompanyUsageCounter

    settings.TELEPHONY_EMERGENCY_NUMBERS = ["104", "112"]
    PbxTenant.objects.update(outbound_suspended=True)
    PbxOutboundRoute.objects.update(patterns=[{"prefix": "+9647", "digits": 9}])  # no route matches "112"
    telephony_plan.usage_limits_monthly = {"monthly_pbx_minutes": 10}
    telephony_plan.save()
    CompanyUsageCounter.objects.create(company=company, key="monthly_pbx_minutes",
                                       period_start=get_month_period_start(), count=10)

    resp = _call(employee_client, " 112 ")
    assert resp.status_code == 202, resp.content
    [sent] = worker.originates()
    assert sent["destination"] == "112" and sent["emergency"] is True
    assert sent["caller_id"] == "+9647700000001"  # the route's verified number, as the compiler picks it
    _validate_against_contract("originate_request", sent)
    call = PbxCall.objects.get(pk=api_body(resp)["id"])
    assert (call.to_party, call.trunk) == ("112", setup["trunk"])

    # A short code that is not an emergency number stays invalid.
    resp = _call(employee_client, "113")
    assert resp.status_code == 400 and _code(resp) == "invalid_destination"


def test_emergency_call_needs_a_verified_number_on_a_working_line(employee_client, setup, worker, settings):
    from telephony.models import PbxNumber, PbxTrunk

    settings.TELEPHONY_EMERGENCY_NUMBERS = ["112"]
    PbxOutboundRoute.objects.all().delete()
    PbxNumber.objects.update(verified_at=None)
    resp = _call(employee_client, "112")
    assert resp.status_code == 503 and _code(resp) == "emergency_unroutable"
    PbxNumber.objects.update(verified_at=setup["did"].created_at)
    PbxTrunk.objects.update(status=PbxTrunk.Status.DISABLED)
    assert _code(_call(employee_client, "112")) == "emergency_unroutable"
    assert worker.requests == []


def test_lead_must_be_visible_to_the_caller(employee_client, setup, worker, company, admin_user, other_company):
    from crm.models import Client

    someone_elses = Client.objects.create(name="Not mine", company=company, priority="low", type="cold",
                                          phone_number="+9647801111111", assigned_to=admin_user)
    other_tenants = Client.objects.create(name="B lead", company=other_company, priority="low", type="cold",
                                          phone_number="+9647802222222")
    for lead in (someone_elses, other_tenants):
        resp = _call(employee_client, crm_contact_id=lead.pk)
        assert resp.status_code == 404 and _code(resp) == "invalid_contact"
    assert worker.requests == []


def test_monthly_minutes_cap(employee_client, setup, worker, telephony_plan, company):
    from subscriptions.entitlements import get_month_period_start
    from subscriptions.models import CompanyUsageCounter

    telephony_plan.usage_limits_monthly = {"monthly_pbx_minutes": 10}
    telephony_plan.save()
    CompanyUsageCounter.objects.create(company=company, key="monthly_pbx_minutes",
                                       period_start=get_month_period_start(), count=10)
    resp = _call(employee_client)
    # Same envelope as every other entitlement limit in the CRM (subscriptions.entitlements).
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "monthly_pbx_minutes"
    assert worker.requests == [] and not PbxCall.objects.exists()


@pytest.mark.parametrize("code,status,client_code", [
    ("concurrency_limit", 409, "concurrency_limit"),
    ("cps_limit", 409, "cps_limit"),
    ("extension_unavailable", 409, "phone_not_registered"),
    ("ari_unavailable", 503, "pbx_unavailable"),
])
def test_node_refusals_fail_the_queued_call(employee_client, setup, worker, code, status, client_code):
    worker.reply = (status, {"code": code})
    resp = _call(employee_client)
    assert _code(resp) == client_code
    assert resp.status_code == {"concurrency_limit": 429, "cps_limit": 429}.get(client_code, 409 if status == 409 else 503)
    assert PbxCall.objects.get().status == "failed"


def test_unreachable_node(employee_client, setup, pbx_node):
    pbx_node.control_url = "http://127.0.0.1:9"  # discard port: connection refused
    pbx_node.save()
    resp = _call(employee_client)
    assert resp.status_code == 503 and _code(resp) == "pbx_unavailable"


def test_idempotency_key(employee_client, setup, worker):
    first = _call(employee_client, headers={"HTTP_IDEMPOTENCY_KEY": "click-1"})
    again = _call(employee_client, headers={"HTTP_IDEMPOTENCY_KEY": "click-1"})
    assert api_body(first)["id"] == api_body(again)["id"] and len(worker.originates()) == 1
    assert api_body(_call(employee_client, headers={"HTTP_IDEMPOTENCY_KEY": "click-2"}))["id"] != api_body(first)["id"]


# ---- hangup ------------------------------------------------------------------------


def test_hangup(employee_client, setup, worker):
    call_id = api_body(_call(employee_client))["id"]
    resp = employee_client.post(f"{BASE}/calls/{call_id}/hangup/")
    assert resp.status_code == 202
    path, _, body = worker.requests[-1]
    assert path == f"/v1/calls/{call_id}/hangup" and body["tenant"] == "tdev4test"

    PbxCall.objects.filter(pk=call_id).update(status="answered")
    resp = employee_client.post(f"{BASE}/calls/{call_id}/hangup/")
    assert resp.status_code == 409 and _code(resp) == "call_not_active"


def test_cannot_hang_up_other_tenants_call(other_admin_client, employee_client, setup, worker):
    call_id = api_body(_call(employee_client))["id"]
    assert other_admin_client.post(f"{BASE}/calls/{call_id}/hangup/").status_code == 404
    assert [p for p, _, _ in worker.requests] == ["/v1/calls/originate"]


# ---- node events for a CRM-originated call --------------------------------------


def _events(node, events):
    from rest_framework.test import APIClient

    c = APIClient()
    batch = {"node_id": str(node.pk), "batch_id": str(uuid.uuid4()), "sent_at": "2026-10-15T10:00:00Z",
             "events": [{"event_id": str(uuid.uuid4()), "seq": i + 1, "tenant": "tdev4test", **e}
                        for i, e in enumerate(events)]}
    return c.post("/api/v1/internal/telephony/events", batch, format="json",
                  HTTP_AUTHORIZATION=f"Bearer {node.plain_token}")


def test_node_events_complete_the_queued_call_and_count_minutes(employee_client, setup, worker, pbx_node, company):
    from crm.models import ClientCall
    from subscriptions.entitlements import get_monthly_usage_snapshot

    call_id = api_body(_call(employee_client, crm_contact_id=setup["lead"].pk))["id"]
    common = {"call_id": call_id, "linked_id": call_id}
    trunk_leg = {"uniqueid": "1792021131.41", "endpoint": "tdev4test_trk_1a2b3c4d", "source": "100",
                 "destination": LEAD_PHONE}
    resp = _events(pbx_node, [
        {**common, "type": "call.created", "occurred_at": "2026-10-15T10:00:00Z",
         "data": {"direction": "outbound", "origin": "crm", "from": "100", "to": LEAD_PHONE, "extension": "100"}},
        {**common, "type": "call.answered", "occurred_at": "2026-10-15T10:00:05Z",
         "data": {"leg": {**trunk_leg, "disposition": "answered", "answered_at": "2026-10-15T10:00:05Z"}}},
        {**common, "type": "call.ended", "occurred_at": "2026-10-15T10:01:06Z",
         "data": {"status": "answered", "hangup_cause": 16, "duration_s": 66, "billsec_s": 61, "recorded": False,
                  "legs": [trunk_leg]}},
    ])
    assert resp.status_code == 200 and len(api_body(resp)["accepted"]) == 3, resp.content
    call = PbxCall.objects.get(pk=call_id)
    assert (call.status, call.linked_id, call.billsec_s) == ("answered", call_id, 61)
    assert call.crm_contact == setup["lead"]  # the CRM set it; the node never named the contact
    assert ClientCall.objects.get(pbx_call=call).client == setup["lead"]
    assert get_monthly_usage_snapshot(company).get("monthly_pbx_minutes") == 2  # 61 s = 2 started minutes

    # Timeline payload (web + crm_mobile render these typed fields).
    from crm.serializers import ClientCallListSerializer, ClientCallSerializer

    for serializer in (ClientCallSerializer, ClientCallListSerializer):
        data = serializer(ClientCall.objects.get(pbx_call=call)).data
        assert data["source"] == "pbx" and data["pbx_call_id"] == call_id
        assert (data["pbx_direction"], data["pbx_call_status"], data["pbx_duration_sec"]) == ("outbound", "answered", 61)
        assert data["pbx_recording_id"] is None and data["pbx_recording_status"] is None


# ---- me + webrtc credentials ---------------------------------------------------------


def test_me(employee_client, admin_client, setup):
    body = api_body(employee_client.get(f"{BASE}/me/"))
    assert body["extension"]["number"] == "100" and body["webrtc"] is True and body["can_click_to_call"] is True
    assert api_body(admin_client.get(f"{BASE}/me/"))["extension"] is None


def test_webrtc_credentials_edge_match_the_contract(employee_client, setup, settings, monkeypatch):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.PBX_SIP_EPHEMERAL_SECRET = "sip-shared-secret-for-vectors"
    settings.PBX_TURN_SECRET = "turn-shared-secret-for-vectors"
    settings.TELEPHONY_SIP_WS_URI = "wss://sip.loop-crm.example/ws"
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    settings.TELEPHONY_TURN_URLS = ["turns:turn.loop-crm.example:5349?transport=tcp"]
    settings.TELEPHONY_CREDENTIAL_TTL_SECONDS = 28800
    monkeypatch.setattr("telephony.services.webrtc.time.time", lambda: 1760433600)
    resp = employee_client.get(f"{BASE}/me/webrtc-credentials/")
    assert resp.status_code == 200, resp.content
    body = api_body(resp)
    vector = json.loads((Path(__file__).parent / "fixtures/pbx_contract/credentials.json").read_text())["cases"][0]
    assert body["sip"]["username"] == vector["username"] == "1760462400:tdev4test_100"
    assert body["sip"]["password"] == vector["password"]
    assert body["sip"]["uri"] == "sip:tdev4test_100@sip.loop-crm.example"
    assert body["turn"]["username"] == "1760462400:tdev4test_100" and body["turn"]["credential"] != body["sip"]["password"]
    assert body["expires_at"] == "2025-10-14T17:20:00Z" and body["refresh_after"] == "2025-10-14T16:50:00Z"
    assert resp["Cache-Control"] == "private, no-store"

    # Cached until refresh_after: a reload does not mint a new credential.
    monkeypatch.setattr("telephony.services.webrtc.time.time", lambda: 1760433600 + 3600)
    assert api_body(employee_client.get(f"{BASE}/me/webrtc-credentials/"))["sip"]["username"] == vector["username"]
    monkeypatch.setattr("telephony.services.webrtc.time.time", lambda: 1760462400 - 1700)
    assert api_body(employee_client.get(f"{BASE}/me/webrtc-credentials/"))["sip"]["username"] != vector["username"]


def test_edge_without_secrets_is_503(employee_client, setup, settings):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.PBX_SIP_EPHEMERAL_SECRET = ""
    resp = employee_client.get(f"{BASE}/me/webrtc-credentials/")
    assert resp.status_code == 503 and _code(resp) == "not_configured"


def test_edge_refuses_reused_sip_and_turn_secret(employee_client, setup, settings):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.PBX_SIP_EPHEMERAL_SECRET = "one-secret-must-not-serve-two-protocols"
    settings.PBX_TURN_SECRET = settings.PBX_SIP_EPHEMERAL_SECRET
    settings.TELEPHONY_SIP_WS_URI = "wss://sip.loop-crm.example/ws"
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    settings.TELEPHONY_TURN_URLS = ["turns:turn.loop-crm.example:5349?transport=tcp"]
    resp = employee_client.get(f"{BASE}/me/webrtc-credentials/")
    assert resp.status_code == 503 and _code(resp) == "not_configured"


def test_static_secret_never_leaves_a_production_server(employee_client, setup, settings):
    settings.TELEPHONY_WEBRTC_AUTH = "static"
    settings.DEBUG = False
    settings.TELEPHONY_ALLOW_STATIC_WEBRTC = False
    resp = employee_client.get(f"{BASE}/me/webrtc-credentials/")
    assert resp.status_code == 503 and "dev-only" not in resp.content.decode()

    settings.TELEPHONY_ALLOW_STATIC_WEBRTC = True
    body = api_body(employee_client.get(f"{BASE}/me/webrtc-credentials/"))
    assert body["mode"] == "static" and body["sip"]["password"] == "dev-only-secret-0000"


def test_desk_phone_extension_has_no_browser_credentials(employee_client, setup):
    setup["ext"].device_type = DeviceType.DESK_PHONE
    setup["ext"].save()
    resp = employee_client.get(f"{BASE}/me/webrtc-credentials/")
    assert resp.status_code == 409 and _code(resp) == "not_webrtc"


def test_no_extension_no_credentials(admin_client, setup):
    assert admin_client.get(f"{BASE}/me/webrtc-credentials/").status_code == 403


# ---- apply-now nudge after a compile (contract section 6) -------------------------------


def _recompile(admin_client, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        resp = admin_client.post(f"{BASE}/extensions/", {"number": "150", "device_type": "desk_phone"}, format="json")
    assert resp.status_code == 201, resp.content


def test_compile_does_not_nudge_by_default(admin_client, setup, worker, django_capture_on_commit_callbacks):
    _recompile(admin_client, django_capture_on_commit_callbacks)
    assert setup["tenant"].jobs.filter(status="awaiting_apply").exists()
    assert [p for p, _, _ in worker.requests] == []


def test_compile_nudges_the_home_node(admin_client, setup, worker, pbx_node, settings, django_capture_on_commit_callbacks):
    settings.TELEPHONY_APPLY_NUDGE = True
    _recompile(admin_client, django_capture_on_commit_callbacks)
    [(path, headers, body)] = worker.requests
    assert path == "/v1/apply-now" and body == {}
    assert headers["Authorization"] == f"Bearer {pbx_node.plain_crm_token}"


def test_unreachable_node_does_not_fail_the_compile(admin_client, setup, pbx_node, settings, django_capture_on_commit_callbacks):
    import socket
    import time

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    pbx_node.control_url = f"http://127.0.0.1:{dead}"
    pbx_node.save(update_fields=["control_url"])
    settings.TELEPHONY_APPLY_NUDGE = True
    started = time.monotonic()
    _recompile(admin_client, django_capture_on_commit_callbacks)
    assert time.monotonic() - started < 5
    assert setup["tenant"].jobs.filter(status="awaiting_apply").exists()


# ---- agent status (spec section 6; contract section 6 POST /v1/agents/status) ----------------


def _status(client, value):
    return client.put(f"{BASE}/agents/me/status/", {"status": value}, format="json")


def test_pausing_pushes_to_the_node_then_saves(employee_client, setup, worker, pbx_node):
    resp = _status(employee_client, "paused")
    assert resp.status_code == 200, resp.content
    assert api_body(resp)["status"] == "paused"
    [(path, headers, body)] = [r for r in worker.requests if r[0] == "/v1/agents/status"]
    assert body["tenant"] == setup["tenant"].tag and body["extension"] == "100" and body["status"] == "paused"
    assert headers["Authorization"] == f"Bearer {pbx_node.plain_crm_token}"
    setup["ext"].refresh_from_db()
    assert setup["ext"].agent_status == "paused" and setup["ext"].agent_status_changed_at is not None
    assert api_body(employee_client.get(f"{BASE}/me/"))["extension"]["agent_status"] == "paused"
    assert api_body(employee_client.get(f"{BASE}/agents/me/status/"))["status"] == "paused"

    # Setting it again re-sends it (repairs a node that lost it) without a new change time.
    changed = setup["ext"].agent_status_changed_at
    assert _status(employee_client, "paused").status_code == 200
    setup["ext"].refresh_from_db()
    assert setup["ext"].agent_status_changed_at == changed
    assert len([r for r in worker.requests if r[0] == "/v1/agents/status"]) == 2


def test_status_is_not_saved_when_the_node_refuses(employee_client, setup, worker):
    worker.reply = (503, {"code": "ari_unavailable"})
    resp = _status(employee_client, "paused")
    assert resp.status_code == 503 and _code(resp) == "pbx_unavailable"
    setup["ext"].refresh_from_db()
    assert setup["ext"].agent_status == "available"


def test_unreachable_node_status(employee_client, setup, pbx_node):
    pbx_node.control_url = ""
    pbx_node.save(update_fields=["control_url"])
    resp = _status(employee_client, "paused")
    assert resp.status_code == 503 and _code(resp) == "pbx_unavailable"


@pytest.mark.parametrize("value", ["away", "", None, "PAUSED"])
def test_invalid_status(employee_client, setup, worker, value):
    resp = _status(employee_client, value)
    assert resp.status_code == 400 and _code(resp) == "invalid_status"
    assert worker.requests == []


def test_status_needs_an_own_extension(admin_client, setup, worker):
    resp = _status(admin_client, "paused")
    assert resp.status_code == 403 and _code(resp) == "no_extension"
    assert admin_client.get(f"{BASE}/agents/me/status/").status_code == 403


def test_status_needs_a_live_tenant(employee_client, setup, worker):
    PbxTenant.objects.filter(pk=setup["tenant"].pk).update(status=PbxTenant.Status.PENDING)
    resp = _status(employee_client, "paused")
    assert resp.status_code == 409 and _code(resp) == "not_provisioned"
    assert worker.requests == []


def test_presence_lists_the_company_agents(employee_client, setup, worker, company, admin_user, make_extension,
                                           other_company):
    from telephony.models import PbxExtension

    desk = make_extension(company, "101", device_type=DeviceType.DESK_PHONE, user=admin_user, display_name="Desk")
    PbxExtension.objects.filter(pk=desk.pk).update(registered=True)
    make_extension(other_company, "100", display_name="Other tenant")
    PbxCall.objects.create(id=uuid.uuid4(), company=company, tenant=setup["tenant"], direction="outbound",
                           status="in_progress", extension=desk, started_at=datetime.now(dt_timezone.utc))
    assert _status(employee_client, "paused").status_code == 200

    agents = api_body(employee_client.get(f"{BASE}/agents/status/"))["agents"]
    assert [(a["number"], a["agent_status"], a["registered"], a["on_call"]) for a in agents] == [
        ("100", "paused", False, False),
        ("101", "available", True, True),
    ]
    assert agents[1]["user"] == admin_user.pk and agents[1]["display_name"] == "Desk"


def test_browser_may_send_the_idempotency_key_cross_origin(client):
    """The softphone sends Idempotency-Key; without it in the CORS allow-list the preflight blocks the POST."""
    resp = client.options(
        f"{BASE}/calls/",
        HTTP_ORIGIN="http://localhost:3000",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type,idempotency-key,x-api-key",
    )
    allowed = {h.strip().lower() for h in resp["Access-Control-Allow-Headers"].split(",")}
    assert {"authorization", "content-type", "idempotency-key", "x-api-key"} <= allowed
