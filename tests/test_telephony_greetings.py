"""
Custom recorded IVR greetings (/telephony/sounds/): upload a WAV (16-bit PCM, mono,
8 kHz), stored as A-law; a menu that plays one ships it in the tenant's artifact
(sha256-checked by the node) and plays it by absolute path.
"""

import io
import math
import struct
import wave

import pytest

from telephony import contract
from telephony.models import PbxArtifact, PbxIvrMenu, PbxSound, PbxTenant
from telephony.services import provisioning, sounds
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db

BASE = "/api/v1/telephony"


def wav(seconds=2.0, rate=8000, channels=1, width=2, hz=440):
    frames = int(seconds * rate)
    one = [int(8000 * math.sin(2 * math.pi * hz * i / rate)) for i in range(frames)]
    if width == 2:
        data = b"".join(struct.pack("<h", v) * channels for v in one)
    else:
        data = bytes(((v >> 8) + 128) & 0xFF for v in one for _ in range(channels))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(data)
    return buf.getvalue()


def upload(client, name="Welcome", data=None, filename="welcome.wav"):
    f = io.BytesIO(wav() if data is None else data)
    f.name = filename
    return client.post(f"{BASE}/sounds/", {"name": name, "file": f}, format="multipart")


def _code(resp):
    return resp.json()["error"]["code"]


def _fields(resp):
    error = resp.json()["error"]
    return set(error.get("fields") or {}) | set(error.get("details") or {})


@pytest.fixture
def live(tenant_a):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1)
    tenant_a.refresh_from_db()
    return tenant_a


def test_upload_stores_alaw_and_recompiles_nothing(admin_client, live):
    resp = upload(admin_client)
    assert resp.status_code == 201, resp.content
    body = resp.json()["data"]
    assert body["name"] == "Welcome" and body["duration_ms"] == 2000 and body["size_bytes"] == 16000
    sound = PbxSound.objects.get(pk=body["id"])
    assert len(bytes(sound.audio)) == 16000 and sound.sha256 == contract.sha256_hex(bytes(sound.audio))
    live.refresh_from_db()
    assert live.config_version == 1  # nothing reaches the node until a menu plays it


@pytest.mark.parametrize(
    "data, message",
    [
        (wav(rate=16000), "16-bit PCM, mono, 8 kHz"),
        (wav(channels=2), "16-bit PCM, mono, 8 kHz"),
        (wav(width=1), "16-bit PCM, mono, 8 kHz"),
        (b"ID3\x03\x00\x00\x00 an mp3, not a wav" * 10, "16-bit PCM, mono, 8 kHz"),
        (wav(seconds=0.2), "too short"),
        (wav(seconds=61), "at most 60 seconds"),
    ],
    ids=["16khz", "stereo", "8bit", "not-wav", "too-short", "too-long"],
)
def test_upload_format_is_checked(admin_client, live, data, message):
    resp = upload(admin_client, data=data)
    assert resp.status_code == 400, resp.content
    assert "file" in _fields(resp)
    assert message in str(resp.json()["error"])
    assert not PbxSound.objects.exists()


def test_preview_round_trips_as_pcm_wav(admin_client, live):
    sound_id = upload(admin_client).json()["data"]["id"]
    resp = admin_client.get(f"{BASE}/sounds/{sound_id}/audio/")
    assert resp.status_code == 200 and resp["Content-Type"] == "audio/wav"
    with wave.open(io.BytesIO(resp.content), "rb") as back:
        assert (back.getnchannels(), back.getsampwidth(), back.getframerate(), back.getnframes()) == (1, 2, 8000, 16000)


