"""
Provisioning state machine (spec §5; CRM-pbx/docs/contract.md §2).

Requesting a change never touches a node. It records a ``PbxProvisioningJob`` and
queues it; the job compiles the tenant's rows into a new, immutable, versioned
``PbxArtifact``. The tenant's home node pulls it through the internal API, applies
it, and reports back; only that report moves the tenant to ACTIVE/SUSPENDED.

Idempotency and concurrency:

* ``request_job`` coalesces: while a config-changing job is still PENDING, new
  edits reuse it (it compiles whatever the rows say when it runs);
* ``run_job`` locks the job and the tenant row, and is a no-op for a job that is
  no longer PENDING/COMPILING, so a duplicate queue delivery cannot publish twice;
* ``record_apply_result`` is idempotent by (tenant, version) and never moves
  ``applied_version`` backwards.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from integrations.encryption import decrypt_token, encrypt_token

from .. import contract
from ..models import PbxArtifact, PbxNode, PbxProvisioningJob, PbxTenant
from .compiler import COMPILER_VERSION, CompileError, compile_tenant
from .fraud import effective_limits

logger = logging.getLogger(__name__)

TASK_PATH = "telephony.tasks.run_provisioning_job"
QUEUE_CLUSTER = "telephony"

Job = PbxProvisioningJob

# Kinds that only mean "compile the current rows again"; safe to coalesce.
_COALESCABLE = (Job.Kind.RECOMPILE, Job.Kind.PROVISION, Job.Kind.SUSPEND, Job.Kind.RESUME)


# ---------------------------------------------------------------------------
# Nodes and tenants
# ---------------------------------------------------------------------------


def pick_node() -> PbxNode | None:
    """Least-loaded ACTIVE node with spare tenant capacity."""
    return (
        PbxNode.objects.filter(status=PbxNode.Status.ACTIVE)
        .annotate(n=Count("tenants", filter=~Q(tenants__status=PbxTenant.Status.DEPROVISIONED)))
        .order_by("n", "name")
        .filter(n__lt=F("max_tenants"))
        .first()
    )


def ensure_tenant(company) -> PbxTenant:
    tenant, _ = PbxTenant.objects.get_or_create(company=company)
    return tenant


def tenant_for_company(company) -> PbxTenant | None:
    return PbxTenant.objects.filter(company=company).first()


# ---------------------------------------------------------------------------
# Requesting work
# ---------------------------------------------------------------------------


def request_job(tenant: PbxTenant, kind: str, *, user=None) -> PbxProvisioningJob:
    """Record (or reuse) a job and queue it after the surrounding transaction commits."""
    with transaction.atomic():
        tenant = PbxTenant.objects.select_for_update().get(pk=tenant.pk)
        if kind == Job.Kind.SUSPEND:
            tenant.outbound_suspended = True
        elif kind == Job.Kind.RESUME:
            tenant.outbound_suspended = False
            tenant.suspended_reason = ""
            tenant.suspended_for_billing = False
        elif kind == Job.Kind.PROVISION and tenant.status in (PbxTenant.Status.FAILED, PbxTenant.Status.DEPROVISIONED):
            tenant.status = PbxTenant.Status.PENDING
        tenant.save()

        job = None
        if kind in _COALESCABLE:
            job = tenant.jobs.filter(status=Job.Status.PENDING, kind__in=_COALESCABLE).order_by("created_at").first()
        if job is None:
            job = Job.objects.create(company_id=tenant.company_id, tenant=tenant, kind=kind, requested_by=user)
        job_id = job.id
        transaction.on_commit(lambda: enqueue(job_id))
    return job


def request_recompile_for_company(company, *, user=None) -> PbxProvisioningJob | None:
    """Call after any config row changes. No-op until the company is provisioned."""
    tenant = tenant_for_company(company)
    if tenant is None or tenant.status == PbxTenant.Status.DEPROVISIONED:
        return None
    return request_job(tenant, Job.Kind.RECOMPILE, user=user)


def enqueue(job_id) -> None:
    """
    Dedicated ``telephony`` django-q cluster with one worker (plan: compiles never
    compete with the CRM's own queue). Off by default, like PUSH_QUEUE_ENABLED:
    until that cluster runs, jobs execute inline after commit — compiling is a few
    milliseconds of string building and touches no node.
    """
    if getattr(settings, "TELEPHONY_QUEUE_ENABLED", False):
        try:
            from django_q.tasks import async_task

            async_task(TASK_PATH, str(job_id), cluster=QUEUE_CLUSTER, task_name=f"pbx_job:{job_id}"[:100])
            return
        except Exception as exc:  # broker down: do the work now rather than lose it
            logger.warning("telephony: could not enqueue job %s (%s); running inline", job_id, exc)
    run_job(job_id)


# ---------------------------------------------------------------------------
# Running a job
# ---------------------------------------------------------------------------


def run_job(job_id) -> PbxProvisioningJob | None:
    with transaction.atomic():
        job = Job.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.status not in (Job.Status.PENDING, Job.Status.COMPILING):
            return job
        tenant = PbxTenant.objects.select_for_update().get(pk=job.tenant_id)
        job.status = Job.Status.COMPILING
        job.attempts += 1

        if tenant.home_node_id is None:
            tenant.home_node = pick_node()
            if tenant.home_node is None:
                return _fail(job, tenant, "no_node_available", "No active PBX node has capacity.")

        removing = job.kind == Job.Kind.DEPROVISION
        version = tenant.config_version + 1
        try:
            files = {} if removing else compile_tenant(tenant, version=version).files
        except (CompileError, contract.ArtifactError, contract.NamingError) as exc:
            return _fail(job, tenant, "compile_failed", str(exc))

        manifest_fields = {
            "tenant": tenant.tag,
            "company_id": tenant.company_id,
            "version": version,
            "action": "remove" if removing else "apply",
            "outbound": "suspended" if tenant.outbound_suspended else "enabled",
            "generated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            # The node's fraud detector enforces these (contract section 6, fraud).
            **({} if removing else {"fraud": effective_limits(tenant)}),
            "compiler_version": COMPILER_VERSION,
        }
        try:
            blob, manifest = contract.build_artifact(manifest_fields, files)
        except contract.ArtifactError as exc:
            return _fail(job, tenant, "compile_failed", str(exc))

        PbxArtifact.objects.create(
            tenant=tenant,
            version=version,
            action=manifest["action"],
            sha256=contract.sha256_hex(blob),
            size_bytes=len(blob),
            blob_encrypted=encrypt_token(blob.hex()),
            manifest=manifest,
        )
        tenant.config_version = version
        tenant.save(update_fields=["config_version", "home_node", "updated_at"])
        prune_artifacts(tenant)

        # Older jobs still waiting on the node are folded into this artifact.
        tenant.jobs.filter(status=Job.Status.AWAITING_APPLY).exclude(pk=job.pk).update(
            status=Job.Status.SUPERSEDED, finished_at=timezone.now()
        )
        job.status = Job.Status.AWAITING_APPLY
        job.target_version = version
        job.last_error = ""
        job.save()
        logger.info("telephony: %s compiled v%s (job %s, %s)", tenant.tag, version, job.id, job.kind)
        node_id = tenant.home_node_id
        transaction.on_commit(lambda: nudge_node(node_id))
    return job


KEEP_ARTIFACTS = 5


def prune_artifacts(tenant: PbxTenant) -> int:
    """
    Every compile stores a full encrypted artifact (with greetings, up to a few
    MB). Keep the newest few, plus the one the node runs: a node only ever asks
    for the current version, and a node rolls back from its own releases.
    """
    keep = set(
        PbxArtifact.objects.filter(tenant=tenant).order_by("-version").values_list("version", flat=True)[:KEEP_ARTIFACTS]
    )
    for version in (tenant.applied_version, tenant.config_version, tenant.draining_version):
        if version:
            keep.add(version)
    deleted, _ = PbxArtifact.objects.filter(tenant=tenant).exclude(version__in=keep).delete()
    return deleted


def nudge_node(node_id) -> bool:
    """
    Ask the node's worker to make its applier pull now (contract section 6,
    ``POST /v1/apply-now``), so a change lands in about a second rather than on the
    next 15 s poll. Behind ``TELEPHONY_APPLY_NUDGE`` and best effort: a short
    timeout, and a node that cannot be reached still picks the change up by polling.
    """
    if not getattr(settings, "TELEPHONY_APPLY_NUDGE", False):
        return False
    from . import pbx_worker_client

    node = PbxNode.objects.filter(pk=node_id).first()
    if node is None or not node.control_url:
        return False
    ok = pbx_worker_client.apply_now(node, timeout=getattr(settings, "TELEPHONY_APPLY_NUDGE_TIMEOUT", 1.5))
    if not ok:
        logger.info("telephony: apply-now nudge to %s not delivered; the applier will poll", node.name)
    return ok


def _fail(job, tenant, code: str, detail: str):
    job.status = Job.Status.FAILED
    job.last_error = f"{code}: {detail}"[:2000]
    job.finished_at = timezone.now()
    job.save()
    tenant.last_apply_error = job.last_error
    if tenant.applied_version is None:
        tenant.status = PbxTenant.Status.FAILED
    tenant.save()
    logger.warning("telephony: job %s for %s failed: %s", job.id, tenant.tag, job.last_error)
    return job


# ---------------------------------------------------------------------------
# Node side (internal API)
# ---------------------------------------------------------------------------


def artifact_blob(artifact: PbxArtifact) -> bytes:
    return bytes.fromhex(decrypt_token(artifact.blob_encrypted))


def desired_state(node: PbxNode) -> list[dict]:
    """
    Latest artifact of every tenant homed on ``node`` that still needs serving, plus
    the ``remove`` artifact of tenants that moved away and are still draining here.
    """
    out = []
    for t in PbxTenant.objects.filter(draining_node=node, draining_version__isnull=False).order_by("realm_key"):
        a = PbxArtifact.objects.filter(tenant=t, version=t.draining_version).only("version", "sha256").first()
        if a is not None:
            out.append({
                "tenant": t.tag,
                "version": a.version,
                "artifact_path": f"/api/v1/internal/telephony/nodes/{node.id}/artifacts/{t.tag}/{a.version}/",
                "artifact_sha256": a.sha256,
            })
    tenants = (
        PbxTenant.objects.filter(home_node=node, config_version__gt=0)
        .exclude(status=PbxTenant.Status.DEPROVISIONED, applied_version=F("config_version"))
        .order_by("realm_key")
    )
    artifacts = {
        (a.tenant_id, a.version): a
        for a in PbxArtifact.objects.filter(tenant__in=tenants).only("tenant_id", "version", "sha256")
    }
    for t in tenants:
        a = artifacts.get((t.id, t.config_version))
        if a is None:
            continue
        out.append({
            "tenant": t.tag,
            "version": a.version,
            "artifact_path": f"/api/v1/internal/telephony/nodes/{node.id}/artifacts/{t.tag}/{a.version}/",
            "artifact_sha256": a.sha256,
        })
    return out


class NotOnNode(Exception):
    pass


def record_apply_result(node: PbxNode, payload: dict) -> str:
    """Apply a node's apply-result report. Returns "recorded" or "duplicate"."""
    try:
        rk = contract.realm_key_from_tag(payload["tenant"])
    except contract.NamingError as exc:
        raise NotOnNode(str(exc)) from exc
    version = int(payload["version"])
    status = payload["status"]

    with transaction.atomic():
        tenant = PbxTenant.objects.select_for_update().filter(realm_key=rk, home_node=node).first()
        if tenant is None:
            draining = PbxTenant.objects.select_for_update().filter(
                realm_key=rk, draining_node=node, draining_version=version
            ).first()
            if draining is None:
                raise NotOnNode(payload["tenant"])
            return _record_drain_result(draining, node, status, payload)
        artifact = tenant.artifacts.filter(version=version).first()
        if artifact is None:
            raise NotOnNode(f"{payload['tenant']} v{version}")

        PbxNode.objects.filter(pk=node.pk).update(
            last_seen_at=timezone.now(),
            **({"asterisk_version": payload["asterisk_version"][:40]} if payload.get("asterisk_version") else {}),
        )
        job = tenant.jobs.filter(target_version=version).order_by("-created_at").first()
        if job is not None and job.status in (Job.Status.SUCCEEDED, Job.Status.FAILED):
            return "duplicate"

        now = timezone.now()
        if status == "applied":
            if tenant.applied_version is None or version > tenant.applied_version:
                tenant.applied_version = version
            tenant.last_apply_error = ""
            if version == tenant.config_version:
                if artifact.action == PbxArtifact.Action.REMOVE:
                    tenant.status = PbxTenant.Status.DEPROVISIONED
                elif tenant.outbound_suspended:
                    tenant.status = PbxTenant.Status.SUSPENDED
                else:
                    tenant.status = PbxTenant.Status.ACTIVE
            if job is not None:
                job.status, job.finished_at, job.last_error = Job.Status.SUCCEEDED, now, ""
                if job.kind == Job.Kind.MIGRATE:
                    # The new node's worker only accepts state for tenants it has
                    # applied: re-push paused agents and the outbound block now.
                    from .migration import repush_node_state

                    tenant_id = tenant.pk
                    transaction.on_commit(lambda: repush_node_state(PbxTenant.objects.get(pk=tenant_id)))
        else:
            err = payload.get("error") or {}
            message = f"{status}: {err.get('code', 'unknown')}: {err.get('detail', '')}"[:2000]
            tenant.last_apply_error = message
            if tenant.applied_version is None:
                tenant.status = PbxTenant.Status.FAILED
            if job is not None:
                job.status, job.finished_at, job.last_error = Job.Status.FAILED, now, message
        tenant.save()
        if job is not None:
            job.save()
    return "recorded"


