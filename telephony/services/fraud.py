"""
Outbound fraud (CRM-pbx contract section 6, fraud; spec section 9).

Detection runs on the PBX node, which sees every call as it starts, even while
the CRM is unreachable. The CRM's part:

* the limits: ``settings.TELEPHONY_FRAUD_DEFAULTS`` overridden per tenant by the
  platform operator (``PbxTenant.fraud_limits``), shipped in every artifact
  manifest (``fraud``);
* ``fraud.alert`` events: stored as ``PbxFraudAlert``; when the node already
  blocked the tenant (``action_taken: outbound_suspended``) the CRM suspends it
  too, so the recompiled config holds the block durably;
* the node's immediate outbound switch (``POST /v1/tenants/outbound``): a platform
  suspend sets it, and a resume must clear it on the node before anything else.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import PbxFraudAlert, PbxTenant
from . import pbx_worker_client

logger = logging.getLogger(__name__)

RULES = {choice for choice, _ in PbxFraudAlert.Rule.choices}
_PREFIX_RE = re.compile(r"^\+[1-9][0-9]{0,14}$")
_HOME_RE = re.compile(r"^\+[1-9][0-9]{0,6}$")


class InvalidLimits(ValueError):
    pass


def defaults() -> dict:
    return dict(getattr(settings, "TELEPHONY_FRAUD_DEFAULTS", {}) or {})


def validate_limits(raw) -> dict:
    """Platform overrides: only known keys, each in range. Raises InvalidLimits."""
    if not isinstance(raw, dict):
        raise InvalidLimits("fraud_limits must be an object")
    known = set(defaults())
    unknown = set(raw) - known
    if unknown:
        raise InvalidLimits(f"unknown fraud limit(s): {', '.join(sorted(unknown))}")
    out = {}
    for key, value in raw.items():
        if key in ("max_calls_per_hour", "max_new_destinations_per_hour", "min_calls_for_share"):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100000:
                raise InvalidLimits(f"{key} must be an integer from 1 to 100000")
        elif key == "max_international_share":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                raise InvalidLimits("max_international_share must be between 0 and 1")
            value = float(value)
        elif key in ("home_prefixes", "premium_prefixes"):
            pattern = _HOME_RE if key == "home_prefixes" else _PREFIX_RE
            limit = 10 if key == "home_prefixes" else 100
            if not isinstance(value, list) or len(value) > limit or not all(
                    isinstance(p, str) and pattern.match(p) for p in value):
                raise InvalidLimits(f"{key} must be up to {limit} E.164 prefixes like +964")
        elif key == "action":
            if value not in ("block", "alert"):
                raise InvalidLimits("action must be block or alert")
        out[key] = value
    return out


def effective_limits(tenant: PbxTenant) -> dict:
    """What the node enforces for this tenant (the manifest's ``fraud``)."""
    merged = {**defaults(), **(tenant.fraud_limits or {})}
    try:
        return {**defaults(), **validate_limits({k: v for k, v in merged.items() if k in defaults()})}
    except InvalidLimits:
        logger.error("telephony: invalid fraud limits for %s; platform defaults used", tenant.tag)
        return defaults()


# ---------------------------------------------------------------------------
# Alerts from nodes
# ---------------------------------------------------------------------------


def record_alert(tenant: PbxTenant, node, ev: dict) -> PbxFraudAlert | None:
    """Store one ``fraud.alert`` (idempotent by event id) and suspend the tenant if the node blocked it."""
    from .events import InvalidEvent

    data = ev["data"]
    rule = data.get("rule")
    if rule not in RULES:
        raise InvalidEvent("invalid fraud rule")
    if data.get("severity") not in ("warning", "critical") or data.get("action_taken") not in ("none", "outbound_suspended"):
        raise InvalidEvent("invalid fraud severity or action")
    number = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    destination = data.get("destination") or ""
    if destination and not re.fullmatch(r"\+[1-9][0-9]{6,14}", str(destination)):
        raise InvalidEvent("invalid destination")
    try:
        with transaction.atomic():
            alert = PbxFraudAlert.objects.create(
                company_id=tenant.company_id, tenant=tenant, node=node, event_id=ev["event_id"], rule=rule,
                severity=data["severity"], action_taken=data["action_taken"], observed=number(data.get("observed")),
                threshold=number(data.get("threshold")), destination=destination, occurred_at=ev["occurred_at"],
            )
    except IntegrityError:
        return None
    if alert.action_taken == "outbound_suspended":
        transaction.on_commit(lambda: _suspend_for_fraud(alert.pk))
    logger.warning("telephony: fraud alert %s for %s (%s)", rule, tenant.tag, alert.action_taken)
    return alert


def _suspend_for_fraud(alert_id) -> None:
    from settings.services import log_system_action

    from . import provisioning

    alert = PbxFraudAlert.objects.select_related("tenant").get(pk=alert_id)
    tenant = alert.tenant
    PbxTenant.objects.filter(pk=tenant.pk).update(outbound_blocked_on_node=True)
    if not tenant.outbound_suspended:
        PbxTenant.objects.filter(pk=tenant.pk).update(suspended_reason=f"Fraud alert: {alert.get_rule_display()}"[:200])
        provisioning.request_job(tenant, provisioning.Job.Kind.SUSPEND)
    log_system_action(
        "telephony.fraud_suspended",
        message=f"Outbound calls suspended by the fraud detector ({alert.rule})",
        metadata={"company_id": tenant.company_id, "alert_id": str(alert.pk), "rule": alert.rule,
                  "observed": alert.observed, "threshold": alert.threshold},
    )


def resolve_alert(alert: PbxFraudAlert, *, user, note: str = "") -> PbxFraudAlert:
    alert.resolved_at = timezone.now()
    alert.resolved_by = user
    alert.resolution_note = (note or "")[:500]
    alert.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])
    return alert


# ---------------------------------------------------------------------------
# The node's immediate outbound switch
# ---------------------------------------------------------------------------


def block_on_node(tenant: PbxTenant) -> bool:
    """Best effort (the recompiled config blocks anyway). True when the node took it."""
    if tenant.home_node is None:
        return False
    try:
        pbx_worker_client.set_outbound(tenant.home_node, tenant_tag=tenant.tag, status="blocked")
    except pbx_worker_client.WorkerError as exc:
        logger.info("telephony: outbound block for %s not delivered (%s); the recompile still blocks", tenant.tag, exc.code)
        return False
    PbxTenant.objects.filter(pk=tenant.pk).update(outbound_blocked_on_node=True)
    return True


def unblock_on_node(tenant: PbxTenant) -> None:
    """Required before a resume when the node holds a block. Raises WorkerError when it cannot."""
    if not tenant.outbound_blocked_on_node:
        return
    if tenant.home_node is None:
        raise pbx_worker_client.WorkerError(None, "node_unavailable", "tenant has no home node")
    pbx_worker_client.set_outbound(tenant.home_node, tenant_tag=tenant.tag, status="enabled")
    PbxTenant.objects.filter(pk=tenant.pk).update(outbound_blocked_on_node=False)
    tenant.outbound_blocked_on_node = False
