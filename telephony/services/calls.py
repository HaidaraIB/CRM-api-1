"""
Click-to-call (spec section 6; CRM-pbx contract section 6).

``place_call`` applies the CRM's policy, pre-creates the ``PbxCall`` (status
``queued``, a uuid4 id the node will use as the call id), and asks the tenant's
home node to originate. The node then enforces live CPS/concurrency, rings the
agent's own phone, and only when the agent answers dials the destination; the
compiled dialplan re-checks the destination and caller id (defense in depth).

Policy, in order (the view already enforced auth, subscription, the plan
feature and the integration kill switch):

1. the tenant is provisioned, live, homed on a node, and outbound is not suspended;
2. the caller has an active extension of their own;
3. the destination is E.164 and matches an enabled outbound route (the allowlist).
   An extension with its own verified SIM uses that number and its trunk; otherwise
   the route's caller id and trunk. Anything the allowlist misses is refused;
4. the CRM contact, if given, is a lead the caller may see;
5. the monthly ``monthly_pbx_minutes`` cap is not exhausted.
"""

from __future__ import annotations

import logging
import re
import uuid

from django.db import transaction
from django.utils import timezone

from .. import contract
from ..models import PbxCall, PbxExtension, PbxOutboundRoute, PbxTenant, PbxTrunk
from . import pbx_worker_client as worker
from .compiler import CompileError, validate_pattern

logger = logging.getLogger(__name__)

RING_TIMEOUT_S = 30
MAX_DURATION_S = 7200


