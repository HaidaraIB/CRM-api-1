"""ZYCOO CDR recording_filename parsing."""

import json
import secrets

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from integrations.models import (
    PbxCallDisposition,
    PbxCallRecord,
    PbxEventType,
    PbxRecordingStatus,
    PbxSettings,
)
from integrations.services.pbx_handler import process_pbx_payload
from integrations.services.pbx_recording_service import (
    apply_recording_path_from_cdr,
    build_recording_download_url,
    get_playback_url,
    list_pending_recording_jobs,
)
from integrations.services.zycoo_parser import parse_zycoo_payload


def test_cdr_recording_filename_parsed():
    body = {
        "Event": "Cdr",
        "UniqueID": "1780515827.110",
        "Linkedid": "1780515827.110",
        "Source": '"07809418884" <07809418884>',
        "Destination": "104",
        "Disposition": "NO ANSWER",
        "Duration": "27",
        "BillableSeconds": "0",
        "CallType": "incoming",
        "recording_filename": (
            "/var/spool/asterisk/monitor/recording/20260603/104/"
            "1780515827.110-07809418884-104-20260603-224349.wav"
        ),
    }
    parsed = parse_zycoo_payload(json.dumps(body).encode(), "application/json")
    assert parsed["event_type"] == PbxEventType.HANGUP
    assert parsed["caller"] == "07809418884"
    assert parsed["disposition"] == PbxCallDisposition.NO_ANSWER
    assert parsed["duration_sec"] == 27
    assert "1780515827.110-07809418884" in parsed["recording_path"]


def test_build_recording_url_from_zycoo_path():
    settings = PbxSettings(pbx_host="shatalarab.uae.zycoo.com")
    path = (
        "/var/spool/asterisk/monitor/recording/20260603/104/"
        "1780515827.110-07809418884-104-20260603-224349.wav"
    )
    url = build_recording_download_url(settings, path, "")
    assert url.startswith("https://shatalarab.uae.zycoo.com/monitor/recording/")
    assert url.endswith(".wav")


def test_build_recording_url_lan_pbx_uses_http():
    settings = PbxSettings(pbx_host="192.168.1.100")
    path = (
        "/var/spool/asterisk/monitor/recording/20260603/104/"
        "1780515827.110-07809418884-104-20260603-224349.wav"
    )
    url = build_recording_download_url(settings, path, "")
    assert url.startswith("http://192.168.1.100/monitor/recording/")
    assert url.endswith(".wav")


@pytest.mark.django_db
def test_apply_recording_path_from_cdr_sets_download_url(company):
    settings = PbxSettings.objects.create(
        company=company,
        pbx_host="192.168.1.100",
        is_enabled=True,
    )
    record = PbxCallRecord.objects.create(
        company=company,
        uniqueid="1780515827.110",
        linkedid="1780515827.110",
    )
    path = (
        "/var/spool/asterisk/monitor/recording/20260603/104/"
        "1780515827.110-07809418884-104-20260603-224349.wav"
    )
    assert apply_recording_path_from_cdr(record, path, settings=settings) is True
    record.refresh_from_db()
    assert record.recording_path == path
    assert record.recording_url.startswith("http://192.168.1.100/monitor/recording/")
    assert record.recording_status == "pending"


@pytest.mark.django_db
def test_list_pending_recording_jobs_includes_download_url(company):
    settings = PbxSettings.objects.create(
        company=company,
        pbx_host="192.168.1.100",
        is_enabled=True,
    )
    path = (
        "/var/spool/asterisk/monitor/recording/20260603/104/"
        "1780515827.110-07809418884-104-20260603-224349.wav"
    )
    record = PbxCallRecord.objects.create(
        company=company,
        uniqueid="1780515827.110",
        linkedid="1780515827.110",
    )
    apply_recording_path_from_cdr(record, path, settings=settings)
    jobs = list_pending_recording_jobs(company.id)
    assert len(jobs) == 1
    assert jobs[0]["record_id"] == record.id
    assert jobs[0]["file"] == path
    assert jobs[0]["download_url"].startswith("http://192.168.1.100/monitor/recording/")


@pytest.mark.django_db(transaction=True)
def test_cdr_push_queues_recording_job(api_client, company):
    settings = PbxSettings.objects.create(
        company=company,
        pbx_host="192.168.1.100",
        is_enabled=True,
        webhook_token=secrets.token_urlsafe(32),
        connector_api_key=secrets.token_urlsafe(32),
    )
    body = {
        "Event": "Cdr",
        "UniqueID": "1780515827.110",
        "Linkedid": "1780515827.110",
        "Source": '"07809418884" <07809418884>',
        "Destination": "104",
        "Disposition": "ANSWERED",
        "Duration": "45",
        "BillableSeconds": "38",
        "CallType": "incoming",
        "recording_filename": (
            "/var/spool/asterisk/monitor/recording/20260603/104/"
            "1780515827.110-07809418884-104-20260603-224349.wav"
        ),
    }
    result = process_pbx_payload(
        settings,
        json.dumps(body).encode(),
        "application/json",
        source="connector",
    )
    assert result["ok"] is True
    record = PbxCallRecord.objects.get(pk=result["record_id"])
    assert record.recording_url.startswith("http://192.168.1.100/monitor/recording/")
    assert record.recording_status == PbxRecordingStatus.PENDING

    jobs_resp = api_client.get(
        "/api/integrations/pbx/connector/recording-jobs/",
        HTTP_X_CONNECTOR_KEY=settings.connector_api_key,
    )
    assert jobs_resp.status_code == 200
    jobs = jobs_resp.json()["data"]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["download_url"] == record.recording_url


@pytest.mark.django_db
def test_connector_upload_marks_recording_ready(api_client, company):
    settings = PbxSettings.objects.create(
        company=company,
        pbx_host="192.168.1.100",
        is_enabled=True,
        webhook_token=secrets.token_urlsafe(32),
        connector_api_key=secrets.token_urlsafe(32),
    )
    path = (
        "/var/spool/asterisk/monitor/recording/20260603/104/"
        "1780515827.110-07809418884-104-20260603-224349.wav"
    )
    record = PbxCallRecord.objects.create(
        company=company,
        uniqueid="1780515827.110",
        linkedid="1780515827.110",
    )
    apply_recording_path_from_cdr(record, path, settings=settings)
    wav = SimpleUploadedFile("recording.wav", b"RIFFfake", content_type="audio/wav")
    upload_resp = api_client.post(
        f"/api/integrations/pbx/connector/recordings/{record.id}/upload/",
        {"file": wav},
        HTTP_X_CONNECTOR_KEY=settings.connector_api_key,
        format="multipart",
    )
    assert upload_resp.status_code == 200
    record.refresh_from_db()
    assert record.recording_status == PbxRecordingStatus.READY
    assert record.recording_uploaded is True
    assert record.recording_storage_key
    assert get_playback_url(record) is not None
