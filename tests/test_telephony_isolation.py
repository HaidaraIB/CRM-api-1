"""
Tenant isolation for the telephony API (spec §12 acceptance; CRM-pbx ADR 0002).

Tenant A must not be able to read, change, delete, or *reference* Tenant B's
extensions, trunks, numbers or routes; both tenants may own extension 100; and
every telephony route must be covered here (``test_every_route_is_covered``).
"""

import pytest
from django.urls import get_resolver

from conftest import api_body
from telephony.models import PbxExtension, PbxInboundRoute, PbxOutboundRoute
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"

# Every tenant resource and how to make one for company B.
RESOURCES = [
    "extensions", "trunks", "numbers", "inbound-routes", "outbound-routes", "business-hours", "ivr-menus", "queues",
]


@pytest.fixture
def b_rows(other_company, make_trunk, make_number, make_extension):
    trunk = make_trunk(other_company, name="B carrier", host="198.51.100.7")
    number = make_number(other_company, trunk, "+9647800000009")
    ext = make_extension(other_company, "100", display_name="B agent")
    inbound = PbxInboundRoute.objects.create(company=other_company, number=number, extension=ext)
    outbound = PbxOutboundRoute.objects.create(
        company=other_company, name="B route", patterns=[{"prefix": "+9647", "digits": 9}], trunk=trunk, caller_id=number
    )
    from telephony.models import PbxBusinessHours, PbxIvrMenu, PbxQueue, PbxQueueMember

    hours = PbxBusinessHours.objects.create(company=other_company, name="B hours",
                                            rules=[{"days": [0], "start": "09:00", "end": "17:00"}])
    queue = PbxQueue.objects.create(company=other_company, name="B queue")
    PbxQueueMember.objects.create(company=other_company, queue=queue, extension=ext)
    menu = PbxIvrMenu.objects.create(company=other_company, name="B menu")
    return {"extensions": ext, "trunks": trunk, "numbers": number, "inbound-routes": inbound, "outbound-routes": outbound,
            "business-hours": hours, "ivr-menus": menu, "queues": queue}


def _rows(body):
    return body["results"] if isinstance(body, dict) and "results" in body else body


@pytest.mark.parametrize("resource", RESOURCES)
def test_list_never_shows_other_tenant(admin_client, other_admin_client, b_rows, resource):
    assert _rows(api_body(admin_client.get(f"{BASE}/{resource}/"))) == []
    assert len(_rows(api_body(other_admin_client.get(f"{BASE}/{resource}/")))) == 1


@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize("method", ["get", "patch", "put", "delete"])
def test_detail_of_other_tenant_is_404(admin_client, b_rows, resource, method):
    url = f"{BASE}/{resource}/{b_rows[resource].pk}/"
    resp = getattr(admin_client, method)(url, {}, format="json")
    assert resp.status_code == 404


def test_other_tenant_rows_survive_attempts(admin_client, b_rows):
    for resource, row in b_rows.items():
        admin_client.delete(f"{BASE}/{resource}/{row.pk}/")
        admin_client.patch(f"{BASE}/{resource}/{row.pk}/", {"name": "pwned", "number": "999"}, format="json")
    assert PbxExtension.objects.get(pk=b_rows["extensions"].pk).number == "100"
    assert PbxOutboundRoute.objects.get(pk=b_rows["outbound-routes"].pk).name == "B route"


def test_rotate_other_tenant_extension_is_404(admin_client, b_rows):
    url = f"{BASE}/extensions/{b_rows['extensions'].pk}/credentials/rotate/"
    assert admin_client.post(url).status_code == 404


def test_cannot_reference_other_tenant_objects(admin_client, company, b_rows, make_trunk, make_number, make_extension):
    own_trunk = make_trunk(company)
    own_number = make_number(company, own_trunk, "+9647700000001")
    own_ext = make_extension(company, "101")

    # Number on B's trunk.
    r = admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000005", "trunk": str(b_rows["trunks"].pk)}, format="json")
    assert r.status_code == 400
    # Inbound route from B's number, or to B's extension.
    r = admin_client.post(f"{BASE}/inbound-routes/", {"number": str(b_rows["numbers"].pk), "extension": str(own_ext.pk)}, format="json")
    assert r.status_code == 400
    r = admin_client.post(f"{BASE}/inbound-routes/", {"number": str(own_number.pk), "extension": str(b_rows["extensions"].pk)}, format="json")
    assert r.status_code == 400
    # Outbound route over B's trunk, or presenting B's number (CLI spoofing).
    pattern = [{"prefix": "+9647", "digits": 9}]
    r = admin_client.post(f"{BASE}/outbound-routes/", {"name": "x", "patterns": pattern, "trunk": str(b_rows["trunks"].pk), "caller_id": str(own_number.pk)}, format="json")
    assert r.status_code == 400
    r = admin_client.post(f"{BASE}/outbound-routes/", {"name": "y", "patterns": pattern, "trunk": str(own_trunk.pk), "caller_id": str(b_rows["numbers"].pk)}, format="json")
    assert r.status_code == 400
    # Assigning B's user to an extension.
    from accounts.models import User

    b_user = User.objects.filter(company=b_rows["extensions"].company).first()
    r = admin_client.post(f"{BASE}/extensions/", {"number": "102", "user": b_user.pk}, format="json")
    assert r.status_code == 400


