"""
Config compiler: snapshot + default-reject + tenant-boundary behaviour.

The snapshot scenario is the same two-tenant setup as CRM-pbx's dev sample
(tdev4test with ext 100/101, a trunk, a verified DID, an "Iraq mobile" route).
Regenerate snapshots after an intended change with:

    UPDATE_SNAPSHOTS=1 .venv/Scripts/python.exe -m pytest tests/test_telephony_compiler.py
"""

import json
import os
import re
import uuid
from pathlib import Path

import pytest
from django.test import override_settings

from telephony import contract
from telephony.models import (
    DeviceType,
    PbxExtension,
    PbxInboundRoute,
    PbxOutboundRoute,
    PbxTrunk,
    RecordingMode,
    RecordingNotice,
)
from telephony.services.compiler import CompileError, clean_display_name, compile_tenant
from tests.telephony_fixtures import *  # noqa: F401,F403

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "telephony"
TRUNK_ID = uuid.UUID("1a2b3c4d-0000-4000-8000-000000000001")

pytestmark = pytest.mark.django_db
EMERGENCY = ["104", "115", "122", "112", "911"]


@pytest.fixture(autouse=True)
def _emergency_numbers(settings):
    # Snapshots never depend on a developer's TELEPHONY_EMERGENCY_NUMBERS.
    settings.TELEPHONY_EMERGENCY_NUMBERS = EMERGENCY


@pytest.fixture
def scenario(company, tenant_a, make_trunk, make_number, make_extension):
    ext100 = make_extension(company, "100", secret="dev-only-100-secret", display_name="Agent 100",
                            device_type=DeviceType.WEBRTC)
    make_extension(company, "101", secret="dev-only-101-secret", display_name="Desk 101",
                   device_type=DeviceType.DESK_PHONE)
    trunk = make_trunk(company, id=TRUNK_ID)
    did = make_number(company, trunk, "+9647700000001")
    PbxInboundRoute.objects.create(company=company, number=did, extension=ext100)
    PbxOutboundRoute.objects.create(
        company=company, name="Iraq mobile", patterns=[{"prefix": "+9647", "digits": 9}], trunk=trunk, caller_id=did
    )
    return tenant_a


def _check_snapshot(name, text):
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def test_snapshot(scenario):
    compiled = compile_tenant(scenario)
    assert set(compiled.files) == {"pjsip_tdev4test.conf", "extensions_tdev4test.conf"}
    for name, text in compiled.files.items():
        _check_snapshot(name, text)
    assert compiled.warnings == []


def test_output_is_deterministic(scenario):
    assert compile_tenant(scenario).files == compile_tenant(scenario).files


def test_every_entry_context_ends_in_default_reject(scenario):
    text = compile_tenant(scenario).files["extensions_tdev4test.conf"]
    for ctx in ("tdev4test-internal", "tdev4test-from-agent", "tdev4test-from-trunk"):
        block = text.split(f"[{ctx}]", 1)[1].split("\n[", 1)[0]
        assert "exten => _X.,1,Hangup(" in block and "exten => _+X.,1,Hangup(" in block and "exten => i,1,Hangup(" in block
    assert "include =>" not in text and "#include" not in text


def test_only_own_tenant_rows_are_compiled(scenario, other_company, tenant_b, make_trunk, make_number, make_extension):
    # Tenant B has the same extension number and its own trunk/DID.
    make_extension(other_company, "100", display_name="Acme")
    b_trunk = make_trunk(other_company, name="B carrier", host="198.51.100.7", allowed_sources=["198.51.100.7/32"])
    make_number(other_company, b_trunk, "+9647800000009")

    a = compile_tenant(scenario).files
    b = compile_tenant(tenant_b).files
    joined_a, joined_b = "".join(a.values()), "".join(b.values())
    assert "tacme2pbx" not in joined_a and "198.51.100.7" not in joined_a and "+9647800000009" not in joined_a
    assert "tdev4test" not in joined_b and "192.0.2.10" not in joined_b and "+9647700000001" not in joined_b
    assert "[tacme2pbx_100]" in b["pjsip_tacme2pbx.conf"]


