"""ZYCOO CDR recording_filename parsing."""

import json

from integrations.services.pbx_handler import _build_recording_url
from integrations.services.zycoo_parser import parse_zycoo_payload
from integrations.models import PbxCallDisposition, PbxEventType, PbxSettings


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
    url = _build_recording_url(settings, path, "")
    assert url.startswith("https://shatalarab.uae.zycoo.com/monitor/recording/")
    assert url.endswith(".wav")
