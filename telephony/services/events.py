"""
Ingest of node-reported call-state events (CRM-pbx contract §5).

Delivery is at-least-once and may be out of order, so:

* every event is stored once in ``PbxCallEvent`` (primary key = event_id); a
  replay is reported as a duplicate and changes nothing;
* call state only moves forward: live states can be refined, and the first
  ``call.ended`` fixes the terminal status — a late ``call.ringing`` is stored but
  cannot reopen the call;
* ``call.ended`` carries every leg, so a lost intermediate event cannot leave
  the call wrong;
* a node may only report for tenants homed on it (``tenant_not_on_node``).

Side effects per accepted batch: one ``telephony`` slice bump per company (the
batch is the debounce: nodes push about once a second), ``telephony.event``
frames to the agents involved, and CRM timeline linking on ``call.ended``.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .. import contract
from ..models import (
    PbxCall,
    PbxCallEvent,
    PbxCallLeg,
    PbxExtension,
    PbxNode,
    PbxQueue,
    PbxRecording,
    PbxTenant,
    PbxTrunk,
)
from .emergency import is_emergency

logger = logging.getLogger(__name__)

CALL_EVENT_TYPES = {
    "call.created", "call.ringing", "call.answered", "call.held", "call.transferred", "call.ended",
    "call.queued", "call.dequeued",
}
EVENT_TYPES = CALL_EVENT_TYPES | {"agent.status_changed", "gateway.status_changed", "quota.warning", "fraud.alert"}
TERMINAL_STATUSES = {"answered", "busy", "no_answer", "failed", "abandoned", "missed", "cancelled"}
LEG_DISPOSITIONS = {"answered", "busy", "no_answer", "failed", "cancelled"}
MAX_BATCH = 200

_PARTY_RE = re.compile(r"^(\+[1-9][0-9]{6,14}|[1-9][0-9]{1,5}|anonymous)$")
# Frames sent to the agents on a call for these transitions.
_AGENT_FRAMES = {"call.ringing", "call.answered", "call.transferred", "call.ended"}


class InvalidEvent(ValueError):
    pass


@dataclass
class BatchResult:
    accepted: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "duplicates": self.duplicates, "rejected": self.rejected}


# ---------------------------------------------------------------------------
# Validation (mirror of CRM-pbx/contract/schemas/events-batch.schema.json)
# ---------------------------------------------------------------------------


def _uuid(value, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise InvalidEvent(f"{name} must be a UUID")


def _ts(value, name: str) -> datetime:
    dt = parse_datetime(value) if isinstance(value, str) else None
    if dt is None or dt.tzinfo is None:
        raise InvalidEvent(f"{name} must be an ISO-8601 UTC timestamp")
    return dt


def _party(value, name: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not _PARTY_RE.match(value):
        raise InvalidEvent(f"{name} is not a valid party")
    return value


def _ext(value, name: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not contract.EXTENSION_RE.match(value):
        raise InvalidEvent(f"{name} is not a valid extension")
    return value


def validate_event(ev) -> dict:
    if not isinstance(ev, dict):
        raise InvalidEvent("event must be an object")
    out = {
        "event_id": _uuid(ev.get("event_id"), "event_id"),
        "type": ev.get("type"),
        "tenant": ev.get("tenant"),
        "occurred_at": _ts(ev.get("occurred_at"), "occurred_at"),
        "seq": ev.get("seq"),
        "data": ev.get("data"),
    }
    if out["type"] not in EVENT_TYPES:
        raise InvalidEvent("unknown event type")
    if not isinstance(out["tenant"], str) or not contract.TENANT_TAG_RE.match(out["tenant"]):
        raise InvalidEvent("invalid tenant tag")
    if not isinstance(out["seq"], int) or isinstance(out["seq"], bool) or out["seq"] < 1:
        raise InvalidEvent("seq must be a positive integer")
    if not isinstance(out["data"], dict):
        raise InvalidEvent("data must be an object")
    if out["type"] in CALL_EVENT_TYPES:
        out["call_id"] = _uuid(ev.get("call_id"), "call_id")
        linked = ev.get("linked_id")
        if not isinstance(linked, str) or not (1 <= len(linked) <= 64):
            raise InvalidEvent("linked_id is required for call events")
        out["linked_id"] = linked
    return out


def _leg(raw) -> dict:
    if not isinstance(raw, dict):
        raise InvalidEvent("leg must be an object")
    uniqueid = raw.get("uniqueid")
    if not isinstance(uniqueid, str) or not (1 <= len(uniqueid) <= 64):
        raise InvalidEvent("leg.uniqueid is required")
    disposition = raw.get("disposition") or ""
    if disposition and disposition not in LEG_DISPOSITIONS:
        raise InvalidEvent("invalid leg disposition")
    return {
        "uniqueid": uniqueid,
        "endpoint": str(raw.get("endpoint") or "")[:80],
        "extension": _ext(raw.get("extension"), "leg.extension"),
        "source": _party(raw.get("source"), "leg.source"),
        "destination": _party(raw.get("destination"), "leg.destination"),
        "disposition": disposition,
        "started_at": _ts(raw["started_at"], "leg.started_at") if raw.get("started_at") else None,
        "answered_at": _ts(raw["answered_at"], "leg.answered_at") if raw.get("answered_at") else None,
        "ended_at": _ts(raw["ended_at"], "leg.ended_at") if raw.get("ended_at") else None,
    }


# ---------------------------------------------------------------------------
# Applying events
# ---------------------------------------------------------------------------


@dataclass
class _Effects:
    """Collected while applying a batch; fired once after commit."""

    companies: set = field(default_factory=set)
    frames: set = field(default_factory=set)  # (user_id, type, call_id)
    ended_calls: set = field(default_factory=set)
    minutes: dict = field(default_factory=dict)  # company_id -> outbound minutes (monthly_pbx_minutes)
    trunk_health: list = field(default_factory=list)  # (trunk_id, old, new): down / back up, for alerts
    emergency_calls: set = field(default_factory=set)  # calls to an emergency number: alert the admins


def _extension(company_id, number: str):
    if not number:
        return None
    return PbxExtension.objects.filter(company_id=company_id, number=number).first()


def _get_or_create_call(tenant: PbxTenant, ev: dict) -> PbxCall:
    company_id = tenant.company_id
    call = (
        PbxCall.objects.select_for_update().filter(company_id=company_id, pk=ev["call_id"]).first()
        or PbxCall.objects.select_for_update().filter(company_id=company_id, linked_id=ev["linked_id"]).first()
    )
    if call is None:
        if PbxCall.objects.filter(pk=ev["call_id"]).exists():
            # The id exists under another company: never attach to it.
            raise InvalidEvent("call_id belongs to another tenant")
        call = PbxCall.objects.create(
            id=ev["call_id"],
            company_id=company_id,
            tenant=tenant,
            linked_id=ev["linked_id"],
            started_at=ev["occurred_at"],
        )
        PbxRecording.objects.filter(company_id=company_id, call__isnull=True, call_uuid=call.id).update(call=call)
    if not call.linked_id:
        call.linked_id = ev["linked_id"]
    if call.started_at is None or ev["occurred_at"] < call.started_at:
        call.started_at = ev["occurred_at"]
    return call


def _upsert_leg(call: PbxCall, leg: dict) -> PbxCallLeg:
    obj, _ = PbxCallLeg.objects.get_or_create(
        company_id=call.company_id, uniqueid=leg["uniqueid"], defaults={"call": call}
    )
    if obj.call_id != call.id:
        raise InvalidEvent("leg belongs to another call")
    for name in ("endpoint", "source", "destination", "disposition"):
        if leg[name]:
            setattr(obj, name, leg[name])
    for name in ("started_at", "answered_at", "ended_at"):
        if leg[name] and getattr(obj, name) is None:
            setattr(obj, name, leg[name])
    if leg["extension"]:
        obj.extension = _extension(call.company_id, leg["extension"])
    # Endpoint names are tenant-qualified; refuse a leg naming another tenant.
    owner = contract.owner_realm_key(obj.endpoint) if obj.endpoint else None
    if obj.endpoint and owner != call.tenant.realm_key:
        raise InvalidEvent("leg endpoint belongs to another tenant")
    obj.save()
    # Nodes never need trunk UUIDs: a trunk leg is named t<key>_trk_<id8>
    # (contract section 1), which identifies the trunk within this tenant.
    trunk_prefix = f"{call.tenant.tag}_trk_"
    if call.trunk_id is None and obj.endpoint.startswith(trunk_prefix):
        short = obj.endpoint[len(trunk_prefix):]
        call.trunk = next(
            (t for t in PbxTrunk.objects.filter(company_id=call.company_id) if contract.short_id(t.id) == short), None
        )
    return obj


def _queue(tenant: PbxTenant, name) -> PbxQueue | None:
    """A queue named by the node (``t<key>_q_<id8>``), resolved in this tenant only."""
    prefix = f"{tenant.tag}_q_"
    if not isinstance(name, str) or not re.fullmatch(rf"{re.escape(prefix)}[0-9a-f]{{8}}", name):
        raise InvalidEvent("queue must be this tenant's t<key>_q_<id8>")
    short = name[len(prefix):]
    return next((q for q in PbxQueue.objects.filter(company_id=tenant.company_id) if contract.short_id(q.id) == short),
                None)


def _close_wait(call: PbxCall, at, outcome: str) -> None:
    """The caller stopped waiting (answered, overflowed or hung up); the first reason wins."""
    if call.queued_at is not None and call.queue_left_at is None:
        call.queue_left_at = max(at, call.queued_at)
        call.queue_outcome = outcome


def _agent_users(call: PbxCall) -> set:
    ids = set(
        PbxCallLeg.objects.filter(call=call, extension__user__isnull=False).values_list("extension__user_id", flat=True)
    )
    for ext in (call.extension, call.answered_by):
        if ext is not None and ext.user_id:
            ids.add(ext.user_id)
    return ids


def _apply_call_event(tenant: PbxTenant, ev: dict, effects: _Effects) -> PbxCall:
    call = _get_or_create_call(tenant, ev)
    data, kind, at = ev["data"], ev["type"], ev["occurred_at"]
    company_id = tenant.company_id

    if kind == "call.created":
        direction = data.get("direction")
        if direction not in ("inbound", "outbound", "internal"):
            raise InvalidEvent("invalid direction")
        to_party = _party(data.get("to"), "to")
        if is_emergency(to_party):
            # The node reports a short code as internal (it looks like an extension); no
            # extension may use an emergency number, so this left the tenant (services/emergency.py).
            direction = "outbound"
            effects.emergency_calls.add(call.id)
        call.direction = call.direction or direction
        call.origin = call.origin or (data.get("origin") if data.get("origin") in ("crm", "pbx") else "pbx")
        call.from_party = call.from_party or _party(data.get("from"), "from")
        call.to_party = call.to_party or to_party
        did = data.get("did") or ""
        if did and not contract.E164_RE.match(did):
            raise InvalidEvent("invalid did")
        call.did = call.did or did
        if data.get("trunk_id") and call.trunk_id is None:
            call.trunk = PbxTrunk.objects.filter(company_id=company_id, pk=_uuid(data["trunk_id"], "trunk_id")).first()
        if data.get("extension") and call.extension_id is None:
            call.extension = _extension(company_id, _ext(data["extension"], "extension"))
        # Only a CRM-originated call may name a CRM contact (the CRM supplied it).
        contact = data.get("crm_contact_id")
        if isinstance(contact, int) and not isinstance(contact, bool) and call.origin == "crm" and call.crm_contact_id is None:
            from crm.models import Client

            call.crm_contact = Client.objects.filter(company_id=company_id, pk=contact).first()
        if call.status == PbxCall.Status.QUEUED:
            call.status = PbxCall.Status.RINGING
        if call.is_terminal:
            # call.ended overtook this event; now that the direction and parties
            # are known, the CRM link it could not make then can be made.
            effects.ended_calls.add(call.id)
    elif kind == "call.ringing":
        _upsert_leg(call, _leg(data.get("leg")))
        if call.status == PbxCall.Status.QUEUED:
            call.status = PbxCall.Status.RINGING
    elif kind == "call.answered":
        leg = _upsert_leg(call, _leg(data.get("leg")))
        if call.answered_at is None or at < call.answered_at:
            call.answered_at = at
        _close_wait(call, at, "answered")
        if not call.is_terminal:
            call.status = PbxCall.Status.IN_PROGRESS
        if leg.extension_id and call.answered_by_id is None:
            call.answered_by_id = leg.extension_id
    elif kind == "call.queued":
        # A later queue replaces an earlier one (IVR -> queue A overflow -> IVR -> queue B).
        queue = _queue(tenant, data.get("queue"))
        if call.queued_at is None or at > call.queued_at:
            call.queue, call.queued_at, call.queue_left_at, call.queue_outcome = queue, at, None, ""
            if call.answered_at is not None and call.answered_at >= at:
                _close_wait(call, call.answered_at, "answered")  # call.answered overtook this event
            elif call.is_terminal and call.ended_at is not None:
                _close_wait(call, call.ended_at, "answered" if call.status == PbxCall.Status.ANSWERED else "abandoned")
    elif kind == "call.dequeued":
        _queue(tenant, data.get("queue"))
        if data.get("reason") != "overflow":
            raise InvalidEvent("invalid dequeue reason")
        _close_wait(call, at, "overflow")
    elif kind == "call.held":
        if not call.is_terminal:
            call.on_hold = bool(data.get("held"))
    elif kind == "call.transferred":
        # A SIP REFER the node saw complete. Stored in the event log (the call
        # detail lists it); the target agent gets a frame so their UI re-reads.
        if data.get("mode") not in ("blind", "attended"):
            raise InvalidEvent("invalid transfer mode")
        _ext(data.get("from_extension"), "from_extension")
        to_ext = _ext(data.get("to_extension"), "to_extension")
        to = _party(data.get("to"), "to")
        if bool(to_ext) == bool(to):
            raise InvalidEvent("transfer needs exactly one of to_extension or to")
        target = _extension(company_id, to_ext)
        if target is not None and target.user_id:
            effects.frames.add((target.user_id, kind, str(call.id)))
    elif kind == "call.ended":
        status = data.get("status")
        if status not in TERMINAL_STATUSES:
            raise InvalidEvent("invalid terminal status")
        legs = data.get("legs")
        if not isinstance(legs, list) or len(legs) > 50:
            raise InvalidEvent("legs must be a list of at most 50")
        for raw in legs:
            _upsert_leg(call, _leg(raw))
        if not call.is_terminal:
            # First call.ended wins; a replayed or late duplicate cannot rewrite history.
            call.status = status
            call.ended_at = at
            call.on_hold = False
            cause = data.get("hangup_cause")
            call.hangup_cause = cause if isinstance(cause, int) and 0 <= cause <= 127 else None
            for name in ("duration_s", "billsec_s"):
                value = data.get(name)
                setattr(call, name, value if isinstance(value, int) and value >= 0 else None)
            call.recorded = bool(data.get("recorded"))
            _close_wait(call, at, "answered" if status == "answered" else "abandoned")
            answered_by = _ext(data.get("answered_by_extension"), "answered_by_extension")
            if answered_by:
                call.answered_by = _extension(company_id, answered_by) or call.answered_by
            effects.ended_calls.add(call.id)
            if call.direction == PbxCall.Direction.OUTBOUND and call.billsec_s and not is_emergency(call.to_party):
                # Billed like carriers do: every started minute of talk time. Emergency calls never
                # count against the plan's minutes (they bypass every limit).
                effects.minutes[company_id] = effects.minutes.get(company_id, 0) + -(-call.billsec_s // 60)
    call.save()

    if kind in _AGENT_FRAMES:
        for user_id in _agent_users(call):
            effects.frames.add((user_id, kind, str(call.id)))
    return call


def _apply_other_event(tenant: PbxTenant, ev: dict, node: PbxNode | None = None, effects: _Effects | None = None) -> None:
    data, at = ev["data"], ev["occurred_at"]
    if ev["type"] == "agent.status_changed":
        number = _ext(data.get("extension"), "extension")
        if not number or not isinstance(data.get("registered"), bool):
            raise InvalidEvent("agent.status_changed needs extension and registered")
        device = data.get("device") if data.get("device") in ("webrtc", "desk_phone", "softphone") else ""
        # Older reports never overwrite newer presence.
        PbxExtension.objects.filter(company_id=tenant.company_id, number=number).exclude(
            registered_changed_at__gt=at
        ).update(registered=data["registered"], registered_device=device, registered_changed_at=at)
    elif ev["type"] == "gateway.status_changed":
        status = data.get("status")
        if status not in ("ready", "degraded", "down"):
            raise InvalidEvent("invalid gateway status")
        trunks = PbxTrunk.objects.filter(company_id=tenant.company_id)
        if data.get("trunk"):
            # The node names a trunk by its endpoint (contract section 1); resolve it in this tenant only.
            endpoint, prefix = str(data["trunk"]), f"{tenant.tag}_trk_"
            if not endpoint.startswith(prefix):
                raise InvalidEvent("trunk belongs to another tenant")
            short = endpoint[len(prefix):]
            ids = [t.pk for t in trunks if contract.short_id(t.id) == short]
            trunks = trunks.filter(pk__in=ids)
        else:
            trunks = trunks.filter(pk=_uuid(data.get("trunk_id"), "trunk_id"))
        current = trunks.exclude(health_changed_at__gt=at)
        if effects is not None:
            # Going down, or coming back from down, alerts the company's admins (spec §12).
            effects.trunk_health += [
                (t.pk, t.health, status) for t in current
                if t.health != status and "down" in (t.health, status) and t.health
            ]
        current.update(health=status, health_reason=str(data.get("reason") or "")[:200], health_changed_at=at)
    elif ev["type"] == "fraud.alert":
        from .fraud import record_alert

        record_alert(tenant, node, ev)
    elif ev["type"] == "quota.warning":
        # A live plan limit refused a call on the node. Stored in the event log; the
        # Usage report counts recent ones (services/usage.py limit_hits).
        if data.get("quota") not in ("max_pbx_concurrent_calls", "max_pbx_cps"):
            raise InvalidEvent("invalid quota")
        for name in ("limit", "observed"):
            value = data.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidEvent(f"{name} must be a non-negative integer")


def ingest_batch(node: PbxNode, payload: dict) -> BatchResult:
    """Apply one events batch from ``node``. Never raises for a bad single event."""
    result = BatchResult()
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or not (1 <= len(events) <= MAX_BATCH):
        raise InvalidEvent(f"events must be a list of 1-{MAX_BATCH}")

    tenants: dict[str, PbxTenant | None] = {}
    effects = _Effects()

    for raw in events:
        raw_id = raw.get("event_id") if isinstance(raw, dict) else None
        try:
            ev = validate_event(raw)
        except InvalidEvent as exc:
            if raw_id:
                result.rejected.append({"id": str(raw_id), "code": "invalid_payload", "detail": str(exc)[:500]})
            continue

        tag = ev["tenant"]
        if tag not in tenants:
            tenants[tag] = PbxTenant.objects.filter(realm_key=contract.realm_key_from_tag(tag)).first()
        tenant = tenants[tag]
        if tenant is None:
            result.rejected.append({"id": ev["event_id"], "code": "unknown_tenant"})
            continue
        if node.id not in (tenant.home_node_id, tenant.draining_node_id):
            # A node a tenant just moved away from still reports the calls it carried.
            result.rejected.append({"id": ev["event_id"], "code": "tenant_not_on_node"})
            continue

        try:
            with transaction.atomic():
                stored = PbxCallEvent.objects.create(
                    id=ev["event_id"],
                    company_id=tenant.company_id,
                    tenant=tenant,
                    node=node,
                    type=ev["type"],
                    seq=ev["seq"],
                    occurred_at=ev["occurred_at"],
                    data=ev["data"],
                )
                if ev["type"] in CALL_EVENT_TYPES:
                    call = _apply_call_event(tenant, ev, effects)
                    stored.call = call
                    stored.save(update_fields=["call"])
                else:
                    _apply_other_event(tenant, ev, node, effects)
        except IntegrityError:
            if PbxCallEvent.objects.filter(pk=ev["event_id"]).exists():
                result.duplicates.append(ev["event_id"])
            else:
                result.rejected.append({"id": ev["event_id"], "code": "invalid_payload", "detail": "conflict"})
            continue
        except InvalidEvent as exc:
            result.rejected.append({"id": ev["event_id"], "code": "invalid_payload", "detail": str(exc)[:500]})
            continue
        result.accepted.append(ev["event_id"])
        effects.companies.add(tenant.company_id)

    PbxNode.objects.filter(pk=node.pk).update(last_seen_at=timezone.now())
    _fire_effects(effects)
    return result


def _fire_effects(effects: _Effects) -> None:
    from realtime.publish import publish_telephony_event
    from sync.version import bump_company_slice

    from .emergency import notify_emergency
    from .linking import link_call_to_crm
    from .missed import notify_missed

    for call_id in effects.emergency_calls:
        try:
            notify_emergency(call_id)
        except Exception:
            logger.exception("telephony: emergency-call alert failed for call %s", call_id)

    for call_id in effects.ended_calls:
        try:
            link_call_to_crm(call_id)
        except Exception:  # linking is best effort; the call row is already stored
            logger.exception("telephony: CRM linking failed for call %s", call_id)
        try:
            # After linking, so a caller matched by phone is named in the push.
            notify_missed(call_id)
        except Exception:
            logger.exception("telephony: missed-call push failed for call %s", call_id)
    for company_id in effects.companies:
        bump_company_slice("telephony", company_id)
    if effects.minutes:
        from companies.models import Company
        from subscriptions.entitlements import increment_monthly_usage

        for company in Company.objects.filter(pk__in=effects.minutes):
            increment_monthly_usage(company, "monthly_pbx_minutes", effects.minutes[company.pk])
    if effects.trunk_health:
        from .alerts import notify_trunk_health

        for trunk_id, old, new in effects.trunk_health:
            try:
                notify_trunk_health(trunk_id, old, new)
            except Exception:
                logger.exception("telephony: trunk health alert failed for trunk %s", trunk_id)
    for user_id, kind, call_id in effects.frames:
        publish_telephony_event(user_id, kind, call_id)