def test_suspended_tenant_has_no_outbound_routes(scenario):
    scenario.outbound_suspended = True
    text = compile_tenant(scenario).files["extensions_tdev4test.conf"]
    from_agent = text.split("[tdev4test-from-agent]", 1)[1].split("\n[", 1)[0]
    assert "_+9647XXXXXXXXX" not in from_agent and "outbound route" not in from_agent
    assert "exten => 100,1,Goto(tdev4test-internal,100,1)" in from_agent  # internal calls still work
    # Emergency numbers bypass every suspension: still compiled, and never behind the outbound switch.
    assert "exten => 112,1,Gosub(tdev4test-sub-entry,s,1)" in from_agent
    assert "tdev4test-outbound" not in from_agent


def test_unverified_numbers_are_neither_routed_nor_used_as_caller_id(scenario):
    from telephony.models import PbxNumber

    PbxNumber.objects.update(verified_at=None)
    compiled = compile_tenant(scenario)
    text = compiled.files["extensions_tdev4test.conf"]
    assert "+9647700000001" not in text
    assert any("not verified" in w for w in compiled.warnings)


def test_disabled_extension_and_trunk_are_omitted(scenario):
    PbxExtension.objects.filter(number="101").update(status=PbxExtension.Status.DISABLED)
    PbxTrunk.objects.update(status=PbxTrunk.Status.DISABLED)
    files = compile_tenant(scenario).files
    assert "tdev4test_101" not in "".join(files.values())
    assert "tdev4test_trk_" not in "".join(files.values())


def test_edge_auth_mode_drops_webrtc_auth_only(scenario, settings):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.TELEPHONY_SIP_EDGE_PROXY = "127.0.0.1:5065"
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    pjsip = compile_tenant(scenario).files["pjsip_tdev4test.conf"]
    assert "[tdev4test_100-auth]" not in pjsip and "dev-only-100-secret" not in pjsip
    assert "[tdev4test_101-auth]" in pjsip  # desk phones keep digest auth


def test_webrtc_endpoints_present_the_platform_sip_domain(scenario, settings):
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    pjsip = compile_tenant(scenario).files["pjsip_tdev4test.conf"]
    webrtc = pjsip.split("[tdev4test_100]", 1)[1].split("\n\n", 1)[0]
    desk = pjsip.split("[tdev4test_101]", 1)[1].split("\n\n", 1)[0]
    assert "from_domain = sip.loop-crm.example" in webrtc and "from_domain" not in desk

    settings.TELEPHONY_SIP_DOMAIN = "evil.example\nauth = x"
    with pytest.raises(CompileError):
        compile_tenant(scenario)


@pytest.mark.parametrize(
    "raw,clean",
    [
        ('Ali "boss" <1>', "Ali boss 1"),
        ("x;\nexten => 9,1,Dial(PJSIP/tacme2pbx_100)", "xexten 91DialPJSIPtacme2pbx100"),
        ("علي حسن", "علي حسن"),
        ("tacme2pbx-internal", "tacme2pbxinternal"),
    ],
)
def test_display_names_cannot_inject_config(raw, clean):
    assert clean_display_name(raw) == clean


def test_injected_display_name_compiles_safely(scenario):
    PbxExtension.objects.filter(number="101").update(display_name="x\n[tacme2pbx-internal]\ninclude => tacme2pbx-internal")
    files = compile_tenant(scenario).files  # check_config_text would raise on any leak
    assert "[tacme2pbx-internal]" not in files["pjsip_tdev4test.conf"]


def test_corrupt_secret_is_refused(scenario):
    from integrations.encryption import encrypt_token

    PbxExtension.objects.filter(number="101").update(sip_secret_encrypted=encrypt_token("abc\npassword = x" * 2))
    with pytest.raises(CompileError):
        compile_tenant(scenario)


