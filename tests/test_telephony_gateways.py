"""
GSM gateway onboarding (spec section 5; CRM-pbx contract section 5, gateway peers):
one-time enrollment token, pinned tunnel address, node peers in desired-state,
READY only after the node reports the gateway reachable.
"""

import base64
import os
from datetime import timedelta

import pytest
from django.utils import timezone

from conftest import api_body
from telephony.models import PbxGatewayPeer, PbxNode, PbxTenant, PbxTrunk
from telephony.services import gateways
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"
ENROLL = "/api/v1/public/telephony/gateways/enroll/"
NODE_KEY = base64.b64encode(b"n" * 32).decode()


def _key():
    return base64.b64encode(os.urandom(32)).decode()


def _code(resp):
    return resp.json()["error"]["code"]


@pytest.fixture
def live(tenant_a, pbx_node):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1)
    PbxNode.objects.filter(pk=pbx_node.pk).update(gateway_wg_public_key=NODE_KEY, gateway_wg_endpoint="pbx-1.example.com:51821")
    tenant_a.refresh_from_db()
    return tenant_a


def _create(client, name="Dinstar office"):
    resp = client.post(f"{BASE}/gateways/", {"name": name}, format="json")
    assert resp.status_code == 201, resp.content
    return api_body(resp)


def _desired(pbx_node):
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {pbx_node.plain_token}")
    return client.get(f"/api/v1/internal/telephony/nodes/{pbx_node.id}/desired-state").json()


def test_adding_a_gateway_pins_an_address_and_returns_the_token_once(admin_client, live):
    first, second = _create(admin_client), _create(admin_client, "Dinstar branch")
    assert first["enrollment_token"].startswith("lpg_") and first["enrollment_url"].endswith("/telephony/gateways/enroll/")
    assert (first["tunnel_ip"], second["tunnel_ip"]) == ("10.78.0.2", "10.78.0.3")
    assert first["status"] == "awaiting_enrollment" and first["trunk_status"] == "pending"
    trunk = PbxTrunk.objects.get(pk=first["trunk"])
    assert (trunk.kind, trunk.host, trunk.allowed_sources) == ("gsm_gateway", "10.78.0.2", ["10.78.0.2/32"])
    # Only the hash is kept, and listing never shows a token.
    peer = PbxGatewayPeer.objects.get(pk=first["id"])
    assert peer.enrollment_token_hash == gateways.hash_token(first["enrollment_token"])
    listed = api_body(admin_client.get(f"{BASE}/gateways/"))
    assert all("enrollment_token" not in row for row in (listed.get("results", listed)))
    # The gateway trunk is compiled, qualified and identified by its tunnel address.
    from telephony.services.compiler import compile_tenant

    pjsip = compile_tenant(live).files["pjsip_tdev4test.conf"]
    assert "contact = sip:10.78.0.2:5060" in pjsip and "match = 10.78.0.2/32" in pjsip and "qualify_frequency = 30" in pjsip


def test_gateway_needs_a_provisioned_tenant(admin_client, telephony_subscription):
    resp = admin_client.post(f"{BASE}/gateways/", {"name": "Dinstar"}, format="json")
    assert resp.status_code == 409 and _code(resp) == "not_provisioned"


def test_gateway_counts_toward_the_trunk_quota(admin_client, live):
    _create(admin_client, "A")
    _create(admin_client, "B")  # telephony_plan: max_pbx_trunks = 2
    resp = admin_client.post(f"{BASE}/gateways/", {"name": "C"}, format="json")
    assert resp.status_code in (400, 403) and "max_pbx_trunks" in resp.content.decode()  # same as plain trunks
    assert not PbxTrunk.objects.filter(name="C").exists()


def test_enrollment_spends_the_token_and_returns_the_settings(admin_client, api_client, live, pbx_node):
    gw = _create(admin_client)
    assert _desired(pbx_node)["gateway_peers"] == []  # nothing to add before enrollment
    key = _key()
    resp = api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": key}, format="json")
    assert resp.status_code == 200, resp.content
    body = api_body(resp)
    assert body["tunnel_address"] == "10.78.0.2/32" and body["sip"] == {"server": "10.78.0.1", "port": 5060, "transport": "udp"}
    assert body["wireguard"]["peer_public_key"] == NODE_KEY and body["wireguard"]["allowed_ips"] == "10.78.0.1/32"
    assert "Endpoint = pbx-1.example.com:51821" in body["wireguard"]["config"]
    peer = PbxGatewayPeer.objects.get(pk=gw["id"])
    assert (peer.status, peer.public_key, peer.enrollment_token_hash) == ("enrolled", key, "")
    assert _desired(pbx_node)["gateway_peers"] == [{"public_key": key, "allowed_ip": "10.78.0.2/32"}]
    # Spent: the same token never works twice.
    again = api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    assert again.status_code == 400 and _code(again) == "invalid_token"


@pytest.mark.parametrize("token,key,code", [
    ("lpg_wrong", None, "invalid_token"),
    ("not-a-token", None, "invalid_token"),
    (None, "short", "invalid_public_key"),
    (None, "!" * 44, "invalid_public_key"),
])
def test_enrollment_refusals(admin_client, api_client, live, token, key, code):
    gw = _create(admin_client)
    resp = api_client.post(ENROLL, {"token": token or gw["enrollment_token"], "public_key": key or _key()}, format="json")
    assert resp.status_code == 400 and _code(resp) == code
    assert PbxGatewayPeer.objects.get(pk=gw["id"]).status == "awaiting_enrollment"