def test_menu_greeting_ships_in_the_artifact(admin_client, live):
    sound_id = upload(admin_client).json()["data"]["id"]
    resp = admin_client.post(f"{BASE}/ivr-menus/", {"name": "Main", "greeting": sound_id}, format="json")
    assert resp.status_code == 201, resp.content
    assert resp.json()["data"]["greeting"] == sound_id
    live.refresh_from_db()
    artifact = PbxArtifact.objects.get(tenant=live, version=live.config_version)
    name = contract.sound_filename(live.realm_key, sound_id)
    entry = next(f for f in artifact.manifest["files"] if f["name"] == name)
    sound = PbxSound.objects.get(pk=sound_id)
    assert entry["kind"] == "sound" and entry["sha256"] == sound.sha256 and entry["bytes"] == sound.size_bytes


def test_greeting_in_use_cannot_be_deleted(admin_client, live):
    sound_id = upload(admin_client).json()["data"]["id"]
    menu = admin_client.post(f"{BASE}/ivr-menus/", {"name": "Main", "greeting": sound_id}, format="json").json()["data"]
    resp = admin_client.delete(f"{BASE}/sounds/{sound_id}/")
    assert resp.status_code == 409 and _code(resp) == "in_use"
    assert admin_client.patch(f"{BASE}/ivr-menus/{menu['id']}/", {"greeting": None}, format="json").status_code == 200
    assert admin_client.delete(f"{BASE}/sounds/{sound_id}/").status_code == 204


def test_rename_and_unique_names(admin_client, live):
    sound_id = upload(admin_client).json()["data"]["id"]
    assert upload(admin_client, name="Closed").status_code == 201
    resp = admin_client.patch(f"{BASE}/sounds/{sound_id}/", {"name": "Closed"}, format="json")
    assert resp.status_code == 400 and "name" in _fields(resp)
    resp = admin_client.patch(f"{BASE}/sounds/{sound_id}/", {"name": "Main menu"}, format="json")
    assert resp.status_code == 200 and resp.json()["data"]["name"] == "Main menu"


def test_greetings_per_company_are_capped(admin_client, live, monkeypatch):
    monkeypatch.setattr(sounds, "MAX_SOUNDS_PER_TENANT", 2)
    assert upload(admin_client, name="One").status_code == 201
    assert upload(admin_client, name="Two").status_code == 201
    resp = upload(admin_client, name="Three")
    assert resp.status_code == 409 and _code(resp) == "too_many_sounds"


def test_another_tenants_greeting_cannot_be_used_or_read(admin_client, other_admin_client, live, tenant_b):
    sound_id = upload(other_admin_client).json()["data"]["id"]
    resp = admin_client.post(f"{BASE}/ivr-menus/", {"name": "Main", "greeting": sound_id}, format="json")
    assert resp.status_code == 400 and "greeting" in _fields(resp)
    assert admin_client.get(f"{BASE}/sounds/{sound_id}/audio/").status_code == 404
    assert admin_client.delete(f"{BASE}/sounds/{sound_id}/").status_code == 404
    assert admin_client.get(f"{BASE}/sounds/").json()["data"] == [] or admin_client.get(
        f"{BASE}/sounds/").json()["data"]["results"] == []


def test_greetings_are_admin_only(employee_client, live):
    assert employee_client.get(f"{BASE}/sounds/").status_code == 403
    assert upload(employee_client).status_code == 403


def test_old_artifacts_are_pruned(live):
    provisioning.request_recompile_for_company(live.company)
    live.refresh_from_db()
    running = live.config_version
    PbxTenant.objects.filter(pk=live.pk).update(applied_version=running)  # the node runs this one
    for _ in range(8):
        provisioning.request_recompile_for_company(live.company)
    live.refresh_from_db()
    versions = sorted(PbxArtifact.objects.filter(tenant=live).values_list("version", flat=True))
    # The newest five, plus the version the node runs.
    assert versions == [running, *range(live.config_version - 4, live.config_version + 1)]


def test_menu_payload_without_greeting_keeps_the_platform_prompt(admin_client, live):
    resp = admin_client.post(f"{BASE}/ivr-menus/", {"name": "Main", "prompt": "loop/ivr-welcome-en"}, format="json")
    assert resp.status_code == 201 and resp.json()["data"]["greeting"] is None
    assert PbxIvrMenu.objects.get().greeting_id is None
