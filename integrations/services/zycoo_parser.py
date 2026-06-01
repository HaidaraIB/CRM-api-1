"""Parse ZYCOO CooVox / Asterisk Push Event payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone as dt_timezone
from typing import Any
from urllib.parse import parse_qs

from django.utils import timezone

from integrations.models import (
    PbxCallDirection,
    PbxCallDisposition,
    PbxEventType,
)


def _first(data: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        val = data.get(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


def _parse_body(raw_body: bytes, content_type: str) -> dict[str, Any]:
    text = (raw_body or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    ct = (content_type or "").lower()
    if "json" in ct:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"payload": parsed}
        except json.JSONDecodeError:
            pass
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"payload": parsed}
        except json.JSONDecodeError:
            pass
    # form-urlencoded Asterisk-style key: value lines or query string
    if "=" in text and "\n" not in text:
        qs = parse_qs(text, keep_blank_values=True)
        return {k: (v[0] if len(v) == 1 else v) for k, v in qs.items()}
    result: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
        elif "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _map_event(raw_event: str) -> PbxEventType:
    ev = (raw_event or "").lower().replace(" ", "")
    if ev in ("newchannel", "newstate", "ringing", "incoming", "incomingcall", "dialbegin"):
        return PbxEventType.RINGING
    if ev in ("bridgeenter", "bridge", "answered", "answer", "pickup"):
        return PbxEventType.ANSWERED
    if ev in ("hangup", "callend", "callended", "cdr"):
        return PbxEventType.HANGUP
    if ev in ("missed", "noanswer", "no_answer"):
        return PbxEventType.MISSED
    if ev in ("agentlogin",):
        return PbxEventType.AGENT_LOGIN
    if ev in ("agentlogoff",):
        return PbxEventType.AGENT_LOGOFF
    return PbxEventType.OTHER


def _map_disposition(raw: str) -> PbxCallDisposition:
    d = (raw or "").upper()
    if d in ("ANSWERED", "ANSWER"):
        return PbxCallDisposition.ANSWERED
    if d in ("NO ANSWER", "NOANSWER"):
        return PbxCallDisposition.NO_ANSWER
    if d == "BUSY":
        return PbxCallDisposition.BUSY
    if d in ("FAILED", "CONGESTION"):
        return PbxCallDisposition.FAILED
    return PbxCallDisposition.UNKNOWN


def _infer_direction(data: dict[str, Any], caller: str, callee: str, extension: str) -> PbxCallDirection:
    explicit = _first(data, "Direction", "direction", "CallType", "call_type").lower()
    if explicit in ("inbound", "incoming", "in"):
        return PbxCallDirection.INBOUND
    if explicit in ("outbound", "outgoing", "out"):
        return PbxCallDirection.OUTBOUND
    if explicit == "internal":
        return PbxCallDirection.INTERNAL
    # Heuristic: external number on caller with local extension as callee → inbound
    caller_digits = "".join(c for c in caller if c.isdigit())
    if extension and callee == extension and len(caller_digits) >= 7:
        return PbxCallDirection.INBOUND
    if extension and caller == extension and len("".join(c for c in callee if c.isdigit())) >= 7:
        return PbxCallDirection.OUTBOUND
    return PbxCallDirection.INBOUND


def _parse_int(val: str) -> int:
    try:
        return max(0, int(float(val or 0)))
    except (TypeError, ValueError):
        return 0


def parse_zycoo_payload(raw_body: bytes, content_type: str = "") -> dict[str, Any]:
    """Return normalized PBX event dict from raw webhook body."""
    data = _parse_body(raw_body, content_type)
    raw_event = _first(data, "Event", "event", "Action", "action", "type", "Type")
    event_type = _map_event(raw_event)

    uniqueid = _first(data, "Uniqueid", "uniqueid", "UniqueID", "CallID", "call_id", "id")
    if not uniqueid:
        uniqueid = _first(data, "Linkedid", "linkedid") or f"unknown-{timezone.now().timestamp()}"

    caller = _first(data, "CallerIDNum", "calleridnum", "Caller", "caller", "From", "from", "src")
    callee = _first(
        data,
        "ConnectedLineNum",
        "connectedlinenum",
        "Callee",
        "callee",
        "To",
        "to",
        "dst",
        "DialString",
    )
    extension = _first(data, "Exten", "exten", "Extension", "extension", "Agent", "agent")
    if not extension and callee and len(callee) <= 6:
        extension = callee
    if not extension and caller and len(caller) <= 6:
        extension = caller

    direction = _infer_direction(data, caller, callee, extension)
    external_phone = caller if direction == PbxCallDirection.INBOUND else callee
    if direction == PbxCallDirection.OUTBOUND and not external_phone:
        external_phone = callee

    disposition = _map_disposition(_first(data, "Disposition", "disposition", "Status", "status"))
    duration_sec = _parse_int(_first(data, "Duration", "duration"))
    billsec = _parse_int(_first(data, "Billsec", "billsec", "TalkTime", "talk_time"))
    recording_path = _first(data, "RecordingFile", "recordingfile", "Recording", "recording")
    recording_url = _first(data, "RecordingUrl", "recording_url", "RecordingURL")

    now = timezone.now()
    return {
        "event_type": event_type,
        "raw_event": raw_event,
        "uniqueid": uniqueid,
        "direction": direction,
        "caller": caller,
        "callee": callee,
        "extension": extension,
        "external_phone": external_phone,
        "disposition": disposition,
        "duration_sec": duration_sec,
        "billsec": billsec,
        "recording_path": recording_path,
        "recording_url": recording_url,
        "started_at": now,
        "answered_at": now if event_type == PbxEventType.ANSWERED else None,
        "ended_at": now if event_type in (PbxEventType.HANGUP, PbxEventType.MISSED) else None,
        "raw_payload": data,
    }
