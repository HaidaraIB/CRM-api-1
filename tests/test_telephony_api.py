"""Tenant telephony API: gating, CRUD, secrets, quotas, recompiles."""

import pytest
from rest_framework.test import APIClient

from conftest import api_body
from telephony.models import PbxArtifact, PbxExtension, PbxNumber, PbxTenant
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"


# ---- gating ----------------------------------------------------------------


def test_plan_without_telephony_is_forbidden(admin_user, subscription):
    c = APIClient()
    c.force_authenticate(admin_user)
    assert c.get(f"{BASE}/status/").status_code == 403
    assert c.get(f"{BASE}/extensions/").status_code == 403


def test_kill_switch_blocks_telephony(admin_client, company):
    from settings.models import SystemSettings

    s = SystemSettings.get_settings()
    s.integration_policies = {"telephony": {"global_enabled": False, "global_message": "", "company_overrides": {}}}
    s.save()
    assert admin_client.get(f"{BASE}/status/").status_code == 403


def test_telephony_policy_defaults_to_off(company):
    from integrations.policy import get_effective_integration_policy

    policy = get_effective_integration_policy({}, company_id=company.id, platform="telephony")
    assert policy["enabled"] is False


def test_system_settings_cannot_globally_enable_telephony():
    from settings.serializers import SystemSettingsSerializer

    serializer = SystemSettingsSerializer()
    policies = serializer.validate_integration_policies(
        {
            "telephony": {
                "global_enabled": True,
                "global_message": "",
                "company_overrides": {},
            },
        },
    )

    assert policies["telephony"]["global_enabled"] is False


def test_admin_provision_requires_explicit_pilot_approval(
    company, pbx_node, superadmin_client, telephony_subscription,
):
    from settings.models import SystemSettings

    settings_obj = SystemSettings.get_settings()
    settings_obj.integration_policies = {
        "telephony": {"global_enabled": False, "global_message": "", "company_overrides": {}},
    }
    settings_obj.save(update_fields=["integration_policies"])

    response = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/provision/")

    assert response.status_code == 403
    assert api_body(response)["code"] == "telephony_not_approved"
    assert not PbxTenant.objects.filter(company=company).exists()


def test_admin_provision_allows_explicit_pilot_company(
    company, pbx_node, superadmin_client, telephony_subscription,
):
    from settings.models import SystemSettings

    settings_obj = SystemSettings.get_settings()
    settings_obj.integration_policies = {
        "telephony": {
            "global_enabled": False,
            "global_message": "",
            "company_overrides": {str(company.id): {"enabled": True, "message": ""}},
        },
    }
    settings_obj.save(update_fields=["integration_policies"])

    response = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/provision/")

    assert response.status_code == 202
    assert PbxTenant.objects.filter(company=company).exists()


def test_employees_cannot_read_pbx_config(employee_client):
    assert employee_client.get(f"{BASE}/trunks/").status_code == 403
    assert employee_client.get(f"{BASE}/extensions/").status_code == 403


def test_unauthenticated_is_rejected(db):
    assert APIClient().get(f"{BASE}/status/").status_code == 401


def test_status_before_and_after_provisioning(admin_client, company, pbx_node, superadmin_client):
    assert api_body(admin_client.get(f"{BASE}/status/")) == {"provisioned": False, "tenant": None}
    resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/provision/")
    assert resp.status_code == 202, resp.content
    body = api_body(admin_client.get(f"{BASE}/status/"))
    assert body["provisioned"] is True and body["tenant"]["status"] == "pending"
    assert body["tenant"]["config_version"] == 1


# ---- extensions ------------------------------------------------------------


def test_extension_secret_is_shown_once(admin_client, company):
    resp = admin_client.post(f"{BASE}/extensions/", {"number": "100", "display_name": "Ali"}, format="json")
    assert resp.status_code == 201, resp.content
    created = api_body(resp)
    assert created["sip_secret"] and len(created["sip_secret"]) >= 24
    listed = api_body(admin_client.get(f"{BASE}/extensions/"))
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert rows[0]["sip_secret"] is None
    detail = api_body(admin_client.get(f"{BASE}/extensions/{created['id']}/"))
    assert detail["sip_secret"] is None
    # Stored encrypted only.
    assert created["sip_secret"] not in PbxExtension.objects.get().sip_secret_encrypted