def test_duplicate_patterns_keep_highest_priority_route(scenario, company, make_number):
    trunk = PbxTrunk.objects.get()
    other = make_number(company, trunk, "+9647700000002")
    PbxOutboundRoute.objects.create(
        company=company, name="Dup", priority=200, patterns=[{"prefix": "+9647", "digits": 9}], trunk=trunk, caller_id=other
    )
    compiled = compile_tenant(scenario)
    text = compiled.files["extensions_tdev4test.conf"]
    assert text.count("exten => _+9647XXXXXXXXX,1") == 1
    assert "CALLERID(all)=\"+9647700000001\" <+9647700000001>" in text
    assert any("already routed" in w for w in compiled.warnings)


# ---- call recording (contract section 7) ------------------------------------


def _record(tenant, mode=RecordingMode.EXTERNAL, notice=RecordingNotice.AR_EN):
    tenant.recording_mode, tenant.recording_notice = mode, notice
    tenant.save()
    with override_settings(TELEPHONY_RECORDING_ALLOWED=True):  # the platform's legal gate, off by default
        return compile_tenant(tenant)


def _block(text, ctx):
    return text.split(f"[{ctx}]", 1)[1].split("\n[", 1)[0]


def test_recording_snapshot(scenario):
    plain = compile_tenant(scenario).files
    compiled = _record(scenario)
    _check_snapshot("recording/extensions_tdev4test.conf", compiled.files["extensions_tdev4test.conf"])
    assert compiled.files["pjsip_tdev4test.conf"] == plain["pjsip_tdev4test.conf"]
    assert compiled.warnings == []


def test_platform_gate_overrides_a_tenant_that_records(scenario):
    """A row left in a recording mode compiles no recording while TELEPHONY_RECORDING_ALLOWED is off."""
    scenario.recording_mode, scenario.recording_notice = RecordingMode.ALL, RecordingNotice.AR_EN
    scenario.save()
    text = compile_tenant(scenario).files["extensions_tdev4test.conf"]
    for needle in ("MixMonitor", "sub-record", "sub-notice", "LOOP_RECORDING", "Playback", ",U("):
        assert needle not in text


def test_recording_off_emits_nothing(scenario):
    text = compile_tenant(scenario).files["extensions_tdev4test.conf"]
    for needle in ("MixMonitor", "sub-record", "sub-notice", "LOOP_RECORDING", "Playback", ",U("):
        assert needle not in text


def test_external_mode_records_trunk_calls_only(scenario):
    text = _record(scenario).files["extensions_tdev4test.conf"]
    record = "Gosub(tdev4test-sub-record,s,1)"
    assert record not in _block(text, "tdev4test-internal")
    assert record in _block(text, "tdev4test-from-trunk")
    outbound = _block(text, "tdev4test-from-agent").split("exten => _+9647XXXXXXXXX,1,", 1)[1]
    assert record in outbound.split("Dial(", 1)[0]  # started before dialling
    assert "Dial(PJSIP/${EXTEN}@tdev4test_trk_1a2b3c4d,60,U(tdev4test-sub-notice))" in outbound
    # Inbound: the caller hears the notice before the call is routed to an agent.
    inbound = _block(text, "tdev4test-from-trunk").split("exten => +9647700000001,1,", 1)[1].split("exten =>", 1)[0]
    assert inbound.index("sub-record") < inbound.index("sub-notice") < inbound.index("Goto(tdev4test-internal")


def test_all_mode_records_internal_calls_without_a_notice(scenario):
    text = _record(scenario, RecordingMode.ALL).files["extensions_tdev4test.conf"]
    internal = _block(text, "tdev4test-internal")
    assert internal.count("Gosub(tdev4test-sub-record,s,1)") == 2  # ext 100 and 101
    assert "sub-notice" not in internal


def test_no_notice_means_no_playback(scenario):
    text = _record(scenario, notice=RecordingNotice.NONE).files["extensions_tdev4test.conf"]
    assert "MixMonitor" in text and "Playback" not in text and "sub-notice" not in text and ",U(" not in text


