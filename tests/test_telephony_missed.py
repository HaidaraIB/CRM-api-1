"""Phase 6: missed-call push (telephony/services/missed.py)."""

from __future__ import annotations

from unittest import mock

import pytest

from notifications.models import Notification
from notifications.translations import get_notification_text
from telephony.models import PbxCall, PbxQueue, PbxQueueMember
from telephony.services import missed
from tests.telephony_fixtures import *  # noqa: F401,F403
from tests.test_telephony_calls import LINKED, inbound_call, node, setup  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db


def _push(node, events):  # noqa: F811
    with mock.patch("realtime.publish.publish_telephony_event"):
        node.push(*events)


def _missed(send):
    """The pbx_call_missed sends (linking a call also notifies the team feed)."""
    return [c for c in send.call_args_list if c.kwargs.get("notification_type") == "pbx_call_missed"]


def test_missed_inbound_call_notifies_the_rung_agent_once(node, setup):  # noqa: F811
    events = inbound_call(node, setup["tenant"], status="missed", answered=False)
    with mock.patch("notifications.services.NotificationService.send_notification") as send:
        _push(node, events)
        # A replayed batch (same event ids) is a duplicate and notifies nobody again.
        _push(node, events)
        # Nor does a late call.created with a new id (it re-links a terminal call).
        _push(node, inbound_call(node, setup["tenant"], status="missed", answered=False)[:1])
    call = PbxCall.objects.get()
    assert call.status == "missed"
    sends = _missed(send)
    assert len(sends) == 1
    (user,), kwargs = sends[0]
    assert user == setup["agent"].user
    assert kwargs["notification_type"] == "pbx_call_missed"
    data = kwargs["data"]
    assert data["call_id"] == str(call.pk) and data["client_id"] == str(setup["lead"].pk)
    assert data["caller"] == "Lead" and data["phone"] == "+9647801234567"
    assert data["invalidate"] == "telephony:calls"


def test_answered_and_outbound_calls_are_not_missed(node, setup):  # noqa: F811
    with mock.patch("notifications.services.NotificationService.send_notification") as send:
        _push(node, inbound_call(node, setup["tenant"]))
    assert _missed(send) == []


def test_unknown_caller_gets_an_isolated_number_in_the_push(node, setup, settings):  # noqa: F811
    setup["lead"].delete()
    with mock.patch("notifications.services.NotificationService.send_notification") as send:
        _push(node, inbound_call(node, setup["tenant"], status="no_answer", answered=False))
    (sent,) = _missed(send)
    data = sent.kwargs["data"]
    assert "client_id" not in data
    assert data["caller"] == "⁨+9647801234567⁩"  # FSI … PDI keeps "+" left of the digits in Arabic


def test_recipients_fall_back_to_queue_members_then_the_lead_assignee(setup, company, employee_user, admin_user):
    call = PbxCall.objects.create(
        id="0b5cbbd0-5c5b-4f63-9d7e-000000000001", company=company, tenant=setup["tenant"], linked_id="x",
        direction="inbound", status="abandoned", from_party="+9647801234567", crm_contact=setup["lead"],
    )
    # No extension and no leg: the lead's assignee.
    assert missed.recipients(call) == [employee_user]
    # Waited in a queue nobody was rung from: its members, in penalty order.
    queue = PbxQueue.objects.create(company=company, name="Sales")
    ext101 = setup["agent"].__class__.objects.get(company=company, number="101")
    PbxQueueMember.objects.create(company=company, queue=queue, extension=ext101, penalty=0)
    PbxQueueMember.objects.create(company=company, queue=queue, extension=setup["agent"], penalty=5)
    call.queue = queue
    call.save(update_fields=["queue"])
    assert missed.recipients(call) == [admin_user, employee_user]
    # Inactive users are skipped.
    admin_user.is_active = False
    admin_user.save(update_fields=["is_active"])
    assert missed.recipients(call) == [employee_user]


def test_push_writes_an_inbox_notification(node, setup):  # noqa: F811
    _push(node, inbound_call(node, setup["tenant"], status="missed", answered=False))
    row = Notification.objects.get(user=setup["agent"].user, type="pbx_call_missed")
    assert row.data["call_id"] == str(PbxCall.objects.get().pk)


def test_missed_call_text_in_both_languages():
    en = get_notification_text("pbx_call_missed", "en", caller="Lead")
    ar = get_notification_text("pbx_call_missed", "ar", caller="Lead")
    assert (en["title"], en["body"]) == ("Missed call", "Missed call from Lead")
    assert (ar["title"], ar["body"]) == ("مكالمة فائتة", "مكالمة فائتة من Lead")


def test_missed_type_uses_the_tasks_channel():
    from notifications.fcm_android_channels import android_notification_channel_id

    assert android_notification_channel_id("pbx_call_missed") == "tasks"


def test_call_history_filters_on_several_statuses(node, setup, admin_client):  # noqa: F811
    _push(node, inbound_call(node, setup["tenant"], status="missed", answered=False))
    _push(node, inbound_call(node, setup["tenant"], linked_id="1760433300.77"))
    resp = admin_client.get("/api/v1/telephony/calls/?direction=inbound&status=missed,no_answer,abandoned")
    assert resp.status_code == 200
    assert [c["status"] for c in resp.json()["data"]["results"]] == ["missed"]
    both = admin_client.get("/api/v1/telephony/calls/?status=missed,answered").json()["data"]["results"]
    assert sorted(c["status"] for c in both) == ["answered", "missed"]


# ---- trunk / gateway down alerts (spec §12) --------------------------------------------------


def _health(node, tenant, trunk, status, at):  # noqa: F811
    from telephony import contract as c

    return node.event("gateway.status_changed", tenant,
                      {"trunk": f"{tenant.tag}_trk_{c.short_id(trunk.id)}", "status": status}, at=at)


def test_trunk_down_and_back_alert_the_company_admins_once_each(node, setup, owner_user, admin_user, employee_user):  # noqa: F811
    tenant, trunk = setup["tenant"], setup["trunk"]
    with mock.patch("notifications.services.NotificationService.send_notification") as send:
        node.push(_health(node, tenant, trunk, "ready", 0))  # first report: no alert
        node.push(_health(node, tenant, trunk, "down", 10))
        node.push(_health(node, tenant, trunk, "down", 20))  # no change: no alert
        node.push(_health(node, tenant, trunk, "ready", 30))
        node.push(_health(node, tenant, trunk, "down", 5))  # older than the last report: ignored
    sent = [(c.args[0], c.kwargs["notification_type"]) for c in send.call_args_list]
    admins = sorted([owner_user, admin_user], key=lambda u: u.pk)  # every active company admin
    assert sent == [(u, "pbx_trunk_down") for u in admins] + [(u, "pbx_trunk_recovered") for u in admins]
    assert employee_user not in {u for u, _ in sent}
    data = send.call_args_list[0].kwargs["data"]
    assert data["trunk_name"] == trunk.name and data["health"] == "down"


def test_trunk_alert_texts_in_both_languages():
    en = get_notification_text("pbx_trunk_down", "en", trunk_name="Carrier")
    ar = get_notification_text("pbx_trunk_recovered", "ar", trunk_name="Carrier")
    assert en["title"] == "Phone line down" and "Carrier is unreachable" in en["body"]
    assert ar["title"] == "عاد خط الهاتف" and "Carrier" in ar["body"]
    from notifications.fcm_android_channels import android_notification_channel_id

    assert android_notification_channel_id("pbx_trunk_down") == "system"