def test_foreign_reference_errors_do_not_reveal_existence(admin_client, b_rows):
    """A real foreign id and a random id must produce the same error."""
    import uuid

    foreign_id, random_id = str(b_rows["trunks"].pk), str(uuid.uuid4())
    foreign = admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000005", "trunk": foreign_id}, format="json")
    missing = admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000005", "trunk": random_id}, format="json")
    assert foreign.status_code == missing.status_code == 400
    assert foreign.content.decode().replace(foreign_id, "ID") == missing.content.decode().replace(random_id, "ID")


def test_did_is_globally_unique_with_neutral_message(admin_client, company, b_rows, make_trunk):
    own = make_trunk(company)
    r = admin_client.post(f"{BASE}/numbers/", {"e164": "+9647800000009", "trunk": str(own.pk)}, format="json")
    assert r.status_code == 400 and "already registered" in r.content.decode()


def test_extension_100_in_both_tenants(admin_client, b_rows):
    assert admin_client.post(f"{BASE}/extensions/", {"number": "100"}, format="json").status_code == 201
    assert PbxExtension.objects.filter(number="100").count() == 2


def test_company_in_body_is_ignored(admin_client, company, other_company):
    r = admin_client.post(f"{BASE}/extensions/", {"number": "100", "company": other_company.id}, format="json")
    assert r.status_code == 201
    assert PbxExtension.objects.get().company_id == company.id


# Where each telephony route's tenant/node isolation is tested. Adding a route
# without adding it here (and a negative test) fails test_every_route_is_covered.
COVERED_ROUTES = {
    # tenant resources: list/detail/actions above (RESOURCES, rotate)
    "pbx-extension-list", "pbx-extension-detail", "pbx-extension-rotate-credentials",
    "pbx-trunk-list", "pbx-trunk-detail",
    "pbx-number-list", "pbx-number-detail",
    "pbx-inbound-route-list", "pbx-inbound-route-detail",
    "pbx-outbound-route-list", "pbx-outbound-route-detail",
    # custom greetings: test_telephony_greetings (another tenant's greeting 404 / not usable)
    "pbx-sound-list", "pbx-sound-detail", "pbx-sound-audio",
    # trunk test: test_telephony_trunk_test (another tenant's trunk is 404 and never probed)
    "pbx-trunk-test",
    # wallboard: test_telephony_wallboard (queue counts per company; live calls through visible_calls)
    "pbx-wallboard",
    # Phase 5: test_telephony_saas (recording delete: other tenant 404, admins only; usage: session company)
    "pbx-recording-delete", "pbx-usage",
    # platform console (Phase 5): test_telephony_migration (platform only; tenants by company id)
    "pbx-admin-number-list", "pbx-admin-fraud-alert-list", "pbx-admin-fraud-alert-resolve", "pbx-admin-health",
    "pbx-admin-tenant-migrate-node", "pbx-admin-tenant-repush",
    # tenant status: test_telephony_api gating tests (company from the session only)
    "pbx-status",
    # tenant settings: company from the session only; test_settings_change_only_own_tenant below
    "pbx-settings",
    # platform admin: test_tenant_admins_cannot_use_platform_endpoints + below
    "pbx-admin-node-list", "pbx-admin-node-detail", "pbx-admin-node-rotate-tokens",
    "pbx-admin-tenant-list", "pbx-admin-tenant-detail", "pbx-admin-tenant-jobs",
    "pbx-admin-tenant-provision", "pbx-admin-tenant-suspend", "pbx-admin-tenant-resume",
    "pbx-admin-number-verify",
    # node-only: test_telephony_provisioning node-auth tests
    "pbx-internal-desired-state", "pbx-internal-artifact", "pbx-internal-apply-result",
    # calls (Phase 2): test_calls_recordings_and_events_of_other_tenant_are_404 below,
    # plus test_telephony_calls (visibility, node scoping, signed stream tokens)
    "pbx-call-list", "pbx-call-detail", "pbx-call-recordings",
    "pbx-event-list", "pbx-event-detail", "pbx-recording-access", "pbx-recording-stream",
    "pbx-internal-events", "pbx-internal-recordings",
    # Phase 3: test_telephony_click_to_call (foreign leads 404, other tenant's hangup 404,
    # credentials only for the caller's own extension; "me" is the session user only)
    "pbx-call-hangup", "pbx-me", "pbx-me-webrtc-credentials",
    # agent status: the caller's own extension only; presence lists the session company
    # only (test_presence_lists_the_company_agents, test_presence_is_company_scoped below)
    "pbx-agents-status", "pbx-agents-me-status",
    # routing (Phase 4): RESOURCES list/detail tests above, foreign references in
    # test_telephony_routing (queue members/overflow, IVR options), members below
    "pbx-business-hours-list", "pbx-business-hours-detail",
    "pbx-ivr-menu-list", "pbx-ivr-menu-detail",
    "pbx-queue-list", "pbx-queue-detail", "pbx-queue-members",
    # gateways: test_telephony_gateways (other tenant's gateway 404 on every action; the
    # public enroll endpoint is authorised by the one-time token alone, and spends it)
    "pbx-gateway-list", "pbx-gateway-detail", "pbx-gateway-verify", "pbx-gateway-reset-enrollment",
    "pbx-gateway-enroll",
}