@pytest.mark.parametrize(
    "notice,playback",
    [
        (RecordingNotice.AR, "Playback(loop/recording-notice-ar)"),
        (RecordingNotice.EN, "Playback(loop/recording-notice-en)"),
        (RecordingNotice.AR_EN, "Playback(loop/recording-notice-ar&loop/recording-notice-en)"),
    ],
)
def test_notice_prompts(scenario, notice, playback):
    assert playback in _record(scenario, notice=notice).files["extensions_tdev4test.conf"]


def test_recording_path_is_built_from_filtered_values_only(scenario):
    """The path also goes through /bin/sh (MixMonitor's post-process command)."""
    text = _record(scenario).files["extensions_tdev4test.conf"]
    [line] = [l for l in text.splitlines() if "Set(LOOP_REC=" in l]
    path = line.split("Set(LOOP_REC=", 1)[1][:-1]
    runtime = path.replace("${FILTER(0-9a-f-,${LOOP_CALL_ID})}", "").replace("${FILTER(0-9.,${UNIQUEID})}", "")
    runtime = runtime.replace("${EPOCH}", "")
    assert "$" not in runtime and " " not in runtime
    assert runtime == "/var/spool/asterisk/recording/loop/tdev4test//-.wav"
    assert "MixMonitor(${LOOP_REC},b,/bin/touch ${LOOP_REC}.done)" in text


def test_artifact_from_compiled_files_passes_contract_checks(scenario):
    files = compile_tenant(scenario).files
    blob, manifest = contract.build_artifact(
        {"tenant": "tdev4test", "company_id": scenario.company_id, "version": 1, "action": "apply",
         "generated_at": "2026-10-14T00:00:00Z", "compiler_version": "1"},
        files,
    )
    assert [f["name"] for f in manifest["files"]] == sorted(files)


def test_edge_mode_emits_routes_and_private_kamailio_contacts(
    settings, tenant_a, make_extension, make_trunk
):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.TELEPHONY_SIP_EDGE_PROXY = "127.0.0.1:5065"
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    make_extension(tenant_a.company, "100", device_type="webrtc")
    trunk = make_trunk(tenant_a.company, status="ready", allowed_sources=["192.0.2.10/32"])

    compiled = compile_tenant(tenant_a, version=7)
    pjsip = compiled.files["pjsip_tdev4test.conf"]
    edge = json.loads(compiled.files["kamailio_tdev4test.json"])

    assert "contact = sip:tdev4test_100@sip.loop-crm.example" in pjsip
    webrtc_aor = pjsip.split("[tdev4test_100]\ntype = aor", 1)[1].split("\n\n", 1)[0]
    assert "qualify_frequency = 10" in webrtc_aor  # answered by the edge from its registrations
    assert "transport = transport-ws" not in pjsip and "webrtc = yes" not in pjsip
    # Escaped: an unescaped ";" starts an Asterisk comment and would drop ";lr".
    assert pjsip.count(r"outbound_proxy = sip:127.0.0.1:5065\;lr") == 4  # ext 100 + trunk: endpoint and AOR
    assert not re.search(r"(?<!\\);(lr|transport)", pjsip)
    # Endpoints without Asterisk-side auth are selected only by the header the
    # edge sets after its own check, never by a From user (contract §10).
    trunk_name = contract.trunk_endpoint("dev4test", trunk.id)
    for name in ("tdev4test_100", trunk_name):
        endpoint = pjsip.split(f"[{name}]\ntype = endpoint", 1)[1].split("\n\n", 1)[0]
        assert "identify_by = header" in endpoint
        assert f"[{name}-identify]\ntype = identify\nendpoint = {name}\nmatch_header = X-Loop-Endpoint: {name}\n" in pjsip
    assert "match = " not in pjsip
    assert not any("no allowed source" in warning for warning in compiled.warnings)
    assert edge == {
        "schema": "loop.pbx.kamailio-route/1",
        "tenant": "tdev4test",
        "version": 7,
        "trunks": [{
            "endpoint": contract.trunk_endpoint("dev4test", trunk.id),
            "sources": ["192.0.2.10/32"],
        }],
    }


