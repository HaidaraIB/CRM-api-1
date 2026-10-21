"""
Subscription/pilot policy → phone system ("end_expired_subscriptions" queues
a SUSPEND job).

Desk phones dial out through the PBX without ever asking the CRM, so an expired
subscription, or loss of both plan entitlement and explicit pilot approval, must
reach the node: outbound calls are suspended (blocked on the node at once, then
recompiled). Incoming and internal calls keep working. When the company is
entitled again, the suspension is lifted.

Run as a reconciliation from ``end_expired_subscriptions`` (every 15 minutes), so
the payment path is untouched: a renewal resumes calling within one run. Only
tenants this module suspended (``suspended_for_billing``) are resumed; a fraud or
platform suspension stays until an operator resumes it.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ..models import PbxTenant
from . import pbx_worker_client
from .fraud import block_on_node, unblock_on_node

logger = logging.getLogger(__name__)

REASON = "Subscription ended or the plan has no phone system"


def entitled(company, *, now=None) -> bool:
    from integrations.policy import is_integration_allowed
    from subscriptions.models import Subscription

    now = now or timezone.now()
    sub = Subscription.objects.filter(company=company, is_active=True, end_date__gt=now).select_related("plan").first()
    if sub is None:
        return False
    return is_integration_allowed(company, "telephony")


def reconcile(*, now=None) -> dict:
    """Suspend tenants that lost telephony, resume the ones this module suspended. Returns counts."""
    from settings.services import log_system_action

    from . import provisioning

    report = {"suspended": 0, "resumed": 0, "resume_failed": 0}
    tenants = PbxTenant.objects.exclude(status=PbxTenant.Status.DEPROVISIONED).select_related("company", "home_node")
    for tenant in tenants:
        ok = entitled(tenant.company, now=now)
        if not ok and not tenant.outbound_suspended:
            PbxTenant.objects.filter(pk=tenant.pk).update(suspended_reason=REASON, suspended_for_billing=True)
            block_on_node(tenant)
            provisioning.request_job(tenant, provisioning.Job.Kind.SUSPEND)
            log_system_action("telephony.billing_suspended", message="Outbound calls suspended: no phone system entitlement",
                              metadata={"company_id": tenant.company_id})
            report["suspended"] += 1
        elif ok and tenant.outbound_suspended and tenant.suspended_for_billing:
            try:
                unblock_on_node(tenant)
            except pbx_worker_client.WorkerError as exc:
                # The node still blocks; resuming now would compile a config the node
                # keeps refusing. Retried on the next run.
                logger.warning("telephony: %s entitled again but the node block stays (%s)", tenant.tag, exc.code)
                report["resume_failed"] += 1
                continue
            PbxTenant.objects.filter(pk=tenant.pk).update(suspended_for_billing=False)
            provisioning.request_job(tenant, provisioning.Job.Kind.RESUME)
            log_system_action("telephony.billing_resumed", message="Outbound calls resumed: subscription active again",
                              metadata={"company_id": tenant.company_id})
            report["resumed"] += 1
    return report
