"""
Agent status and presence (spec section 6: ``GET /agents/status``,
``PUT /agents/me/status``; CRM-pbx contract section 6).

The CRM owns an agent's status. Changing it is pushed to the tenant's home node
first, and saved only once the node took it, so what the CRM shows is what the
PBX does: the worker sets the agent's custom device state, and the compiled
dialplan answers busy (486) instead of ringing a paused agent. Pausing never
needs a recompile or a reload. Setting the same status again re-sends it, which
also repairs a node that lost it.

Presence combines that status with what the node reports: registration
(``agent.status_changed``) and live calls.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from ..models import PbxCall, PbxExtension, PbxTenant
from . import pbx_worker_client as worker
from .calls import CallRefused, user_extension

# A call whose call.ended was lost stops counting as "on a call" after this long
# (same window as the worker's untracked-call TTL).
LIVE_CALL_WINDOW = timedelta(hours=4)

STATUSES = {s.value for s in PbxExtension.AgentStatus}


def set_my_status(user, status) -> PbxExtension:
    if status not in STATUSES:
        raise CallRefused("invalid_status", "Status must be available or paused.", 400)
    tenant = PbxTenant.objects.select_related("home_node").filter(company_id=user.company_id).first()
    if tenant is None or tenant.home_node is None or tenant.status not in (
        PbxTenant.Status.ACTIVE, PbxTenant.Status.SUSPENDED,
    ):
        raise CallRefused("not_provisioned", "The phone system is not ready for your company yet.")
    ext = user_extension(user)
    if ext is None:
        raise CallRefused("no_extension", "You do not have a phone extension.", 403)
    try:
        worker.set_agent_status(tenant.home_node, tenant_tag=tenant.tag, extension=ext.number, status=status)
    except worker.WorkerError:
        raise CallRefused("pbx_unavailable", "The phone system is unavailable. Try again shortly.", 503)
    changed = ext.agent_status != status
    if changed:
        ext.agent_status = status
        ext.agent_status_changed_at = timezone.now()
        ext.save(update_fields=["agent_status", "agent_status_changed_at", "updated_at"])
        from sync.version import bump_company_slice

        bump_company_slice("telephony", user.company_id)
    return ext


def busy_extension_ids(company_id) -> set:
    """Extensions on a live call (any leg, or originating/answering it)."""
    live = PbxCall.objects.filter(
        company_id=company_id,
        status__in=PbxCall.LIVE_STATUSES,
        started_at__gte=timezone.now() - LIVE_CALL_WINDOW,
    )
    ids = set()
    for row in live.values_list("extension_id", "answered_by_id"):
        ids.update(i for i in row if i)
    ids.update(
        i for i in live.filter(legs__extension__isnull=False, legs__ended_at__isnull=True)
        .values_list("legs__extension_id", flat=True) if i
    )
    return ids


def presence(company_id) -> list[dict]:
    exts = (
        PbxExtension.objects.filter(company_id=company_id, status=PbxExtension.Status.ACTIVE)
        .select_related("user")
        .order_by("number")
    )
    busy = busy_extension_ids(company_id)
    out = []
    for ext in exts:
        user = ext.user
        name = ""
        if user is not None:
            name = (user.get_full_name() or user.username or "").strip()
        out.append({
            "extension_id": str(ext.pk),
            "number": ext.number,
            "display_name": ext.display_name,
            "user": ext.user_id,
            "user_name": name,
            "registered": ext.registered,
            "agent_status": ext.agent_status,
            "on_call": ext.pk in busy,
        })
    return out


__all__ = ["set_my_status", "presence", "busy_extension_ids", "STATUSES"]