def test_edge_mode_trunks_hop_privately_and_keep_their_transport_in_the_contact(
    settings, tenant_a, make_extension, make_trunk
):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.TELEPHONY_SIP_EDGE_PROXY = "127.0.0.1:5065"
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    make_extension(tenant_a.company, "101", secret="dev-only-101-secret", device_type="desk_phone")
    tls = make_trunk(tenant_a.company, name="TLS", host="sim.loop-crm.example", port=5061, transport="tls",
                     status="ready", allowed_sources=["192.0.2.10/32"])
    udp = make_trunk(tenant_a.company, name="UDP", host="192.0.2.20", status="ready",
                     allowed_sources=["192.0.2.20/32"])

    pjsip = compile_tenant(tenant_a).files["pjsip_tdev4test.conf"]

    assert "transport-tls" not in pjsip and "transport-tcp" not in pjsip
    assert r"contact = sip:sim.loop-crm.example:5061\;transport=tls" in pjsip
    assert "contact = sip:192.0.2.20:5060\n" in pjsip
    assert contract.trunk_endpoint("dev4test", tls.id) != contract.trunk_endpoint("dev4test", udp.id)
    desk = pjsip.split("[tdev4test_101]\ntype = endpoint", 1)[1].split("\n\n", 1)[0]
    assert "identify_by = username" in desk and "auth = tdev4test_101-auth" in desk
    assert "[tdev4test_101-identify]" not in pjsip


def test_edge_mode_rejects_overlapping_ready_trunk_sources(
    settings, tenant_a, tenant_b, make_trunk
):
    settings.TELEPHONY_WEBRTC_AUTH = "edge"
    settings.TELEPHONY_SIP_EDGE_PROXY = "127.0.0.1:5065"
    settings.TELEPHONY_SIP_DOMAIN = "sip.loop-crm.example"
    make_trunk(tenant_a.company, name="A", status="ready", allowed_sources=["192.0.2.0/24"])
    make_trunk(tenant_b.company, name="B", status="ready", allowed_sources=["192.0.2.8/32"])

    with pytest.raises(CompileError, match="edge source network overlap"):
        compile_tenant(tenant_a)


# ---- routing (Phase 4): business hours, IVR, queues -------------------------------------

HOURS_ID = uuid.UUID("5e1f0a2b-0000-4000-8000-000000000003")
IVR_ID = uuid.UUID("0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d")  # the naming vector's ivr_id
QUEUE_ID = uuid.UUID("9f8e7d6c-5b4a-4321-8fed-cba987654321")  # the naming vector's queue_id


@pytest.fixture
def routing_scenario(scenario, company):
    from telephony.models import PbxBusinessHours, PbxIvrMenu, PbxIvrOption, PbxQueue, PbxQueueMember

    ext100 = PbxExtension.objects.get(company=company, number="100")
    ext101 = PbxExtension.objects.get(company=company, number="101")
    hours = PbxBusinessHours.objects.create(
        id=HOURS_ID, company=company, name="Office", timezone="Asia/Baghdad",
        rules=[{"days": [6, 0, 1, 2, 3], "start": "09:00", "end": "17:00"}], closed_dates=["2026-12-25"],
    )
    queue = PbxQueue.objects.create(id=QUEUE_ID, company=company, name="Sales", member_timeout_s=15, max_wait_s=60,
                                    overflow_type="extension", overflow_extension=ext101)
    PbxQueueMember.objects.create(company=company, queue=queue, extension=ext100)
    PbxQueueMember.objects.create(company=company, queue=queue, extension=ext101, penalty=1)
    menu = PbxIvrMenu.objects.create(id=IVR_ID, company=company, name="Main", prompt="loop/ivr-welcome-ar",
                                     timeout_type="queue", timeout_queue=queue)
    PbxIvrOption.objects.create(company=company, menu=menu, digit="1", destination_type="queue", queue=queue)
    PbxIvrOption.objects.create(company=company, menu=menu, digit="2", destination_type="extension", extension=ext101)
    PbxIvrOption.objects.create(company=company, menu=menu, digit="0", destination_type="hangup")
    PbxInboundRoute.objects.filter(company=company).update(
        destination_type="ivr", extension=None, ivr=menu, business_hours=hours,
        closed_type="extension", closed_extension=ext101,
    )
    return scenario


