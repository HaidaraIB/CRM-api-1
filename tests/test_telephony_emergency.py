"""Emergency calls (acceptance #10, spec §14; telephony/services/emergency.py)."""

from __future__ import annotations

from unittest import mock

import pytest

from notifications.translations import get_notification_text
from telephony.models import PbxCall, PbxExtension
from telephony.services import emergency
from telephony.services.alerts import recipients as alert_recipients
from tests.telephony_fixtures import *  # noqa: F401,F403
from tests.test_telephony_calls import node, setup, ts  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db
BASE = "/api/v1/telephony"
LINKED = "1760433999.7"


@pytest.fixture(autouse=True)
def _emergency_numbers(settings):
    settings.TELEPHONY_EMERGENCY_NUMBERS = ["104", "115", "122", "112", "911"]


def emergency_call(node, tenant, *, number="112", billsec=90):  # noqa: F811
    """A desk phone dialling a short emergency code, as the node's translator reports it (internal)."""
    leg_desk = {"uniqueid": LINKED, "endpoint": f"{tenant.tag}_101", "extension": "101", "source": "101",
                "destination": number, "started_at": ts(0)}
    leg_trunk = {"uniqueid": LINKED + "1", "endpoint": f"{tenant.tag}_trk_1a2b3c4d", "source": "+9647700000001",
                 "destination": number, "started_at": ts(1)}
    return [
        node.event("call.created", tenant, {"direction": "internal", "origin": "pbx", "from": "101", "to": number,
                                            "extension": "101"}, at=0, linked_id=LINKED),
        node.event("call.answered", tenant, {"leg": {**leg_trunk, "answered_at": ts(3)}}, at=3, linked_id=LINKED),
        node.event("call.ended", tenant, {
            "status": "answered", "hangup_cause": 16, "duration_s": billsec + 3, "billsec_s": billsec,
            "recorded": False,
            "legs": [{**leg_desk, "disposition": "answered", "ended_at": ts(93)},
                     {**leg_trunk, "disposition": "answered", "answered_at": ts(3), "ended_at": ts(93)}],
        }, at=93, linked_id=LINKED),
    ]


def _alerts(send):
    return [c for c in send.call_args_list if c.kwargs.get("notification_type") == "pbx_emergency_call"]


def test_numbers_come_from_settings_and_skip_malformed_entries(settings):
    settings.TELEPHONY_EMERGENCY_NUMBERS = ["112", " 911 ", "+964104", "0", "112", "abc"]
    assert emergency.numbers() == ["112", "911"]
    assert emergency.is_emergency("911") and not emergency.is_emergency("101") and not emergency.is_emergency("")


def test_an_extension_cannot_take_an_emergency_number(admin_client):
    resp = admin_client.post(f"{BASE}/extensions/", {"number": "112", "display_name": "Front desk"}, format="json")
    assert resp.status_code == 400, resp.content
    error = resp.json()["error"]
    assert error["fields"]["number"][0]["code"] == "validation.pbx_emergency_number"
    assert not PbxExtension.objects.filter(number="112").exists()
    # Renaming an existing extension onto one is refused too.
    ext = admin_client.post(f"{BASE}/extensions/", {"number": "120", "display_name": "Front desk"}, format="json")
    assert ext.status_code == 201, ext.content
    ext_id = (ext.json().get("data") or ext.json())["id"]
    resp = admin_client.patch(f"{BASE}/extensions/{ext_id}/", {"number": "104"}, format="json")
    assert resp.status_code == 400 and resp.json()["error"]["fields"]["number"][0]["code"] == "validation.pbx_emergency_number"


def test_an_emergency_call_is_outbound_alerts_the_admins_once_and_costs_no_minutes(node, setup, admin_user):  # noqa: F811
    events = emergency_call(node, setup["tenant"])
    with mock.patch("realtime.publish.publish_telephony_event"), \
            mock.patch("notifications.services.NotificationService.send_notification") as send, \
            mock.patch("subscriptions.entitlements.increment_monthly_usage") as minutes:
        node.push(*events)
        node.push(*events)  # a replayed batch: duplicates, no second alert
        node.push(emergency_call(node, setup["tenant"])[0])  # a late call.created with a new id: still one alert
    call = PbxCall.objects.get()
    assert call.direction == "outbound" and call.to_party == "112" and call.status == "answered"
    assert call.emergency_notified_at is not None
    sends = _alerts(send)
    # Once per call for each of the company's active admins (alerts.recipients: the owner and admin_user).
    users = [c.args[0] for c in sends]
    assert admin_user in users and len(users) == len(set(users)) == len(alert_recipients(admin_user.company_id))
    kwargs = sends[0].kwargs
    assert kwargs["skip_settings_check"] is True  # never muted by quiet hours or a per-type switch
    data = kwargs["data"]
    assert data["call_id"] == str(call.pk) and data["number"] == "⁨112⁩"
    assert data["caller"].endswith("(⁨101⁩)")
    minutes.assert_not_called()


def test_an_ordinary_internal_call_is_not_an_emergency(node, setup):  # noqa: F811
    events = emergency_call(node, setup["tenant"], number="100")
    with mock.patch("realtime.publish.publish_telephony_event"), \
            mock.patch("notifications.services.NotificationService.send_notification") as send:
        node.push(*events)
    call = PbxCall.objects.get()
    assert call.direction == "internal" and call.emergency_notified_at is None
    assert _alerts(send) == []


def test_usage_rollup_leaves_emergency_minutes_out(setup, company):  # noqa: F811
    from django.utils import timezone

    from telephony.services.usage import compute_day

    now = timezone.now()
    for i, (to, billsec) in enumerate((("112", 600), ("+9647801234567", 61))):
        PbxCall.objects.create(id=f"0b5cbbd0-5c5b-4f63-9d7e-00000000010{i}", company=company, tenant=setup["tenant"],
                               linked_id=f"u{i}", direction="outbound", status="answered", to_party=to,
                               billsec_s=billsec, started_at=now)
    day = compute_day(company.pk, timezone.localdate(now))
    assert day["calls_outbound"] == 2 and day["outbound_minutes"] == 2  # only the 61 s call: 2 started minutes


@pytest.mark.parametrize("lang,expected", [
    ("en", ("Emergency call", "Ali (101) dialled the emergency number 112.")),
    ("ar", ("مكالمة طوارئ", "اتصل Ali (101) برقم الطوارئ 112.")),
])
def test_alert_text_in_both_languages(lang, expected):
    text = get_notification_text("pbx_emergency_call", lang, caller="Ali (101)", number="112")
    assert (text["title"], text["body"]) == expected
