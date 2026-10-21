"""
Queue waits (call.queued / call.dequeued, contract section 5) and the live calls /
queue wallboard (GET /telephony/wallboard/).
"""

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from telephony import contract
from telephony.models import PbxCall, PbxQueue, PbxQueueMember, PbxTenant
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

QUEUE_ID = uuid.UUID("9f8e7d6c-5b4a-4321-8fed-cba987654321")


class Node:
    """Posts contract-shaped batches; times are seconds relative to a base a few minutes ago."""

    def __init__(self, node, tenant):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {node.plain_token}")
        self.node, self.tenant, self.seq = node, tenant, 0
        self.base = timezone.now() - timedelta(minutes=10)

    def at(self, seconds):
        return (self.base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def event(self, type_, data, at, linked):
        self.seq += 1
        return {"event_id": str(uuid.uuid4()), "type": type_, "tenant": self.tenant.tag, "occurred_at": self.at(at),
                "seq": self.seq, "data": data, "linked_id": linked,
                "call_id": contract.derived_call_id(self.tenant.realm_key, linked)}

    def push(self, *events):
        body = {"node_id": str(self.node.id), "batch_id": str(uuid.uuid4()), "sent_at": self.at(600), "events": list(events)}
        resp = self.client.post("/api/v1/internal/telephony/events", body, format="json")
        assert resp.status_code == 200, resp.content
        return json.loads(resp.content)


@pytest.fixture
def setup(company, tenant_a, pbx_node, admin_user, employee_user, make_trunk, make_number, make_extension):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE)
    trunk = make_trunk(company, id=uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001"))
    make_number(company, trunk, "+9647700000001")
    agent = make_extension(company, "100", user=employee_user, display_name="Agent 100")
    desk = make_extension(company, "101", user=admin_user, display_name="Desk 101")
    queue = PbxQueue.objects.create(id=QUEUE_ID, company=company, name="Sales")
    for ext in (agent, desk):
        PbxQueueMember.objects.create(company=company, queue=queue, extension=ext)
    return {"node": Node(pbx_node, tenant_a), "tenant": tenant_a, "queue": queue, "agent": agent, "desk": desk}


QNAME = "tdev4test_q_9f8e7d6c"


def created(node, linked, at=0):
    return node.event("call.created", {"direction": "inbound", "origin": "pbx", "from": "+9647801234567",
                                       "to": "+9647700000001", "did": "+9647700000001"}, at, linked)


def leg(node, linked, ext="100"):
    return {"uniqueid": linked + "." + ext, "endpoint": f"{node.tenant.tag}_{ext}", "extension": ext}


def ended(node, linked, at, status, answered_by=None):
    data = {"status": status, "hangup_cause": 16, "duration_s": at, "billsec_s": 0, "legs": []}
    if answered_by:
        data["answered_by_extension"] = answered_by
    return node.event("call.ended", data, at, linked)


def _queue_name(tenant):
    return contract.queue_name(tenant.realm_key, QUEUE_ID)


# ---- ingest -------------------------------------------------------------------------


def test_queued_then_answered(setup):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "1.1"), node.event("call.queued", {"queue": _queue_name(t)}, 5, "1.1"),
              node.event("call.answered", {"leg": leg(node, "1.1")}, 17, "1.1"))
    call = PbxCall.objects.get()
    assert call.queue == setup["queue"] and call.queue_outcome == "answered"
    assert (call.queue_left_at - call.queued_at).total_seconds() == 12


def test_queued_then_overflow_then_abandoned_keeps_overflow(setup):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "1.2"), node.event("call.queued", {"queue": _queue_name(t)}, 5, "1.2"),
              node.event("call.dequeued", {"queue": _queue_name(t), "reason": "overflow"}, 65, "1.2"),
              ended(node, "1.2", 70, "busy"))
    call = PbxCall.objects.get()
    assert call.queue_outcome == "overflow" and (call.queue_left_at - call.queued_at).total_seconds() == 60


def test_caller_hanging_up_in_the_queue_is_abandoned(setup):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "1.3"), node.event("call.queued", {"queue": _queue_name(t)}, 5, "1.3"),
              ended(node, "1.3", 40, "abandoned"))
    assert PbxCall.objects.get().queue_outcome == "abandoned"


def test_queue_event_after_the_end_still_closes_the_wait(setup):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "1.4"), ended(node, "1.4", 40, "abandoned"))
    node.push(node.event("call.queued", {"queue": _queue_name(t)}, 5, "1.4"))  # late delivery
    call = PbxCall.objects.get()
    assert call.queue_outcome == "abandoned" and call.queue_left_at is not None