def test_expired_token(admin_client, api_client, live):
    gw = _create(admin_client)
    PbxGatewayPeer.objects.filter(pk=gw["id"]).update(token_expires_at=timezone.now() - timedelta(minutes=1))
    resp = api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    assert resp.status_code == 400 and _code(resp) == "invalid_token"


def test_enrollment_waits_for_the_nodes_gateway_link(admin_client, api_client, live, pbx_node):
    gw = _create(admin_client)
    PbxNode.objects.filter(pk=pbx_node.pk).update(gateway_wg_public_key="")
    resp = api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    assert resp.status_code == 503 and _code(resp) == "gateway_link_not_configured"
    PbxNode.objects.filter(pk=pbx_node.pk).update(gateway_wg_public_key=NODE_KEY)
    resp = api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    assert resp.status_code == 200  # the token was kept


def test_enrollment_is_throttled(admin_client, api_client, live):
    _create(admin_client)
    codes = [api_client.post(ENROLL, {"token": "lpg_guess", "public_key": _key()}, format="json").status_code
             for _ in range(7)]
    assert 429 in codes


def test_verify_needs_enrollment_and_reachability(admin_client, api_client, live, pbx_node):
    gw = _create(admin_client)
    resp = admin_client.post(f"{BASE}/gateways/{gw['id']}/verify/")
    assert resp.status_code == 409 and _code(resp) == "not_enrolled"
    api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    resp = admin_client.post(f"{BASE}/gateways/{gw['id']}/verify/")
    assert resp.status_code == 409 and _code(resp) == "gateway_unreachable"

    # The node qualifies the gateway trunk and reports it by endpoint name.
    from tests.test_telephony_calls import Node

    node = Node(pbx_node)
    trunk = PbxTrunk.objects.get(pk=gw["trunk"])
    endpoint = f"{live.tag}_trk_{str(trunk.id).replace('-', '')[:8]}"
    result = node.push(node.event("gateway.status_changed", live, {"trunk": endpoint, "status": "ready"}))
    assert len(result["accepted"]) == 1, result
    resp = admin_client.post(f"{BASE}/gateways/{gw['id']}/verify/")
    assert resp.status_code == 200, resp.content
    body = api_body(resp)
    assert (body["status"], body["trunk_status"], body["health"]) == ("verified", "ready", "ready")


def test_gateway_status_for_another_tenants_trunk_is_rejected(admin_client, live, pbx_node, other_company, tenant_b):
    from tests.test_telephony_calls import Node

    node = Node(pbx_node)
    result = node.push(node.event("gateway.status_changed", live, {"trunk": "tacme2pbx_trk_1a2b3c4d", "status": "down"}))
    assert len(result["rejected"]) == 1


def test_reset_issues_a_new_token_and_drops_the_peer(admin_client, api_client, live, pbx_node):
    gw = _create(admin_client)
    api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    resp = admin_client.post(f"{BASE}/gateways/{gw['id']}/enrollment/reset/")
    assert resp.status_code == 200, resp.content
    body = api_body(resp)
    assert body["status"] == "awaiting_enrollment" and body["enrollment_token"] != gw["enrollment_token"]
    assert _desired(pbx_node)["gateway_peers"] == []
    assert api_client.post(ENROLL, {"token": body["enrollment_token"], "public_key": _key()}, format="json").status_code == 200


def test_delete_gateway(admin_client, api_client, live, pbx_node, make_number):
    gw = _create(admin_client)
    api_client.post(ENROLL, {"token": gw["enrollment_token"], "public_key": _key()}, format="json")
    number = make_number(live.company, PbxTrunk.objects.get(pk=gw["trunk"]), "+9647700000009")
    resp = admin_client.delete(f"{BASE}/gateways/{gw['id']}/")
    assert resp.status_code == 409 and _code(resp) == "in_use"
    number.delete()
    assert admin_client.delete(f"{BASE}/gateways/{gw['id']}/").status_code == 204
    assert not PbxTrunk.objects.filter(pk=gw["trunk"]).exists() and _desired(pbx_node)["gateway_peers"] == []
    # The freed address is reused.
    assert _create(admin_client, "Replacement")["tunnel_ip"] == "10.78.0.2"


def test_other_tenants_gateway_is_404(admin_client, other_admin_client, live, other_company, tenant_b):
    gw = _create(admin_client)
    for method, path in (("get", ""), ("post", "verify/"), ("post", "enrollment/reset/"), ("delete", "")):
        resp = getattr(other_admin_client, method)(f"{BASE}/gateways/{gw['id']}/{path}")
        assert resp.status_code == 404, (method, path)
    assert PbxGatewayPeer.objects.filter(pk=gw["id"]).exists()


def test_gateway_trunk_address_is_pinned(admin_client, live):
    gw = _create(admin_client)
    url = f"{BASE}/trunks/{gw['trunk']}/"
    assert api_body(admin_client.get(url))["gateway"] == gw["id"]
    for change in ({"host": "203.0.113.9"}, {"allowed_sources": ["0.0.0.0/0"]}, {"kind": "sip"}, {"port": 5070}):
        assert admin_client.patch(url, change, format="json").status_code == 400, change
    trunk = PbxTrunk.objects.get(pk=gw["trunk"])
    assert (trunk.host, trunk.allowed_sources, trunk.kind) == ("10.78.0.2", ["10.78.0.2/32"], "gsm_gateway")
    # The name is still the tenant's to change.
    assert admin_client.patch(url, {"name": "Dinstar lobby"}, format="json").status_code == 200