def test_routing_snapshot(routing_scenario):
    compiled = compile_tenant(routing_scenario)
    assert set(compiled.files) == {"pjsip_tdev4test.conf", "extensions_tdev4test.conf", "queues_tdev4test.conf"}
    for name, text in compiled.files.items():
        _check_snapshot(f"routing/{name}", text)
    assert compiled.warnings == []


def test_business_hours_render_as_time_checks(routing_scenario):
    text = compile_tenant(routing_scenario).files["extensions_tdev4test.conf"]
    hours = text.split("[tdev4test-sub-hours-5e1f0a2b]", 1)[1].split("\n[", 1)[0]
    # The last minute is excluded (GotoIfTime ranges are inclusive), and Monday=0 / Sunday=6.
    assert "GotoIfTime(09:00-16:59,mon&tue&wed&thu&sun,*,*,Asia/Baghdad?open)" in hours
    assert '"${STRFTIME(${EPOCH},Asia/Baghdad,%Y-%m-%d)}" = "2026-12-25"]?done' in hours
    did = text.split("exten => +9647700000001,1,", 1)[1].split("exten =>", 1)[0]
    assert "Gosub(tdev4test-sub-hours-5e1f0a2b,s,1)" in did
    assert " same => n,Goto(tdev4test-internal,101,1)" in did  # closed
    assert " same => n(open),Goto(tdev4test-ivr-0a1b2c3d,s,1)" in did


def test_ivr_menu_and_queue(routing_scenario):
    files = compile_tenant(routing_scenario).files
    text = files["extensions_tdev4test.conf"]
    ivr = text.split("[tdev4test-ivr-0a1b2c3d]", 1)[1].split("\n[", 1)[0]
    assert "Background(loop/ivr-welcome-ar)" in ivr and "WaitExten(5)" in ivr
    assert "exten => 1,1,Goto(tdev4test-queue-9f8e7d6c,s,1)" in ivr
    assert "exten => 2,1,Goto(tdev4test-internal,101,1)" in ivr and "exten => 0,1,Hangup()" in ivr
    assert "exten => t,1,Goto(tdev4test-queue-9f8e7d6c,s,1)" in ivr
    assert "exten => _X.,1,Hangup(" in ivr and "exten => _+X.,1,Hangup(" in ivr
    queue = text.split("[tdev4test-queue-9f8e7d6c]", 1)[1].split("\n[", 1)[0]
    assert "Queue(tdev4test_q_9f8e7d6c,,,,60)" in queue and "Goto(tdev4test-internal,101,1)" in queue
    conf = files["queues_tdev4test.conf"]
    assert "[tdev4test_q_9f8e7d6c]" in conf and "timeout = 15" in conf and "ringinuse = no" in conf
    assert "member => Local/100@tdev4test-agent,0,Agent 100,PJSIP/tdev4test_100" in conf
    assert "member => Local/101@tdev4test-agent,1,Desk 101,PJSIP/tdev4test_101" in conf
    agent = text.split("[tdev4test-agent]", 1)[1].split("\n[", 1)[0]
    assert '"${DEVICE_STATE(Stasis:tdev4test_100-agent)}" = "BUSY"]?paused' in agent


def test_routing_never_points_at_another_tenant(routing_scenario, other_company, tenant_b):
    """A destination naming another company's row (forced past the API) compiles to a hangup."""
    from telephony.models import PbxIvrMenu

    foreign = PbxIvrMenu.objects.create(company=other_company, name="B menu")
    PbxInboundRoute.objects.filter(company=routing_scenario.company).update(ivr=foreign)
    compiled = compile_tenant(routing_scenario)
    text = "".join(compiled.files.values())
    assert "tacme2pbx" not in text and contract.short_id(foreign.id) not in text
    assert any("inbound +9647700000001: destination unavailable" in w for w in compiled.warnings)


def test_queue_without_members_warns(routing_scenario):
    from telephony.models import PbxQueueMember

    PbxQueueMember.objects.all().delete()
    compiled = compile_tenant(routing_scenario)
    assert "member =>" not in compiled.files["queues_tdev4test.conf"]
    assert any("no members" in w for w in compiled.warnings)