def test_rotate_returns_new_secret(admin_client, company):
    ext = api_body(admin_client.post(f"{BASE}/extensions/", {"number": "100"}, format="json"))
    rotated = api_body(admin_client.post(f"{BASE}/extensions/{ext['id']}/credentials/rotate/"))
    assert rotated["sip_secret"] and rotated["sip_secret"] != ext["sip_secret"]


@pytest.mark.parametrize("number", ["0", "1", "0100", "1234567", "10a", "loopcfg", ""])
def test_invalid_extension_numbers(admin_client, number):
    assert admin_client.post(f"{BASE}/extensions/", {"number": number}, format="json").status_code == 400


def test_duplicate_extension_and_one_per_user(admin_client, admin_user):
    assert admin_client.post(f"{BASE}/extensions/", {"number": "100", "user": admin_user.id}, format="json").status_code == 201
    assert admin_client.post(f"{BASE}/extensions/", {"number": "100"}, format="json").status_code == 400
    assert admin_client.post(f"{BASE}/extensions/", {"number": "101", "user": admin_user.id}, format="json").status_code == 400


def test_extension_quota(admin_client):
    for n in range(200, 205):  # clear of the emergency numbers (104, 112, 115, 122)
        assert admin_client.post(f"{BASE}/extensions/", {"number": str(n)}, format="json").status_code == 201
    resp = admin_client.post(f"{BASE}/extensions/", {"number": "205"}, format="json")
    assert resp.status_code in (400, 403)
    assert "max_pbx_extensions" in resp.content.decode()


def test_display_name_is_sanitised(admin_client):
    ext = api_body(admin_client.post(f"{BASE}/extensions/", {"number": "100", "display_name": 'A"<b>;\n'}, format="json"))
    assert ext["display_name"] == "Ab"


# ---- trunks / numbers / routes ---------------------------------------------


def _trunk(c, **kw):
    body = {"name": "Carrier", "host": "sip.carrier.example", "allowed_sources": ["192.0.2.0/24"], **kw}
    resp = c.post(f"{BASE}/trunks/", body, format="json")
    assert resp.status_code == 201, resp.content
    return api_body(resp)


def test_trunk_secret_is_write_only(admin_client):
    trunk = _trunk(admin_client, auth_username="acct", auth_secret="s" * 20)
    assert "auth_secret" not in trunk and trunk["has_auth_secret"] is True
    assert trunk["status"] == "pending"


@pytest.mark.parametrize(
    "field,value",
    [("host", "evil.example\n[x]"), ("host", "a b"), ("allowed_sources", ["not-an-ip"]),
     ("auth_secret", "short"), ("auth_secret", "has spaces in it ok"), ("port", 70000)],
)
def test_trunk_validation(admin_client, field, value):
    body = {"name": "T", "host": "sip.example", "allowed_sources": [], field: value}
    assert admin_client.post(f"{BASE}/trunks/", body, format="json").status_code == 400


def test_trunk_status_cannot_be_self_verified(admin_client):
    trunk = _trunk(admin_client)
    admin_client.patch(f"{BASE}/trunks/{trunk['id']}/", {"status": "ready"}, format="json")
    assert api_body(admin_client.get(f"{BASE}/trunks/{trunk['id']}/"))["status"] == "pending"


def test_number_verification_is_operator_only(admin_client, superadmin_client):
    trunk = _trunk(admin_client)
    num = api_body(admin_client.post(f"{BASE}/numbers/", {"e164": "+964 770 000 0001", "trunk": trunk["id"]}, format="json"))
    assert num["e164"] == "+9647700000001" and num["verified"] is False
    admin_client.patch(f"{BASE}/numbers/{num['id']}/", {"verified_at": "2026-01-01T00:00:00Z"}, format="json")
    assert PbxNumber.objects.get().verified_at is None
    assert admin_client.post(f"/api/v1/admin/telephony/numbers/{num['id']}/verify/").status_code == 403
    assert superadmin_client.post(f"/api/v1/admin/telephony/numbers/{num['id']}/verify/").status_code == 200
    assert PbxNumber.objects.get().verified_at is not None