@pytest.mark.parametrize("queue", ["tacme2pbx_q_9f8e7d6c", "tdev4test-queue-9f8e7d6c", "", None])
def test_queue_of_another_tenant_or_malformed_is_rejected(setup, queue):
    node = setup["node"]
    result = node.push(created(node, "1.5"), node.event("call.queued", {"queue": queue}, 5, "1.5"))
    assert len(result["rejected"]) == 1 and result["rejected"][0]["code"] == "invalid_payload"
    assert PbxCall.objects.get().queue is None


def test_unknown_queue_of_this_tenant_is_kept_without_a_queue(setup):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "1.6"), node.event("call.queued", {"queue": f"{t.tag}_q_00000000"}, 5, "1.6"))
    call = PbxCall.objects.get()
    assert call.queue is None and call.queued_at is not None  # a deleted queue: the wait still counts


# ---- wallboard ----------------------------------------------------------------------


def test_wallboard_counts_waiting_callers_and_todays_outcomes(setup, admin_client):
    node, t = setup["node"], setup["tenant"]
    q = _queue_name(t)
    node.push(
        # answered after 12 s, overflowed after 60 s, abandoned after 35 s, and one still waiting
        created(node, "2.1"), node.event("call.queued", {"queue": q}, 5, "2.1"),
        node.event("call.answered", {"leg": leg(node, "2.1")}, 17, "2.1"), ended(node, "2.1", 60, "answered", "100"),
        created(node, "2.2", 10), node.event("call.queued", {"queue": q}, 12, "2.2"),
        node.event("call.dequeued", {"queue": q, "reason": "overflow"}, 72, "2.2"), ended(node, "2.2", 80, "busy"),
        created(node, "2.3", 20), node.event("call.queued", {"queue": q}, 25, "2.3"), ended(node, "2.3", 60, "abandoned"),
        created(node, "2.4", 500), node.event("call.queued", {"queue": q}, 510, "2.4"),
    )
    body = admin_client.get("/api/v1/telephony/wallboard/").json()["data"]
    [sales] = body["queues"]
    assert sales["name"] == "Sales" and sales["waiting"] == 1 and sales["longest_wait_s"] >= 80
    assert sales["today"] == {"offered": 3, "answered": 1, "abandoned": 1, "overflow": 1, "avg_wait_s": 12,
                              "max_wait_s": 12, "service_level_pct": 33}
    assert sales["agents"]["members"] == 2
    [live] = body["live_calls"]
    assert live["queue_name"] == "Sales" and live["waiting"] is True and live["from_party"] == "+9647801234567"
    assert body["today"]["calls"] == 4 and body["today"]["answered"] == 1 and body["today"]["missed"] == 1
    assert {a["number"] for a in body["agents"]} == {"100", "101"}


def test_agents_see_queue_counts_but_only_their_own_live_calls(setup, employee_client, admin_client):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "3.1"), node.event("call.queued", {"queue": _queue_name(t)}, 5, "3.1"))
    node.push(node.event("call.created", {"direction": "internal", "origin": "pbx", "from": "100", "to": "101",
                                          "extension": "100"}, 8, "3.2"))
    body = employee_client.get("/api/v1/telephony/wallboard/").json()["data"]
    assert body["queues"][0]["waiting"] == 1
    assert [c["direction"] for c in body["live_calls"]] == ["internal"]  # the employee's own call only
    assert len(admin_client.get("/api/v1/telephony/wallboard/").json()["data"]["live_calls"]) == 2


def test_wallboard_is_per_company(setup, other_admin_client, tenant_b, other_company):
    node, t = setup["node"], setup["tenant"]
    node.push(created(node, "4.1"), node.event("call.queued", {"queue": _queue_name(t)}, 5, "4.1"))
    PbxQueue.objects.create(company=other_company, name="B queue")
    body = other_admin_client.get("/api/v1/telephony/wallboard/").json()["data"]
    assert [q["name"] for q in body["queues"]] == ["B queue"] and body["queues"][0]["waiting"] == 0
    assert body["live_calls"] == [] and body["today"]["calls"] == 0


def test_wallboard_needs_telephony(company, employee_user):
    client = APIClient()
    client.force_authenticate(employee_user)
    assert client.get("/api/v1/telephony/wallboard/").status_code == 403