def _record_drain_result(tenant: PbxTenant, node: PbxNode, status: str, payload: dict) -> str:
    """The node a tenant moved away from reports its ``remove`` artifact (node migration)."""
    PbxNode.objects.filter(pk=node.pk).update(last_seen_at=timezone.now())
    if status == "applied":
        tenant.draining_node, tenant.draining_version = None, None
        tenant.save(update_fields=["draining_node", "draining_version", "updated_at"])
        logger.info("telephony: %s drained from %s", tenant.tag, node.name)
    else:
        err = payload.get("error") or {}
        tenant.last_apply_error = f"previous node {node.name}: {status}: {err.get('code', 'unknown')}: {err.get('detail', '')}"[:2000]
        tenant.save(update_fields=["last_apply_error", "updated_at"])
    return "recorded"


# ---------------------------------------------------------------------------
# Node registration
# ---------------------------------------------------------------------------


NODE_SETTABLE = ("status", "gateway_wg_public_key", "gateway_wg_endpoint", "gateway_wg_address")


def register_node(*, name: str, region: str = "", control_url: str = "", max_tenants: int = 100,
                  **settings_: object) -> tuple[PbxNode, str, str]:
    """
    Create a node; returns (node, node_token, crm_token). Tokens are shown once.
    The admin console may also set the status and the GSM gateway link up front.
    """
    unknown = set(settings_) - set(NODE_SETTABLE)
    if unknown:
        raise TypeError(f"unknown node field(s): {', '.join(sorted(unknown))}")
    node_token, crm_token = contract.generate_node_token(), contract.generate_node_token()
    node = PbxNode.objects.create(
        name=name,
        region=region,
        control_url=control_url,
        max_tenants=max_tenants,
        node_token_hash=contract.hash_node_token(node_token),
        crm_token_encrypted=encrypt_token(crm_token),
        **settings_,
    )
    return node, node_token, crm_token


def rotate_node_tokens(node: PbxNode) -> tuple[str, str]:
    node_token, crm_token = contract.generate_node_token(), contract.generate_node_token()
    node.node_token_hash = contract.hash_node_token(node_token)
    node.crm_token_encrypted = encrypt_token(crm_token)
    node.save(update_fields=["node_token_hash", "crm_token_encrypted", "updated_at"])
    return node_token, crm_token