# ---- custom greetings (sounds in the artifact) ------------------------------------------

SOUND_ID = uuid.UUID("5e1f0a2b-6c7d-4e8f-9a0b-1c2d3e4f5a6b")  # the naming vector's sound_id


def _sound(company, audio=b"\xd5\x55" * 4000, **kw):
    from telephony.models import PbxSound

    return PbxSound.objects.create(
        company=company, name=kw.pop("name", "Welcome"), audio=audio, sha256=contract.sha256_hex(audio),
        size_bytes=len(audio), duration_ms=len(audio) // 8, **kw,
    )


def test_ivr_greeting_is_shipped_and_played_by_absolute_path(routing_scenario, company):
    from telephony.models import PbxIvrMenu

    audio = bytes(range(256)) * 40
    sound = _sound(company, audio, id=SOUND_ID)
    _sound(company, name="Unused")  # not played by any menu: not shipped
    PbxIvrMenu.objects.filter(pk=IVR_ID).update(greeting=sound)
    compiled = compile_tenant(routing_scenario)
    assert compiled.files["sound_tdev4test_5e1f0a2b.alaw"] == audio
    assert sum(name.startswith("sound_") for name in compiled.files) == 1
    ivr = compiled.files["extensions_tdev4test.conf"].split("[tdev4test-ivr-0a1b2c3d]", 1)[1].split("\n[", 1)[0]
    assert "Background(/etc/asterisk/loop/active/sound_tdev4test_5e1f0a2b)" in ivr
    assert compiled.warnings == []
    blob, manifest = contract.build_artifact(
        {"tenant": "tdev4test", "company_id": company.id, "version": 1, "action": "apply",
         "generated_at": "2026-10-14T00:00:00Z", "compiler_version": "1"},
        compiled.files,
    )
    entry = next(f for f in manifest["files"] if f["kind"] == "sound")
    assert entry == {"name": "sound_tdev4test_5e1f0a2b.alaw", "kind": "sound", "sha256": contract.sha256_hex(audio),
                     "bytes": len(audio)}


def test_another_tenants_greeting_is_never_shipped(routing_scenario, other_company):
    from telephony.models import PbxIvrMenu

    foreign = _sound(other_company)
    PbxIvrMenu.objects.filter(pk=IVR_ID).update(greeting=foreign)  # forced past the API
    compiled = compile_tenant(routing_scenario)
    assert not any(name.startswith("sound_") for name in compiled.files)
    assert "Background(loop/ivr-welcome-ar)" in compiled.files["extensions_tdev4test.conf"]
    assert any("greeting unavailable" in w for w in compiled.warnings)


def test_corrupt_greeting_fails_the_compile(routing_scenario, company):
    from telephony.models import PbxIvrMenu, PbxSound

    sound = _sound(company)
    PbxSound.objects.filter(pk=sound.pk).update(sha256="0" * 64)
    PbxIvrMenu.objects.filter(pk=IVR_ID).update(greeting=sound)
    with pytest.raises(CompileError, match="corrupt"):
        compile_tenant(routing_scenario)


# ---- emergency numbers (services/emergency.py, acceptance #10) ---------------------------


def _from_agent(scenario):
    compiled = compile_tenant(scenario)
    text = compiled.files["extensions_tdev4test.conf"]
    return text.split("[tdev4test-from-agent]", 1)[1].split("\n[", 1)[0], compiled.warnings


def test_emergency_numbers_leave_by_the_first_route_without_the_outbound_switch(scenario):
    block, warnings = _from_agent(scenario)
    for number in EMERGENCY:
        assert f"exten => {number},1,Gosub(tdev4test-sub-entry,s,1)" in block
    lines = block.split("exten => 112,1,", 1)[1].split("exten =>", 1)[0]
    assert 'Set(CALLERID(all)="+9647700000001" <+9647700000001>)' in lines
    assert "Dial(PJSIP/${EXTEN}@tdev4test_trk_1a2b3c4d,60)" in lines
    # Never behind the node's outbound switch, never recorded, never delayed by the notice.
    assert "DEVICE_STATE" not in lines and "sub-record" not in lines and "sub-notice" not in lines
    assert warnings == []


