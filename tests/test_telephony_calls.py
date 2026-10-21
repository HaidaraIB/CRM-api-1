"""
Phase 2: node-reported call events, recordings, CRM linking, and the calls API.

Event payloads follow CRM-pbx/contract/schemas/events-batch.schema.json.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

import pytest
from rest_framework.test import APIClient

from conftest import api_body
from crm.models import Client, ClientCall
from telephony import contract
from telephony.models import PbxCall, PbxCallEvent, PbxExtension, PbxRecording, PbxTrunk
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 10, 14, 9, 15, tzinfo=dt_tz.utc)
LINKED = "1760433300.42"


def ts(seconds=0):
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Node:
    """Builds and posts contract-shaped batches as a given PBX node."""

    def __init__(self, node):
        self.node = node
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {node.plain_token}")
        self.seq = 0

    def event(self, type_, tenant, data, *, at=0, call_id=None, linked_id=LINKED, event_id=None):
        self.seq += 1
        ev = {
            "event_id": event_id or str(uuid.uuid4()),
            "type": type_,
            "tenant": tenant.tag,
            "occurred_at": ts(at),
            "seq": self.seq,
            "data": data,
        }
        if type_.startswith("call."):
            ev["call_id"] = call_id or contract.derived_call_id(tenant.realm_key, linked_id)
            ev["linked_id"] = linked_id
        return ev

    def push(self, *events):
        body = {"node_id": str(self.node.id), "batch_id": str(uuid.uuid4()), "sent_at": ts(60), "events": list(events)}
        resp = self.client.post("/api/v1/internal/telephony/events", body, format="json", HTTP_ACCEPT="application/json")
        assert resp.status_code == 200, resp.content
        return json.loads(resp.content)

    def recordings(self, *items):
        body = {"node_id": str(self.node.id), "recordings": list(items)}
        resp = self.client.post("/api/v1/internal/telephony/recordings", body, format="json")
        assert resp.status_code == 200, resp.content
        return json.loads(resp.content)


@pytest.fixture
def node(pbx_node):
    return Node(pbx_node)


@pytest.fixture
def setup(company, tenant_a, admin_user, employee_user, make_trunk, make_number, make_extension):
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"))
    make_number(company, trunk, "+9647700000001")
    agent = make_extension(company, "100", user=employee_user)
    make_extension(company, "101", user=admin_user)
    lead = Client.objects.create(name="Lead", company=company, priority="low", type="cold",
                                 phone_number="+9647801234567", assigned_to=employee_user)
    return {"tenant": tenant_a, "trunk": trunk, "agent": agent, "lead": lead}


def inbound_call(node, tenant, *, status="answered", linked_id=LINKED, answered=True):
    leg_trunk = {"uniqueid": linked_id, "endpoint": f"{tenant.tag}_trk_1a2b3c4d", "source": "+9647801234567",
                 "destination": "+9647700000001", "started_at": ts(0)}
    leg_agent = {"uniqueid": linked_id + "1", "endpoint": f"{tenant.tag}_100", "extension": "100",
                 "source": "+9647801234567", "destination": "100", "started_at": ts(1)}
    events = [
        node.event("call.created", tenant, {"direction": "inbound", "origin": "pbx", "from": "+9647801234567",
                                            "to": "+9647700000001", "did": "+9647700000001",
                                            "trunk_id": "1a2b3c4d-0000-4000-8000-000000000001"}, at=0, linked_id=linked_id),
        node.event("call.ringing", tenant, {"leg": leg_agent}, at=1, linked_id=linked_id),
    ]
    if answered:
        events.append(node.event("call.answered", tenant, {"leg": {**leg_agent, "answered_at": ts(6)}}, at=6, linked_id=linked_id))
    legs = [
        {**leg_trunk, "disposition": "answered" if answered else "no_answer", "ended_at": ts(30)},
        {**leg_agent, "disposition": "answered" if answered else "no_answer", "ended_at": ts(30),
         **({"answered_at": ts(6)} if answered else {})},
    ]
    events.append(node.event("call.ended", tenant, {
        "status": status, "hangup_cause": 16, "duration_s": 30, "billsec_s": 24 if answered else 0,
        **({"answered_by_extension": "100"} if answered else {}), "recorded": False, "legs": legs,
    }, at=30, linked_id=linked_id))
    return events


# ---- ingest ----------------------------------------------------------------


def test_inbound_answered_call_lifecycle(node, setup):
    tenant, lead = setup["tenant"], setup["lead"]
    events = inbound_call(node, tenant)
    with mock.patch("realtime.publish.publish_telephony_event") as frames:
        result = node.push(*events)
    assert len(result["accepted"]) == 4 and not result["duplicates"] and not result["rejected"]

    call = PbxCall.objects.get()
    assert str(call.id) == contract.derived_call_id(tenant.realm_key, LINKED)
    assert (call.direction, call.status, call.billsec_s, call.hangup_cause) == ("inbound", "answered", 24, 16)
    assert call.answered_by == setup["agent"] and call.trunk == setup["trunk"]
    assert call.answered_at == T0 + timedelta(seconds=6) and call.ended_at == T0 + timedelta(seconds=30)
    assert call.legs.count() == 2 and call.crm_contact == lead

    # Linked into the lead timeline exactly like WhatsApp calls.
    cc = ClientCall.objects.get(pbx_call=call)
    assert cc.client == lead and cc.source == "pbx" and cc.created_by == setup["agent"].user
    assert cc.notes == "Phone call · Incoming · Answered · 24s" and cc.dialed_phone_number == "+9647801234567"

    # Data-free frames to the agent on the call.
    sent = {(c.args[0], c.args[1]) for c in frames.call_args_list}
    agent_id = setup["agent"].user_id
    assert {(agent_id, "call.ringing"), (agent_id, "call.answered"), (agent_id, "call.ended")} <= sent


def test_replayed_batch_is_idempotent(node, setup):
    events = inbound_call(node, setup["tenant"])
    node.push(*events)
    again = node.push(*events)
    assert len(again["duplicates"]) == 4 and not again["accepted"]
    assert PbxCall.objects.count() == 1 and PbxCallEvent.objects.count() == 4
    assert ClientCall.objects.count() == 1


def test_out_of_order_delivery_converges(node, setup):
    created, ringing, answered, ended = inbound_call(node, setup["tenant"])
    node.push(ended)
    call = PbxCall.objects.get()
    assert call.status == "answered" and call.direction == ""
    assert not ClientCall.objects.exists()  # direction unknown yet

    node.push(ringing, answered, created)
    call.refresh_from_db()
    assert call.status == "answered"  # a late ringing/answered never reopens the call
    assert call.direction == "inbound" and call.from_party == "+9647801234567"
    assert ClientCall.objects.filter(pbx_call=call).count() == 1  # linked once created arrived


def test_missed_call_is_logged_on_lead(node, setup):
    node.push(*inbound_call(node, setup["tenant"], status="missed", answered=False))
    call = PbxCall.objects.get()
    assert call.status == "missed" and call.answered_by is None
    assert ClientCall.objects.get(pbx_call=call).notes == "Phone call · Incoming · Missed"


def test_internal_and_unknown_numbers_are_not_linked(node, setup):
    tenant = setup["tenant"]
    node.push(
        node.event("call.created", tenant, {"direction": "internal", "origin": "pbx", "from": "101", "to": "100"}, linked_id="9.1"),
        node.event("call.ended", tenant, {"status": "answered", "hangup_cause": 16, "duration_s": 5, "billsec_s": 4, "legs": []}, at=5, linked_id="9.1"),
        node.event("call.created", tenant, {"direction": "inbound", "origin": "pbx", "from": "+9647809999999", "to": "+9647700000001"}, linked_id="9.2"),
        node.event("call.ended", tenant, {"status": "missed", "hangup_cause": 16, "duration_s": 5, "billsec_s": 0, "legs": []}, at=5, linked_id="9.2"),
    )
    assert PbxCall.objects.count() == 2 and not ClientCall.objects.exists()


def test_crm_contact_only_trusted_for_crm_originated_calls(node, setup, company):
    tenant = setup["tenant"]
    other_lead = Client.objects.create(name="Other", company=company, priority="low", type="cold", phone_number="+9647700000099")
    node.push(node.event("call.created", tenant, {"direction": "outbound", "origin": "pbx", "from": "100",
                                                  "to": "+9647801234567", "crm_contact_id": other_lead.id}))
    assert PbxCall.objects.get().crm_contact is None


def test_events_for_tenants_not_on_this_node_are_rejected(node, setup, other_company, tenant_b):
    from telephony.services.provisioning import register_node

    other_node, _, _ = register_node(name="pbx-2")
    tenant_b.home_node = other_node
    tenant_b.save()
    ev_b = node.event("call.created", tenant_b, {"direction": "inbound", "origin": "pbx", "from": "+9647801234567", "to": "+9647700000002"})
    ev_unknown = node.event("call.created", setup["tenant"], {"direction": "inbound", "origin": "pbx", "from": "+9647801234567", "to": "+9647700000002"})
    ev_unknown["tenant"] = "tzz2zzzzz"
    result = node.push(ev_b, ev_unknown)
    codes = {r["id"]: r["code"] for r in result["rejected"]}
    assert codes == {ev_b["event_id"]: "tenant_not_on_node", ev_unknown["event_id"]: "unknown_tenant"}
    assert not PbxCall.objects.exists()


def test_cross_tenant_call_and_leg_references_are_rejected(node, setup, other_company, tenant_b):
    tenant_a = setup["tenant"]
    node.push(*inbound_call(node, tenant_b, linked_id="7.1"))
    b_call = PbxCall.objects.get(company=other_company)
    # Tenant A event naming B's call id: must not attach to it.
    hijack = node.event("call.ended", tenant_a, {"status": "failed", "hangup_cause": 1, "duration_s": 0, "billsec_s": 0, "legs": []},
                        call_id=str(b_call.id), linked_id="7.9")
    # Tenant A leg naming tenant B's endpoint.
    foreign_leg = node.event("call.ringing", tenant_a, {"leg": {"uniqueid": "8.1", "endpoint": f"{tenant_b.tag}_100"}}, linked_id="8.1")
    result = node.push(hijack, foreign_leg)
    assert {r["code"] for r in result["rejected"]} == {"invalid_payload"} and len(result["rejected"]) == 2
    b_call.refresh_from_db()
    assert b_call.status == "answered" and b_call.company == other_company


def test_invalid_events_are_rejected_individually(node, setup):
    tenant = setup["tenant"]
    good = node.event("call.created", tenant, {"direction": "inbound", "origin": "pbx", "from": "+9647801234567", "to": "+9647700000001"})
    bad_type = node.event("call.created", tenant, {})
    bad_type["type"] = "call.teleported"
    bad_party = node.event("call.created", tenant, {"direction": "inbound", "from": "<script>", "to": "1"}, linked_id="3.3")
    bad_status = node.event("call.ended", tenant, {"status": "completed", "legs": []}, linked_id="3.4")
    result = node.push(good, bad_type, bad_party, bad_status)
    assert result["accepted"] == [good["event_id"]]
    assert {r["id"] for r in result["rejected"]} == {bad_type["event_id"], bad_party["event_id"], bad_status["event_id"]}
    # A rejected event leaves nothing behind (no event row, no half-built call).
    assert PbxCallEvent.objects.count() == 1


def test_batch_level_validation(node, pbx_node):
    resp = node.client.post("/api/v1/internal/telephony/events", {"events": []}, format="json")
    assert resp.status_code == 400
    assert APIClient().post("/api/v1/internal/telephony/events", {"events": []}, format="json").status_code in (401, 403)


def test_agent_presence_and_gateway_health(node, setup):
    tenant = setup["tenant"]
    node.push(node.event("agent.status_changed", tenant, {"extension": "100", "registered": True, "device": "webrtc"}, at=10))
    node.push(node.event("agent.status_changed", tenant, {"extension": "100", "registered": False}, at=5))  # older: ignored
    ext = PbxExtension.objects.get(number="100")
    assert ext.registered is True and ext.registered_device == "webrtc"

    node.push(node.event("gateway.status_changed", tenant, {"trunk_id": str(setup["trunk"].id), "status": "down", "reason": "OPTIONS timeout"}))
    trunk = PbxTrunk.objects.get()
    assert trunk.health == "down" and trunk.status == "pending"  # health never verifies a trunk


def test_slice_is_bumped_once_per_batch(node, setup):
    with mock.patch("sync.version.bump_company_slice") as bump:
        node.push(*inbound_call(node, setup["tenant"]))
    assert [c.args for c in bump.call_args_list].count(("telephony", setup["tenant"].company_id)) == 1


# ---- recordings ------------------------------------------------------------


def recording(tenant, call_id, *, company_id=None, rec_id=None, fmt="ogg"):
    rec_id = rec_id or str(uuid.uuid4())
    return {
        "recording_id": rec_id, "tenant": tenant.tag, "call_id": call_id,
        "storage_key": f"telephony/{company_id or tenant.company_id}/2026/10/{call_id}/{rec_id}.{fmt}",
        "bytes": 1024, "sha256": "a" * 64, "duration_s": 24, "format": fmt, "started_at": ts(6),
    }


def test_recording_ingest_links_and_marks_timeline(node, setup):
    node.push(*inbound_call(node, setup["tenant"]))
    call = PbxCall.objects.get()
    item = recording(setup["tenant"], str(call.id))
    assert node.recordings(item)["accepted"] == [item["recording_id"]]
    assert node.recordings(item)["duplicates"] == [item["recording_id"]]
    assert call.recordings.count() == 1
    assert ClientCall.objects.get(pbx_call=call).recording_status == "ready"


def test_recording_key_outside_tenant_prefix_is_rejected(node, setup, other_company):
    call_id = contract.derived_call_id(setup["tenant"].realm_key, LINKED)
    wrong_company = recording(setup["tenant"], call_id, company_id=other_company.id)
    mismatched_call = recording(setup["tenant"], call_id)
    mismatched_call["storage_key"] = mismatched_call["storage_key"].replace(call_id, str(uuid.uuid4()))
    result = node.recordings(wrong_company, mismatched_call)
    assert {r["code"] for r in result["rejected"]} == {"storage_key_outside_tenant_prefix"}
    assert not PbxRecording.objects.exists()


def test_recording_before_call_is_linked_later(node, setup):
    call_id = contract.derived_call_id(setup["tenant"].realm_key, LINKED)
    node.recordings(recording(setup["tenant"], call_id))
    assert PbxRecording.objects.get().call is None
    node.push(*inbound_call(node, setup["tenant"]))
    assert PbxRecording.objects.get().call_id == uuid.UUID(call_id)


# ---- calls API -------------------------------------------------------------


def test_calls_api_visibility(node, setup, admin_client, employee_client, other_admin_client, company):
    node.push(*inbound_call(node, setup["tenant"]))  # answered by ext 100 (employee)
    tenant = setup["tenant"]
    node.push(
        node.event("call.created", tenant, {"direction": "internal", "origin": "pbx", "from": "101", "to": "102"}, linked_id="5.5"),
    )
    admin_rows = api_body(admin_client.get("/api/v1/telephony/calls/"))["results"]
    emp_rows = api_body(employee_client.get("/api/v1/telephony/calls/"))["results"]
    assert len(admin_rows) == 2
    assert [r["direction"] for r in emp_rows] == ["inbound"]  # not the admin-only internal call
    assert api_body(other_admin_client.get("/api/v1/telephony/calls/"))["results"] == []

    call = PbxCall.objects.get(direction="inbound")
    detail = api_body(admin_client.get(f"/api/v1/telephony/calls/{call.id}/"))
    assert len(detail["legs"]) == 2 and detail["answered_by_number"] == "100"
    assert other_admin_client.get(f"/api/v1/telephony/calls/{call.id}/").status_code == 404


def test_recording_access_and_dev_stream(node, setup, admin_client, other_admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.RECORDING_STORAGE_BACKEND = "local"
    node.push(*inbound_call(node, setup["tenant"]))
    call = PbxCall.objects.get()
    item = recording(setup["tenant"], str(call.id))
    node.recordings(item)
    audio = tmp_path / "pbx_recordings" / item["storage_key"]
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"OggS-fake-audio")

    assert other_admin_client.post(f"/api/v1/telephony/recordings/{item['recording_id']}/access/").status_code == 404
    access = api_body(admin_client.post(f"/api/v1/telephony/recordings/{item['recording_id']}/access/"))
    assert access["url"] and access["expires_at"]
    path = access["url"].split("testserver", 1)[1]
    stream = APIClient().get(path)  # no JWT, no API key: the signed token authorizes
    assert stream.status_code == 200 and b"".join(stream.streaming_content) == b"OggS-fake-audio"
    assert APIClient().get(path.replace("/recordings/", "/recordings/x")).status_code == 404


def test_presigned_url_for_object_storage(node, setup, admin_client, settings):
    """R2: a 5-minute presigned GET on the recordings bucket, signed locally (no network), with the audio type."""
    from urllib.parse import parse_qs, urlsplit

    from object_storage.config import R2Settings, build_storages

    settings.STORAGES = build_storages(media="local", recordings="r2", r2=R2Settings(
        endpoint_url="https://acc0unt.r2.cloudflarestorage.com", access_key_id="AKTEST", secret_access_key="s3cr3t",
    ))
    node.push(*inbound_call(node, setup["tenant"]))
    item = recording(setup["tenant"], str(PbxCall.objects.get().id))
    node.recordings(item)
    access = api_body(admin_client.post(f"/api/v1/telephony/recordings/{item['recording_id']}/access/"))
    url = urlsplit(access["url"])
    query = parse_qs(url.query)
    assert (url.scheme, url.netloc) == ("https", "acc0unt.r2.cloudflarestorage.com")
    assert url.path == f"/loop-pbx-recordings/{item['storage_key']}"  # path-style, the contract's bucket
    assert query["X-Amz-Expires"] == ["300"] and query["X-Amz-Credential"][0].startswith("AKTEST/")
    assert "/auto/s3/aws4_request" in query["X-Amz-Credential"][0]
    assert query["response-content-type"] == ["audio/ogg"]
    assert "s3cr3t" not in access["url"]


def test_event_replay_is_admin_only_and_scoped(node, setup, admin_client, employee_client, other_admin_client):
    node.push(*inbound_call(node, setup["tenant"]))
    assert len(api_body(admin_client.get("/api/v1/telephony/events/"))["results"]) == 4
    assert employee_client.get("/api/v1/telephony/events/").status_code == 403
    assert api_body(other_admin_client.get("/api/v1/telephony/events/"))["results"] == []


def test_trunk_is_resolved_from_the_trunk_leg_name(node, setup):
    """Nodes send no trunk UUID; the tenant-qualified trunk leg identifies it."""
    tenant = setup["tenant"]
    events = inbound_call(node, tenant)
    events[0]["data"].pop("trunk_id")
    node.push(*events)
    assert PbxCall.objects.get().trunk == setup["trunk"]


def test_transfer_is_logged_and_the_target_agent_is_told(node, setup, admin_client, admin_user):
    """call.transferred (SIP REFER, CRM-pbx contract section 5): stored, listed on the call, frame to the target."""
    tenant = setup["tenant"]
    created, ringing, answered, ended = inbound_call(node, tenant)
    moved = node.event("call.transferred", tenant, {"mode": "blind", "from_extension": "100", "to_extension": "101"}, at=10)
    with mock.patch("realtime.publish.publish_telephony_event") as frames:
        result = node.push(created, ringing, answered, moved)
    assert len(result["accepted"]) == 4
    assert (admin_user.pk, "call.transferred", str(PbxCall.objects.get().pk)) in {c.args for c in frames.call_args_list}

    node.push(ended)
    call = PbxCall.objects.get()
    assert call.status == "answered"  # a transfer never changes the outcome
    detail = admin_client.get(f"/api/v1/telephony/calls/{call.pk}/").json()
    detail = detail.get("data", detail)
    assert [(t["mode"], t["from_extension"], t["to_extension"], t["to"]) for t in detail["transfers"]] == [
        ("blind", "100", "101", None)
    ]


def test_transfer_to_an_outside_number(node, setup):
    tenant = setup["tenant"]
    created = inbound_call(node, tenant)[0]
    moved = node.event("call.transferred", tenant, {"mode": "attended", "from_extension": "100", "to": "+9647809999999"}, at=10)
    assert len(node.push(created, moved)["accepted"]) == 2


@pytest.mark.parametrize("data", [
    {"mode": "blind"},                                                  # no target
    {"mode": "blind", "to_extension": "101", "to": "+9647809999999"},   # two targets
    {"mode": "park", "to_extension": "101"},                            # unknown mode
    {"mode": "blind", "to_extension": "0101"},                          # not an extension
    {"mode": "blind", "to": "PJSIP/tacme2pbx_100"},                     # not a party
])
def test_invalid_transfers_are_rejected(node, setup, data):
    tenant = setup["tenant"]
    created = inbound_call(node, tenant)[0]
    bad = node.event("call.transferred", tenant, data, at=10)
    result = node.push(created, bad)
    assert result["accepted"] == [created["event_id"]]
    assert [r["id"] for r in result["rejected"]] == [bad["event_id"]]
