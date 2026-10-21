"""
Moving a tenant to another PBX node (spec section 6 ``migrate-node``; CRM-pbx
contract section 2, node migration).

One request, three things the nodes pick up on their own:

1. the old node gets a ``remove`` artifact (``draining_version``): it keeps listing
   the tenant in its desired state until it reports that version applied, then the
   drain is over. Its events and recordings are still accepted meanwhile;
2. the tenant is re-homed and recompiled: the new node pulls a fresh ``apply``;
3. gateway peers follow the tenant to the new node's wg-gw (the tunnel address is
   kept when it is free there). The new node has a different WireGuard key and
   endpoint, so each gateway must be reconfigured on site and verified again.

A new node's astdb is empty, so the agent pauses and the outbound block live only
in the CRM until re-pushed (``repush_node_state``): right after the move, and on
demand (``POST /admin/telephony/tenants/<company>/repush/``).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .. import contract
from ..models import PbxArtifact, PbxExtension, PbxGatewayPeer, PbxNode, PbxTenant, PbxTrunk
from . import pbx_worker_client as worker
from .calls import CallRefused

logger = logging.getLogger(__name__)


def repush_node_state(tenant: PbxTenant) -> dict:
    """Re-send what the node keeps in astdb: paused agents and the outbound block. Best effort."""
    tenant = PbxTenant.objects.select_related("home_node").get(pk=tenant.pk)
    report = {"paused_agents": 0, "failed_agents": [], "outbound": None}
    node = tenant.home_node
    if node is None:
        return report
    paused = PbxExtension.objects.filter(
        company_id=tenant.company_id, status=PbxExtension.Status.ACTIVE, agent_status=PbxExtension.AgentStatus.PAUSED,
    ).order_by("number")
    for ext in paused:
        try:
            worker.set_agent_status(node, tenant_tag=tenant.tag, extension=ext.number, status="paused")
            report["paused_agents"] += 1
        except worker.WorkerError:
            report["failed_agents"].append(ext.number)
    if tenant.outbound_suspended or tenant.outbound_blocked_on_node:
        try:
            worker.set_outbound(node, tenant_tag=tenant.tag, status="blocked")
            PbxTenant.objects.filter(pk=tenant.pk).update(outbound_blocked_on_node=True)
            report["outbound"] = "blocked"
        except worker.WorkerError:
            report["outbound"] = "failed"
    return report


def _move_gateways(tenant: PbxTenant, node: PbxNode) -> int:
    from .gateways import _allocate_ip

    moved = 0
    for peer in PbxGatewayPeer.objects.select_for_update().filter(company_id=tenant.company_id).exclude(node=node):
        taken = PbxGatewayPeer.objects.filter(node=node, tunnel_ip=peer.tunnel_ip).exists()
        ip = peer.tunnel_ip if not taken and peer.tunnel_ip != str(node.gateway_wg_address) else _allocate_ip(node)
        peer.node = node
        peer.tunnel_ip = ip
        if peer.status == PbxGatewayPeer.Status.VERIFIED:
            peer.status, peer.verified_at = PbxGatewayPeer.Status.ENROLLED, None
        peer.save()
        PbxTrunk.objects.filter(pk=peer.trunk_id).update(
            host=ip, allowed_sources=[f"{ip}/32"], health="", health_reason="", health_changed_at=None,
            status=PbxTrunk.Status.PENDING, updated_at=timezone.now(),
        )
        moved += 1
    return moved


def migrate_tenant(tenant: PbxTenant, node: PbxNode, *, user=None):
    """Returns (job compiling the new home's config, {"gateways_moved": n})."""
    from settings.services import log_system_action

    from . import provisioning
    from .compiler import COMPILER_VERSION

    with transaction.atomic():
        tenant = PbxTenant.objects.select_for_update().get(pk=tenant.pk)
        node = PbxNode.objects.select_for_update().get(pk=node.pk)
        if tenant.status == PbxTenant.Status.DEPROVISIONED or tenant.home_node_id is None:
            raise CallRefused("not_provisioned", "This company's phone system is not provisioned.")
        if node.pk == tenant.home_node_id:
            raise CallRefused("same_node", "The company is already on this node.")
        if tenant.draining_node_id is not None:
            raise CallRefused("migration_in_progress", "The previous node has not finished removing this company yet.")
        if node.status != PbxNode.Status.ACTIVE:
            raise CallRefused("node_not_active", "Choose an active node.")
        load = PbxNode.objects.filter(pk=node.pk).annotate(
            n=Count("tenants", filter=~Q(tenants__status=PbxTenant.Status.DEPROVISIONED))
        ).values_list("n", flat=True).first()
        if load >= node.max_tenants:
            raise CallRefused("node_full", "This node has no room for another company.")

        old = tenant.home_node
        version = tenant.config_version + 1
        blob, manifest = contract.build_artifact({
            "tenant": tenant.tag, "company_id": tenant.company_id, "version": version, "action": "remove",
            "outbound": "suspended" if tenant.outbound_suspended else "enabled",
            "generated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"), "compiler_version": COMPILER_VERSION,
        }, {})
        from integrations.encryption import encrypt_token

        PbxArtifact.objects.create(
            tenant=tenant, version=version, action=PbxArtifact.Action.REMOVE, sha256=contract.sha256_hex(blob),
            size_bytes=len(blob), blob_encrypted=encrypt_token(blob.hex()), manifest=manifest,
        )
        tenant.config_version = version
        tenant.draining_node, tenant.draining_version = old, version
        tenant.home_node = node
        tenant.applied_version = None  # nothing is live on the new node yet
        tenant.outbound_blocked_on_node = False  # the new node's astdb is empty
        tenant.save()
        moved = _move_gateways(tenant, node)
        job = provisioning.request_job(tenant, provisioning.Job.Kind.MIGRATE, user=user)
        log_system_action(
            "telephony.tenant_migrated", user=user,
            message=f"Phone system moved from {old.name} to {node.name}",
            metadata={"company_id": tenant.company_id, "from_node": str(old.pk), "to_node": str(node.pk),
                      "drain_version": version, "gateways_moved": moved},
        )
        tenant_id, old_id = tenant.pk, old.pk
        transaction.on_commit(lambda: provisioning.nudge_node(old_id))
        transaction.on_commit(lambda: _after_move(tenant_id))
    return job, {"gateways_moved": moved}


def _after_move(tenant_id) -> None:
    tenant = PbxTenant.objects.get(pk=tenant_id)
    report = repush_node_state(tenant)
    if report["failed_agents"] or report["outbound"] == "failed":
        logger.warning("telephony: %s moved, but node state was not fully re-pushed: %s", tenant.tag, report)