def test_changing_number_drops_verification(admin_client, superadmin_client):
    trunk = _trunk(admin_client)
    num = api_body(admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000001", "trunk": trunk["id"]}, format="json"))
    superadmin_client.post(f"/api/v1/admin/telephony/numbers/{num['id']}/verify/")
    admin_client.patch(f"{BASE}/numbers/{num['id']}/", {"e164": "+9647700000002"}, format="json")
    assert PbxNumber.objects.get().verified_at is None


def test_outbound_route_requires_verified_caller_id(admin_client, superadmin_client):
    trunk = _trunk(admin_client)
    num = api_body(admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000001", "trunk": trunk["id"]}, format="json"))
    body = {"name": "Iraq mobile", "patterns": [{"prefix": "+9647", "digits": 9}], "trunk": trunk["id"], "caller_id": num["id"]}
    assert admin_client.post(f"{BASE}/outbound-routes/", body, format="json").status_code == 400
    superadmin_client.post(f"/api/v1/admin/telephony/numbers/{num['id']}/verify/")
    assert admin_client.post(f"{BASE}/outbound-routes/", body, format="json").status_code == 201


@pytest.mark.parametrize(
    "patterns",
    [[], [{"prefix": "9647", "digits": 9}], [{"prefix": "+9647", "digits": 20}], [{"prefix": "+9647X", "digits": 1}],
     [{"prefix": "+1", "digits": 1}], ["_+X."]],
)
def test_outbound_patterns_are_typed(admin_client, superadmin_client, patterns):
    trunk = _trunk(admin_client)
    num = api_body(admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000001", "trunk": trunk["id"]}, format="json"))
    superadmin_client.post(f"/api/v1/admin/telephony/numbers/{num['id']}/verify/")
    body = {"name": "R", "patterns": patterns, "trunk": trunk["id"], "caller_id": num["id"]}
    assert admin_client.post(f"{BASE}/outbound-routes/", body, format="json").status_code == 400


def test_trunk_in_use_cannot_be_deleted(admin_client):
    trunk = _trunk(admin_client)
    admin_client.post(f"{BASE}/numbers/", {"e164": "+9647700000001", "trunk": trunk["id"]}, format="json")
    resp = admin_client.delete(f"{BASE}/trunks/{trunk['id']}/")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "in_use"


def test_trunk_quota(admin_client):
    _trunk(admin_client, name="A")
    _trunk(admin_client, name="B")
    resp = admin_client.post(f"{BASE}/trunks/", {"name": "C", "host": "h.example", "allowed_sources": []}, format="json")
    assert resp.status_code in (400, 403) and "max_pbx_trunks" in resp.content.decode()


# ---- recompile on change ---------------------------------------------------


def test_config_changes_recompile_once_provisioned(admin_client, company, pbx_node, superadmin_client):
    admin_client.post(f"{BASE}/extensions/", {"number": "100"}, format="json")
    assert not PbxArtifact.objects.exists()  # not provisioned yet: nothing compiles

    superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/provision/")
    tenant = PbxTenant.objects.get(company=company)
    assert tenant.config_version == 1

    ext = api_body(admin_client.post(f"{BASE}/extensions/", {"number": "101"}, format="json"))
    admin_client.patch(f"{BASE}/extensions/{ext['id']}/", {"display_name": "Desk"}, format="json")
    admin_client.delete(f"{BASE}/extensions/{ext['id']}/")
    tenant.refresh_from_db()
    assert tenant.config_version == 4
    latest = PbxArtifact.objects.get(tenant=tenant, version=4)
    assert [f["name"] for f in latest.manifest["files"]] == [f"extensions_{tenant.tag}.conf", f"pjsip_{tenant.tag}.conf"]


# ---- settings: call recording ----------------------------------------------


def test_settings_need_a_provisioned_tenant(admin_client):
    assert admin_client.get(f"{BASE}/settings/").status_code == 404


def test_recording_is_off_by_default(admin_client, tenant_a):
    body = api_body(admin_client.get(f"{BASE}/settings/"))
    assert body["recording_mode"] == "off" and body["recording_accepted_at"] is None


def test_turning_recording_on_requires_attestation(admin_client, admin_user, tenant_a):
    from settings.models import SystemAuditLog

    resp = admin_client.patch(f"{BASE}/settings/", {"recording_mode": "external"}, format="json")
    assert resp.status_code == 400 and "accept_recording_terms" in resp.content.decode()
    tenant_a.refresh_from_db()
    assert tenant_a.recording_mode == "off" and tenant_a.config_version == 0

    resp = admin_client.patch(
        f"{BASE}/settings/", {"recording_mode": "external", "accept_recording_terms": True}, format="json"
    )
    assert resp.status_code == 200, resp.content
    body = api_body(resp)
    assert body["recording_mode"] == "external" and body["recording_accepted_by"] == admin_user.pk
    tenant_a.refresh_from_db()
    assert tenant_a.config_version == 1  # recompiled
    import gzip
    import io
    import tarfile

    from telephony.services.provisioning import artifact_blob

    blob = artifact_blob(PbxArtifact.objects.get(tenant=tenant_a, version=1))
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(blob))) as tar:
        dialplan = tar.extractfile(f"extensions_{tenant_a.tag}.conf").read().decode()
    assert "MixMonitor(" in dialplan
    log = SystemAuditLog.objects.get(action="telephony.recording_settings_changed")
    assert log.actor == admin_user and log.metadata["after"]["recording_mode"] == "external"

    # Changing the notice or turning it off needs no new attestation.
    assert admin_client.patch(f"{BASE}/settings/", {"recording_notice": "ar"}, format="json").status_code == 200
    assert admin_client.patch(f"{BASE}/settings/", {"recording_mode": "off"}, format="json").status_code == 200
    tenant_a.refresh_from_db()
    assert tenant_a.config_version == 3 and tenant_a.recording_accepted_by == admin_user


def test_unchanged_settings_do_not_recompile(admin_client, tenant_a):
    assert admin_client.patch(f"{BASE}/settings/", {"recording_mode": "off"}, format="json").status_code == 200
    tenant_a.refresh_from_db()
    assert tenant_a.config_version == 0


@pytest.mark.parametrize("payload", [{"recording_mode": "everything"}, {"recording_notice": "fr"}])
def test_invalid_recording_settings(admin_client, tenant_a, payload):
    assert admin_client.patch(f"{BASE}/settings/", payload, format="json").status_code == 400


def test_employees_cannot_change_recording(employee_client, tenant_a):
    resp = employee_client.patch(
        f"{BASE}/settings/", {"recording_mode": "all", "accept_recording_terms": True}, format="json"
    )
    assert resp.status_code == 403


# ---- platform admin --------------------------------------------------------


def test_node_registration_returns_tokens_once(superadmin_client):
    resp = superadmin_client.post("/api/v1/admin/telephony/nodes/", {"name": "pbx-9", "region": "iq"}, format="json")
    assert resp.status_code == 201
    node = api_body(resp)
    assert node["node_token"].startswith("lpn_") and node["crm_token"].startswith("lpn_")
    listed = api_body(superadmin_client.get("/api/v1/admin/telephony/nodes/"))
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert "node_token" not in rows[0] and "node_token_hash" not in rows[0]


def test_tenant_admins_cannot_use_platform_endpoints(admin_client, company):
    assert admin_client.get("/api/v1/admin/telephony/nodes/").status_code == 403
    assert admin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/provision/").status_code == 403


def test_admin_suspend_resume(superadmin_client, company, pbx_node, telephony_subscription):
    superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/provision/")
    resp = superadmin_client.post(f"/api/v1/admin/telephony/tenants/{company.id}/suspend/", {"reason": "fraud"}, format="json")
    assert resp.status_code == 202
    tenant = PbxTenant.objects.get(company=company)
    assert tenant.outbound_suspended and tenant.suspended_reason == "fraud"
    jobs = api_body(superadmin_client.get(f"/api/v1/admin/telephony/tenants/{company.id}/jobs/"))
    assert jobs[0]["kind"] == "suspend"
