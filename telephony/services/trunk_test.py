"""
``POST /telephony/trunks/{id}/test`` (spec section 6: "check REGISTER/OPTIONS and
call routes"). One report of what stands between this trunk and working calls:

* ``config``    the latest config is live on the home node;
* ``options``   the node sends one SIP OPTIONS to the contact its own config has
                (contract section 6, trunk probe) and reports whether it answered;
* ``identify``  inbound calls can be identified (allowed source networks);
* ``numbers``   verified numbers on this trunk (unverified ones are never routed);
* ``inbound``   every verified number has an inbound route;
* ``outbound``  an enabled outbound route dials through this trunk with a verified
                caller id;
* ``register``  always ``skipped``: Loop trunks are identified by source address and
                never register (contract section 2).

Each check is ``ok``, ``warning``, ``failed`` or ``skipped`` with machine data; the
UI words them. The probe result is also kept as the trunk's ``health``.
"""

from __future__ import annotations

from django.utils import timezone

from .. import contract
from ..models import PbxInboundRoute, PbxOutboundRoute, PbxTenant, PbxTrunk
from . import pbx_worker_client
from .calls import CallRefused

OK, WARNING, FAILED, SKIPPED = "ok", "warning", "failed", "skipped"


def _check(check_id: str, status: str, **data) -> dict:
    return {"id": check_id, "status": status, **({"data": data} if data else {})}


def _probe(tenant: PbxTenant, trunk: PbxTrunk) -> dict:
    if tenant.home_node is None:
        return _check("options", SKIPPED, reason="not_provisioned")
    try:
        result = pbx_worker_client.probe_trunk(
            tenant.home_node, tenant_tag=tenant.tag, trunk_endpoint=contract.trunk_endpoint(tenant.realm_key, trunk.id)
        )
    except pbx_worker_client.WorkerError as exc:
        if exc.code == "unknown_trunk":
            return _check("options", FAILED, reason="not_on_node")
        return _check("options", FAILED, reason="pbx_unavailable")
    data = {k: result[k] for k in ("contact", "transport", "status_code", "rtt_ms") if k in result}
    reachable = bool(result.get("reachable"))
    now = timezone.now()
    PbxTrunk.objects.filter(pk=trunk.pk).update(
        health="ready" if reachable else "down", health_reason="" if reachable else str(result.get("detail", ""))[:200],
        health_changed_at=now,
    )
    return _check("options", OK if reachable else FAILED, **data, **({} if reachable else {"reason": "no_answer"}))


def run(trunk: PbxTrunk) -> dict:
    tenant = PbxTenant.objects.select_related("home_node").filter(company_id=trunk.company_id).first()
    live = tenant is not None and tenant.status in (PbxTenant.Status.ACTIVE, PbxTenant.Status.SUSPENDED)
    if not live:
        raise CallRefused("not_provisioned", "The phone system is not ready for your company yet.", 409)

    checks = []
    if trunk.status == PbxTrunk.Status.DISABLED:
        checks.append(_check("config", FAILED, reason="disabled"))
    elif tenant.applied_version != tenant.config_version:
        checks.append(_check("config", WARNING, reason="applying"))
    else:
        checks.append(_check("config", OK, version=tenant.applied_version))

    checks.append(_probe(tenant, trunk) if trunk.status != PbxTrunk.Status.DISABLED
                  else _check("options", SKIPPED, reason="disabled"))
    checks.append(_check("register", SKIPPED, reason="identified_by_address"))
    checks.append(_check("identify", OK, sources=len(trunk.allowed_sources))
                  if trunk.allowed_sources else _check("identify", FAILED, reason="no_sources"))

    numbers = list(trunk.numbers.order_by("e164"))
    verified = [n for n in numbers if n.verified_at is not None]
    unverified = [n.e164 for n in numbers if n.verified_at is None]
    if not numbers:
        checks.append(_check("numbers", WARNING, reason="none"))
    else:
        checks.append(_check("numbers", OK if not unverified else WARNING, verified=len(verified),
                             unverified=unverified))

    routed = set(PbxInboundRoute.objects.filter(number__in=verified).values_list("number__e164", flat=True))
    unrouted = [n.e164 for n in verified if n.e164 not in routed]
    checks.append(_check("inbound", SKIPPED, reason="no_verified_numbers") if not verified
                  else _check("inbound", OK if not unrouted else WARNING, unrouted=unrouted))

    routes = list(PbxOutboundRoute.objects.filter(trunk=trunk, enabled=True).select_related("caller_id"))
    usable = [r.name for r in routes if r.caller_id.verified_at is not None]
    blocked = [r.name for r in routes if r.caller_id.verified_at is None]
    if usable:
        checks.append(_check("outbound", OK if not blocked else WARNING, routes=usable, unverified_caller_id=blocked))
    else:
        checks.append(_check("outbound", WARNING, reason="unverified_caller_id" if blocked else "none",
                             unverified_caller_id=blocked))
    return {
        "trunk": str(trunk.pk),
        "tested_at": timezone.now(),
        "ok": not any(c["status"] == FAILED for c in checks),
        "checks": checks,
    }