class CallRefused(Exception):
    """A policy refusal: ``code`` is stable for clients, ``status`` the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# Worker error codes → (http status, client code). Live limits are retryable (429).
_WORKER_ERRORS = {
    "concurrency_limit": (429, "concurrency_limit"),
    "cps_limit": (429, "cps_limit"),
    "tenant_suspended": (409, "outbound_suspended"),
    "extension_unavailable": (409, "phone_not_registered"),
    "unknown_extension": (409, "phone_not_registered"),
    "unknown_tenant": (503, "pbx_unavailable"),
    "node_unavailable": (503, "pbx_unavailable"),
    "ari_unavailable": (503, "pbx_unavailable"),
}


def normalise_destination(raw) -> str:
    value = re.sub(r"[\s\-().]", "", str(raw or ""))
    if value.startswith("00"):
        value = "+" + value[2:]
    if not contract.E164_RE.match(value):
        raise CallRefused("invalid_destination", "Enter the number in international format, e.g. +9647701234567.", 400)
    return value


def _matches(route: PbxOutboundRoute, number: str) -> bool:
    for item in route.patterns or []:
        try:
            prefix, digits = validate_pattern(item)
        except CompileError:
            continue
        if number.startswith(prefix) and len(number) == len(prefix) + digits:
            return True
    return False


def route_for(tenant: PbxTenant, number: str) -> PbxOutboundRoute | None:
    """The route the dialplan would use: first enabled route by priority whose pattern matches."""
    routes = (
        PbxOutboundRoute.objects.filter(company_id=tenant.company_id, enabled=True)
        .exclude(trunk__status=PbxTrunk.Status.DISABLED)
        .select_related("caller_id", "trunk")
        .order_by("priority", "name")
    )
    for route in routes:
        if _matches(route, number):
            if route.caller_id.verified_at is None or route.caller_id.company_id != tenant.company_id:
                return None  # the compiler skips such a route, so the call would be rejected
            return route
    return None


def contactable_clients(user):
    """Leads the user may act on; mirrors crm.views.ClientViewSet.get_queryset."""
    from crm.models import Client

    qs = Client.objects.filter(company_id=user.company_id)
    if (
        user.is_admin() or user.is_reception() or user.is_data_entry() or user.is_call_center()
        or (user.is_supervisor() and user.supervisor_has_permission("manage_leads"))
    ):
        return qs
    if user.is_assigned_clinical_staff():
        return qs.filter(assigned_to=user)
    return qs.none()


def user_extension(user) -> PbxExtension | None:
    return (
        PbxExtension.objects.filter(company_id=user.company_id, user=user, status=PbxExtension.Status.ACTIVE)
        .select_related("outbound_number", "outbound_number__trunk")
        .first()
    )


def personal_outbound(ext: PbxExtension):
    """``(caller_id, trunk)`` when this extension calls out on its own SIM, else None."""
    number = ext.outbound_number
    if number is None or number.verified_at is None or number.company_id != ext.company_id:
        return None
    if number.trunk.status == PbxTrunk.Status.DISABLED or not contract.E164_RE.match(number.e164 or ""):
        return None
    return number.e164, number.trunk


def place_call(user, *, destination, crm_contact_id=None) -> PbxCall:
    from subscriptions.entitlements import build_company_entitlements, require_monthly_usage, resolve_quota_limit

    company = user.company
    tenant = PbxTenant.objects.select_related("home_node").filter(company=company).first()
    if tenant is None or tenant.status in (PbxTenant.Status.PENDING, PbxTenant.Status.FAILED,
                                           PbxTenant.Status.DEPROVISIONED) or tenant.home_node is None:
        raise CallRefused("not_provisioned", "The phone system is not ready for your company yet.")
    if tenant.outbound_suspended or tenant.status == PbxTenant.Status.SUSPENDED:
        raise CallRefused("outbound_suspended", "Outbound calling is suspended for your company.")
    ext = user_extension(user)
    if ext is None:
        raise CallRefused("no_extension", "You do not have a phone extension.", 403)

    number = normalise_destination(destination)
    route = route_for(tenant, number)
    if route is None:
        raise CallRefused("destination_not_allowed", "Calls to this number are not allowed.", 403)
    personal = personal_outbound(ext)
    caller_id, trunk = personal if personal is not None else (route.caller_id.e164, route.trunk)

    contact = None
    if crm_contact_id not in (None, ""):
        try:
            contact = contactable_clients(user).filter(pk=int(crm_contact_id)).first()
        except (TypeError, ValueError):
            contact = None
        if contact is None:
            raise CallRefused("invalid_contact", "Lead not found.", 404)

    require_monthly_usage(
        company, "monthly_pbx_minutes", 1,
        message="Your plan's monthly calling minutes are used up.", error_key="monthly_pbx_minutes",
    )
    ent = build_company_entitlements(company)
    limits = {
        "max_concurrent_calls": resolve_quota_limit(ent, "max_pbx_concurrent_calls"),
        "max_cps": resolve_quota_limit(ent, "max_pbx_cps"),
    }
    # None means unlimited in entitlements; the node needs a number.
    limits = {k: (v if v is not None else 10000) for k, v in limits.items()}

    call = PbxCall.objects.create(
        id=uuid.uuid4(),
        company=company,
        tenant=tenant,
        direction=PbxCall.Direction.OUTBOUND,
        origin="crm",
        status=PbxCall.Status.QUEUED,
        from_party=ext.number,
        to_party=number,
        extension=ext,
        trunk=trunk,
        crm_contact=contact,
        started_at=timezone.now(),
    )
    payload = {
        "call_id": str(call.id),
        "tenant": tenant.tag,
        "extension": ext.number,
        "destination": number,
        "caller_id": caller_id,
        "ring_timeout_s": RING_TIMEOUT_S,
        "max_duration_s": MAX_DURATION_S,
        "limits": limits,
    }
    if contact is not None:
        payload["crm_contact_id"] = contact.pk
    try:
        worker.originate(tenant.home_node, payload)
    except worker.WorkerError as exc:
        status, code = _WORKER_ERRORS.get(exc.code, (503, "pbx_unavailable"))
        PbxCall.objects.filter(pk=call.pk, status=PbxCall.Status.QUEUED).update(
            status=PbxCall.Status.FAILED, ended_at=timezone.now(), updated_at=timezone.now()
        )
        logger.info("telephony: originate %s refused by node: %s", call.pk, exc.code)
        raise CallRefused(code, _MESSAGES.get(code, "The call could not be placed."), status)
    _bump(company.id)
    return call


_MESSAGES = {
    "concurrency_limit": "All of your company's phone lines are busy. Try again shortly.",
    "cps_limit": "Too many calls started at once. Try again in a moment.",
    "outbound_suspended": "Outbound calling is suspended for your company.",
    "phone_not_registered": "Your phone is not connected. Open the softphone or check your desk phone.",
    "pbx_unavailable": "The phone system is unavailable. Try again shortly.",
}


def hang_up(user, call: PbxCall) -> None:
    if call.is_terminal:
        raise CallRefused("call_not_active", "This call has already ended.")
    tenant = call.tenant
    try:
        worker.hangup(tenant.home_node, call_id=call.id, tenant_tag=tenant.tag)
    except worker.WorkerError as exc:
        if exc.code == "call_not_active":
            raise CallRefused("call_not_active", "This call has already ended.")
        raise CallRefused("pbx_unavailable", _MESSAGES["pbx_unavailable"], 503)


def _bump(company_id) -> None:
    from sync.version import bump_company_slice

    transaction.on_commit(lambda: bump_company_slice("telephony", company_id))