def test_emergency_numbers_bypass_a_suspension_and_still_record_nothing(scenario, recording_allowed):
    scenario.outbound_suspended = True
    scenario.recording_mode = RecordingMode.EXTERNAL
    block, _ = _from_agent(scenario)
    lines = block.split("exten => 911,1,", 1)[1].split("exten =>", 1)[0]
    assert "Dial(PJSIP/${EXTEN}@tdev4test_trk_1a2b3c4d,60)" in lines and "sub-record" not in lines


def test_emergency_numbers_without_any_outbound_route_use_a_verified_number(scenario):
    PbxOutboundRoute.objects.all().delete()
    block, warnings = _from_agent(scenario)
    assert 'exten => 112,1,' in block and 'Set(CALLERID(all)="+9647700000001" <+9647700000001>)' in block
    assert warnings == []


def test_emergency_numbers_need_a_trunk_with_a_verified_number(scenario):
    from telephony.models import PbxNumber

    PbxNumber.objects.update(verified_at=None)
    block, warnings = _from_agent(scenario)
    assert "exten => 112," not in block
    assert any("emergency numbers are not routed" in w for w in warnings)


def test_an_old_extension_on_an_emergency_number_is_not_dialled_by_agents(scenario, company, make_extension):
    make_extension(company, "112", display_name="Old reception")  # created before the API refused it
    compiled = compile_tenant(scenario)
    text = compiled.files["extensions_tdev4test.conf"]
    block = text.split("[tdev4test-from-agent]", 1)[1].split("\n[", 1)[0]
    assert "exten => 112,1,Goto(tdev4test-internal,112,1)" not in block
    assert block.count("exten => 112,1,") == 1  # only the emergency line: no duplicate priority
    assert "exten => 112,1,Gosub(tdev4test-sub-entry,s,1)" in text.split("[tdev4test-internal]", 1)[1]
    assert any("extension 112 is an emergency number" in w for w in compiled.warnings)


def test_no_emergency_numbers_configured_compiles_none(scenario, settings):
    settings.TELEPHONY_EMERGENCY_NUMBERS = []
    block, warnings = _from_agent(scenario)
    assert "Emergency" not in block and warnings == []


def test_an_extensions_outbound_sim_overrides_the_route(scenario, company, make_trunk, make_number):
    sim_trunk = make_trunk(company, name="GSM", host="192.0.2.20", allowed_sources=["192.0.2.20/32"])
    sim = make_number(company, sim_trunk, "+9647700000099")
    PbxExtension.objects.filter(number="100").update(outbound_number=sim)
    text = compile_tenant(scenario).files["extensions_tdev4test.conf"]
    block = text.split("[tdev4test-from-agent]", 1)[1].split("[tdev4test-from-trunk]", 1)[0]
    assert "tdev4test-sub-outbound-id" in block
    assert 'CHANNEL(endpoint)}" = "tdev4test_100"' in block
    assert "Set(LOOP_OUT_CID=+9647700000099)" in block
    assert "Set(LOOP_OUT_TRUNK=tdev4test_trk_" in block
    assert 'GotoIf($["${LOOP_OUT_CID}" != ""]?ownsim)' in block
    assert "Dial(PJSIP/${EXTEN}@${LOOP_OUT_TRUNK},60)" in block
    # The company route stays the fallback for extensions without a SIM.
    assert 'Set(CALLERID(all)="+9647700000001" <+9647700000001>)' in block


def test_a_direct_call_plays_hold_music_while_the_extension_rings(scenario):
    text = compile_tenant(scenario).files["extensions_tdev4test.conf"]
    internal = text.split("[tdev4test-internal]", 1)[1].split("[tdev4test-from-agent]", 1)[0]
    assert "Dial(PJSIP/tdev4test_100,30,b(tdev4test-sub-agent-headers^s^1)m)" in internal
