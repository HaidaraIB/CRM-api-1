"""
telephony.contract reproduces the CRM-pbx contract vectors exactly.

tests/fixtures/pbx_contract/*.json are copies of CRM-pbx/contract/vectors; the
node side runs the same vectors against its reference implementation.
"""

import json
from pathlib import Path

import pytest

from telephony import contract

VECTORS = Path(__file__).resolve().parent / "fixtures" / "pbx_contract"


def _load(name):
    return json.loads((VECTORS / f"{name}.json").read_text(encoding="utf-8"))


NAMING = _load("naming")
CREDS = _load("credentials")
CALL_IDS = _load("call-ids")


@pytest.mark.parametrize("case", NAMING["valid"], ids=lambda c: c["realm_key"])
def test_naming_vectors(case):
    rk, inputs = case["realm_key"], NAMING["inputs"]
    assert contract.tenant_tag(rk) == case["tenant_tag"]
    assert contract.extension_endpoint(rk, "100") == case["extension_endpoint_100"]
    assert contract.extension_auth(rk, "100") == case["extension_auth_100"]
    assert contract.trunk_endpoint(rk, inputs["trunk_id"]) == case["trunk_endpoint"]
    assert contract.context_internal(rk) == case["context_internal"]
    assert contract.context_from_agent(rk) == case["context_from_agent"]
    assert contract.context_from_trunk(rk) == case["context_from_trunk"]
    assert contract.context_ivr(rk, inputs["ivr_id"]) == case["context_ivr"]
    assert contract.queue_name(rk, inputs["queue_id"]) == case["queue_name"]
    assert contract.agent_device(rk, "100") == case["agent_device_100"]
    assert contract.context_queue(rk, inputs["queue_id"]) == case["context_queue"]
    assert contract.context_agent(rk) == case["context_agent"]
    assert contract.artifact_filename("pjsip", rk) == case["pjsip_file"]
    assert contract.artifact_filename("extensions", rk) == case["extensions_file"]
    assert contract.kamailio_filename(rk) == case["kamailio_file"]
    assert contract.sound_filename(rk, inputs["sound_id"]) == case["sound_file"]
    assert contract.sound_path(rk, inputs["sound_id"]) == case["sound_path"]
    assert contract.parse_artifact_filename(case["sound_file"]) == ("sound", rk)
    assert contract.parse_artifact_filename(case["kamailio_file"]) == ("kamailio", rk)
    assert contract.outbound_block_device(rk) == case["outbound_block_device"]


@pytest.mark.parametrize("bad", NAMING["invalid_realm_keys"])
def test_invalid_realm_keys(bad):
    with pytest.raises(contract.NamingError):
        contract.tenant_tag(bad)


@pytest.mark.parametrize("bad", NAMING["invalid_extensions"])
def test_invalid_extensions(bad):
    with pytest.raises(contract.NamingError):
        contract.extension_endpoint("dev4test", bad)


@pytest.mark.parametrize("case", NAMING["owner_resolution"], ids=lambda c: c["name"])
def test_owner_resolution(case):
    assert contract.owner_realm_key(case["name"]) == case["realm_key"]


@pytest.mark.parametrize("case", NAMING["channel_resolution"], ids=lambda c: c["channel"])
def test_channel_resolution(case):
    assert contract.channel_realm_key(case["channel"]) == case["realm_key"]


def test_generated_realm_keys_are_valid():
    keys = {contract.generate_realm_key() for _ in range(300)}
    assert len(keys) == 300 and all(contract.REALM_KEY_RE.match(k) for k in keys)


@pytest.mark.parametrize("case", CREDS["cases"], ids=lambda c: c["username"])
def test_credential_vectors(case):
    cred = contract.issue_extension_credential(
        case["secret"], case["realm_key"], case["extension"], ttl=case["ttl"], now=case["now"]
    )
    assert (cred.username, cred.password, cred.expires_at) == (case["username"], case["password"], case["expires_at"])


def test_node_token_hash_vector():
    v = CREDS["node_token_hash"]
    assert contract.hash_node_token(v["token"]) == v["sha256"]


@pytest.mark.parametrize("case", CALL_IDS["cases"], ids=lambda c: f'{c["realm_key"]}:{c["linkedid"]}')
def test_call_id_vectors(case):
    assert contract.derived_call_id(case["realm_key"], case["linkedid"]) == case["call_id"]


@pytest.mark.parametrize(
    "text",
    [
        "#include /etc/passwd\n",
        "[tacme2pbx-internal]\n",
        "[default]\n",
        "[tdev4test-x]\ninclude => tacme2pbx-internal\n",
        "[tdev4test-x]\nexten => 1,1,Dial(PJSIP/tacme2pbx_100)\n",
    ],
)
def test_default_reject_checks(text):
    with pytest.raises(contract.ArtifactError):
        contract.check_config_text("dev4test", "extensions_tdev4test.conf", text)


def test_artifact_build_is_deterministic():
    fields = {"tenant": "tdev4test", "company_id": 1, "version": 1, "action": "apply",
              "generated_at": "2026-10-14T00:00:00Z", "compiler_version": "1"}
    files = {"pjsip_tdev4test.conf": "[tdev4test_100]\ntype = endpoint\n"}
    assert contract.build_artifact(fields, files)[0] == contract.build_artifact(fields, files)[0]
