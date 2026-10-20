"""
Object storage against a real S3 server: the dev SeaweedFS standing in for R2
(CRM-pbx/deploy/dev, 127.0.0.1:9000). Never real R2.

    docker compose -f ../CRM-pbx/deploy/dev/docker-compose.yml up -d s3
    LOOP_STORAGE_LIVE=1 .\\.venv\\Scripts\\python.exe -m pytest tests/test_object_storage_live.py -n 0

Covers: upload / presign / GET / delete (and the operator's check command), an
attachment served by a redirect, a backup round trip, a recording uploaded by the
PBX worker's own signer and played through the CRM, and migrate_media_to_r2.
Each run uses fresh buckets and removes them afterwards.
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import wave
from io import StringIO
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import boto3
import pytest
from botocore.config import Config
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from object_storage.backends import backup_storage, recordings_storage
from object_storage.config import R2Settings, build_storages
from tests.telephony_fixtures import *  # noqa: F401,F403
from tests.test_telephony_calls import inbound_call, node, recording, setup  # noqa: F401 (fixtures)
from tests.test_tenant_chat import MINI_PNG_BYTES, _company_with_subscription, _user

pytestmark = pytest.mark.skipif(os.environ.get("LOOP_STORAGE_LIVE") != "1",
                                reason="set LOOP_STORAGE_LIVE=1 with the dev SeaweedFS running")

ENDPOINT = "http://127.0.0.1:9000"
ACCESS, SECRET = "loopdevaccess", "dev-only-s3-secret"  # CRM-pbx/deploy/dev/seaweedfs-s3.json
PBX_REPO = Path(__file__).resolve().parents[2] / "CRM-pbx"
PBX_PY = PBX_REPO / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _s3():
    return boto3.client("s3", endpoint_url=ENDPOINT, region_name="auto", aws_access_key_id=ACCESS,
                        aws_secret_access_key=SECRET,
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


def _keys(bucket):
    return {o["Key"] for o in _s3().list_objects_v2(Bucket=bucket).get("Contents", [])}


@pytest.fixture(scope="module")
def buckets():
    tag = uuid.uuid4().hex[:8]
    names = {role: f"live-{role}-{tag}" for role in ("media", "backups", "recordings")}
    client = _s3()
    for name in names.values():
        client.create_bucket(Bucket=name)
    yield names
    for name in names.values():
        for key in _keys(name):
            client.delete_object(Bucket=name, Key=key)
        client.delete_bucket(Bucket=name)


@pytest.fixture
def r2(settings, buckets):
    conf = R2Settings(endpoint_url=ENDPOINT, access_key_id=ACCESS, secret_access_key=SECRET, region="auto",
                      media_bucket=buckets["media"], backup_bucket=buckets["backups"],
                      recordings_bucket=buckets["recordings"])
    settings.STORAGES = build_storages(media="r2", recordings="r2", r2=conf)
    return conf


def _get(url, **headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as resp:
        return resp.status, dict(resp.headers), resp.read()


# ---------------------------------------------------------------------------


def test_upload_presign_get_delete_live(r2, buckets):
    name = default_storage.save("companies/9/company_library/2026/10/17/عقد الإيجار.pdf", ContentFile(b"%PDF-live"))
    assert name in _keys(buckets["media"])
    url = default_storage.url(name)
    assert url == default_storage.url(name)  # stable inside the window
    status, headers, body = _get(url, Origin="null")
    assert status == 200 and body == b"%PDF-live"
    assert headers.get("Cache-Control") == "private, max-age=86400"
    # The same name again is not overwritten: a suffixed key, like FileSystemStorage.
    second = default_storage.save(name, ContentFile(b"other"))
    assert second != name and default_storage.open(name).read() == b"%PDF-live"
    default_storage.delete(name)
    default_storage.delete(second)
    assert not default_storage.exists(name) and name not in _keys(buckets["media"])
    # A presigned URL for a deleted object is a 404 from the bucket, not a 500 from us.
    with pytest.raises(urllib.error.HTTPError) as gone:
        _get(url)
    assert gone.value.code == 404

    out = StringIO()
    call_command("check_object_storage", stdout=out)
    report = out.getvalue()
    assert f"default: ok bucket={buckets['media']}" in report
    assert f"backups: ok bucket={buckets['backups']}" in report
    assert f"recordings: ok bucket={buckets['recordings']}" in report
    assert SECRET not in report


@pytest.mark.django_db
def test_chat_attachment_is_a_redirect_to_the_bucket_live(r2, buckets):
    company, owner = _company_with_subscription("live_r2")
    emp = _user(company, "emp_live_r2", "employee")
    client = APIClient()
    client.force_authenticate(user=owner)
    conv = client.post(reverse("tenant_chat_conversation-list"), {"with_user_id": emp.id}, format="json").data["id"]
    pdf = b"%PDF-1.4\n% quarterly numbers\n%%EOF\n"
    up = SimpleUploadedFile("تقرير.pdf", pdf, content_type="application/pdf")
    sent = client.post(reverse("tenant_chat_conversation-messages", kwargs={"pk": conv}),
                       {"body": "", "file": up}, format="multipart")
    assert sent.status_code == 201, sent.content
    key = next(k for k in _keys(buckets["media"]) if k.startswith(f"companies/{company.id}/tenant_chat/"))

    emp_client = APIClient()
    emp_client.force_authenticate(user=emp)
    resp = emp_client.get(reverse("tenant_chat_message_attachment", kwargs={"pk": sent.data["id"]}))
    assert resp.status_code == 302 and resp["Cache-Control"] == "private, no-store"
    assert f"/{buckets['media']}/{quote(key)}?" in resp["Location"]
    status, headers, body = _get(resp["Location"], Origin="null")  # what fetch() sends after the redirect
    assert status == 200 and body == pdf
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Disposition"] == "inline; filename*=utf-8''%D8%AA%D9%82%D8%B1%D9%8A%D8%B1.pdf"


@pytest.mark.django_db
def test_backup_round_trip_live(r2, buckets, tmp_path, settings):
    from settings import services

    settings.BACKUP_ROOT = tmp_path / "work"
    live = tmp_path / "live.sqlite3"

    def write(value):
        con = sqlite3.connect(live)
        con.execute("create table if not exists t (v text)")
        con.execute("delete from t")
        con.execute("insert into t values (?)", (value,))
        con.commit()
        con.close()

    def read(path):
        con = sqlite3.connect(path)
        try:
            return con.execute("select v from t").fetchone()[0]
        finally:
            con.close()

    write("stored")
    with mock.patch.object(services, "_ensure_sqlite_backend", return_value=(live, "sqlite3")):
        backup = services.create_database_backup()
        assert backup.file.name in _keys(buckets["backups"])
        assert backup.file.name not in _keys(buckets["media"])
        assert backup_storage().size(backup.file.name) == backup.file_size

        write("changed")
        snapshot = services.restore_database_backup(backup)
        assert read(live) == "stored" and read(snapshot) == "changed"

        services.delete_backup(backup)
        assert backup.file.name not in _keys(buckets["backups"])


def _tone_wav() -> bytes:
    import math
    import struct

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000)))
                                 for i in range(8000)))
    return buf.getvalue()


@pytest.mark.django_db
def test_recording_uploaded_by_the_worker_plays_from_the_bucket_live(r2, buckets, node, setup, admin_client, tmp_path):  # noqa: F811
    """The PBX worker's stdlib SigV4 PUT (region auto, path-style) and the CRM's presigned GET meet on one bucket."""
    from telephony.models import PbxCall

    assert PBX_PY.exists(), f"CRM-pbx venv missing: {PBX_PY}"
    node.push(*inbound_call(node, setup["tenant"]))
    item = recording(setup["tenant"], str(PbxCall.objects.get().id), fmt="wav")
    audio = _tone_wav()
    item.update(bytes=len(audio), sha256=hashlib.sha256(audio).hexdigest())
    spool = tmp_path / "rec.wav"
    spool.write_bytes(audio)
    upload = subprocess.run(
        [str(PBX_PY), "-c",
         "import os, sys; from pathlib import Path; from loop_pbx.worker.storage import S3Store; "
         "S3Store(os.environ['E'], os.environ['B'], os.environ['A'], os.environ['S'], region='auto')"
         ".put(os.environ['K'], Path(os.environ['F']), os.environ['H'], 'wav')"],
        cwd=PBX_REPO, capture_output=True, text=True, timeout=60,
        env={**os.environ, "E": ENDPOINT, "B": buckets["recordings"], "A": ACCESS, "S": SECRET,
             "K": item["storage_key"], "F": str(spool), "H": item["sha256"]},
    )
    assert upload.returncode == 0, upload.stderr
    node.recordings(item)

    resp = admin_client.post(f"/api/v1/telephony/recordings/{item['recording_id']}/access/")
    assert resp.status_code == 200, resp.content
    url = resp.json()["data"]["url"]
    assert f"/{buckets['recordings']}/{item['storage_key']}?" in url and "X-Amz-Expires=300" in url
    status, headers, body = _get(url)
    assert status == 200 and hashlib.sha256(body).hexdigest() == item["sha256"]
    assert headers["Content-Type"] == "audio/wav"

    # Retention / admin delete removes the object from the same bucket.
    from telephony.models import PbxRecording
    from telephony.services.recordings import delete_object

    delete_object(PbxRecording.objects.get(pk=item["recording_id"]))
    assert item["storage_key"] not in _keys(buckets["recordings"])
    assert not recordings_storage().exists(item["storage_key"])


@pytest.mark.django_db
def test_migrate_media_to_r2_live(r2, buckets, tmp_path):
    files = {
        "company_library/2026/01/02/contract.pdf": b"%PDF-old",
        "tenant_chat/2026/01/02/photo.png": MINI_PNG_BYTES,
        "backups/db-20260101.sqlite3": b"SQLite format 3\x00",
        "backups/pre-restore-20260101.sqlite3": b"local only",
        "pbx_recordings/recording/5/linked/abc.webm": b"webm",
        "fonts/Amiri.ttf": b"font",
    }
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)

    def run(*args):
        out = StringIO()
        call_command("migrate_media_to_r2", "--source", str(tmp_path), *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    assert "would_copy=4" in run()
    assert "company_library/2026/01/02/contract.pdf" not in _keys(buckets["media"])  # a dry run copies nothing
    assert "copied=4" in run("--apply")
    assert {"company_library/2026/01/02/contract.pdf", "tenant_chat/2026/01/02/photo.png"} <= _keys(buckets["media"])
    assert "backups/db-20260101.sqlite3" in _keys(buckets["backups"])
    assert "recording/5/linked/abc.webm" in _keys(buckets["recordings"])
    assert not any(k.startswith(("fonts/", "backups/pre-restore")) for b in buckets.values() for k in _keys(b))
    assert storages["default"].open("company_library/2026/01/02/contract.pdf").read() == b"%PDF-old"
    rerun = run("--apply")
    assert "copied=0" in rerun and "present=4" in rerun