def test_members_of_other_tenants_queue_is_404(admin_client, b_rows):
    url = f"{BASE}/queues/{b_rows['queues'].pk}/members/"
    assert admin_client.put(url, {"members": []}, format="json").status_code == 404
    assert b_rows["queues"].members.count() == 1


def test_presence_is_company_scoped(admin_client, other_admin_client, company, other_company, make_extension):
    make_extension(company, "100", display_name="A agent")
    make_extension(other_company, "100", display_name="B agent")
    for client, name in ((admin_client, "A agent"), (other_admin_client, "B agent")):
        agents = api_body(client.get(f"{BASE}/agents/status/"))["agents"]
        assert [a["display_name"] for a in agents] == [name]


def test_settings_change_only_own_tenant(admin_client, tenant_a, tenant_b, other_company, recording_allowed):
    resp = admin_client.patch(
        f"{BASE}/settings/",
        {"recording_mode": "all", "accept_recording_terms": True, "company": other_company.id},
        format="json",
    )
    assert resp.status_code == 200
    tenant_a.refresh_from_db()
    tenant_b.refresh_from_db()
    assert tenant_a.recording_mode == "all" and tenant_b.recording_mode == "off"
    assert tenant_b.config_version == 0


def test_every_route_is_covered():
    names = {k for k in get_resolver().reverse_dict.keys() if isinstance(k, str) and k.startswith("pbx-")}
    assert names - COVERED_ROUTES == set(), "add isolation coverage for these routes"
    assert COVERED_ROUTES - names == set(), "stale entries in COVERED_ROUTES"


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/api/v1/admin/telephony/nodes/"),
        ("get", "/api/v1/admin/telephony/tenants/"),
        ("post", "/api/v1/admin/telephony/tenants/{company}/suspend/"),
        ("post", "/api/v1/admin/telephony/tenants/{company}/resume/"),
        ("get", "/api/v1/admin/telephony/tenants/{company}/jobs/"),
    ],
)
def test_platform_routes_reject_tenant_admins(admin_client, other_company, method, url):
    resp = getattr(admin_client, method)(url.format(company=other_company.id))
    assert resp.status_code == 403


def test_calls_recordings_and_events_of_other_tenant_are_404(admin_client, other_company, tenant_b):
    """Tenant A cannot read B's calls, call recordings, recording URLs or events."""
    import uuid as _uuid

    from django.utils import timezone

    from telephony.models import PbxCall, PbxCallEvent, PbxRecording

    call = PbxCall.objects.create(id=_uuid.uuid4(), company=other_company, tenant=tenant_b, linked_id="1.1",
                                  direction="inbound", status="answered", started_at=timezone.now())
    rec = PbxRecording.objects.create(
        id=_uuid.uuid4(), company=other_company, tenant=tenant_b, call=call, call_uuid=call.id,
        storage_key=f"telephony/{other_company.id}/2026/10/{call.id}/x.ogg", size_bytes=1, sha256="a" * 64,
        duration_s=1, format="ogg", started_at=timezone.now(),
    )
    event = PbxCallEvent.objects.create(id=_uuid.uuid4(), company=other_company, tenant=tenant_b, call=call,
                                        type="call.created", seq=1, occurred_at=timezone.now(), data={})
    assert admin_client.get(f"{BASE}/calls/{call.id}/").status_code == 404
    assert admin_client.get(f"{BASE}/calls/{call.id}/recordings/").status_code == 404
    assert admin_client.post(f"{BASE}/recordings/{rec.id}/access/").status_code == 404
    assert admin_client.get(f"{BASE}/events/{event.id}/").status_code == 404
    assert api_body(admin_client.get(f"{BASE}/calls/"))["results"] == []
    assert api_body(admin_client.get(f"{BASE}/events/"))["results"] == []
