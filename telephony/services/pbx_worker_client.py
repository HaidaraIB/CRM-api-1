"""
Thin client for a node's telephony worker (CRM-pbx contract section 6).

The CRM never opens ARI; it asks the tenant's home node's worker over the private
link, authenticated with the node's ``crm_token`` (stored encrypted on PbxNode).
Requests and responses are the contract's JSON shapes; errors come back as
``WorkerError(status, code)`` so callers can map them to API responses.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid

from django.conf import settings

from integrations.encryption import decrypt_token

from ..models import PbxNode

logger = logging.getLogger(__name__)


class WorkerError(Exception):
    def __init__(self, status: int | None, code: str, detail: str = ""):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


def _post(node: PbxNode, path: str, body: dict, *, timeout: float | None = None) -> dict:
    if not node.control_url or not node.crm_token_encrypted:
        raise WorkerError(None, "node_unavailable", "node has no control URL")
    req = urllib.request.Request(
        node.control_url.rstrip("/") + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {decrypt_token(node.crm_token_encrypted)}",
            "Content-Type": "application/json",
        },
    )
    if timeout is None:
        timeout = float(getattr(settings, "TELEPHONY_WORKER_TIMEOUT", 5))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        raise WorkerError(exc.code, str(payload.get("code") or "node_unavailable"), str(payload.get("detail") or ""))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("telephony: worker %s unreachable: %s", node.name, exc)
        raise WorkerError(None, "node_unavailable", "node unreachable")


def originate(node: PbxNode, payload: dict) -> dict:
    return _post(node, "/v1/calls/originate", {"request_id": str(uuid.uuid4()), **payload})


def hangup(node: PbxNode, *, call_id, tenant_tag: str) -> dict:
    return _post(node, f"/v1/calls/{call_id}/hangup", {"request_id": str(uuid.uuid4()), "tenant": tenant_tag})


def set_agent_status(node: PbxNode, *, tenant_tag: str, extension: str, status: str) -> dict:
    return _post(node, "/v1/agents/status", {
        "request_id": str(uuid.uuid4()), "tenant": tenant_tag, "extension": extension, "status": status,
    })


def apply_now(node: PbxNode, *, timeout: float | None = None) -> bool:
    """Best effort: the applier polls anyway. True when the worker took the nudge."""
    try:
        _post(node, "/v1/apply-now", {}, timeout=timeout)
        return True
    except WorkerError:
        return False


def probe_trunk(node: PbxNode, *, tenant_tag: str, trunk_endpoint: str) -> dict:
    """One SIP OPTIONS from the node to the trunk's configured contact (contract section 6)."""
    # The worker waits up to ~4 s for the far end; leave it room to answer.
    return _post(node, "/v1/trunks/probe", {"request_id": str(uuid.uuid4()), "tenant": tenant_tag,
                                            "trunk": trunk_endpoint}, timeout=8)


def set_outbound(node: PbxNode, *, tenant_tag: str, status: str) -> dict:
    """``blocked`` / ``enabled``: the node's immediate outbound switch (Stasis:t<key>-outbound)."""
    return _post(node, "/v1/tenants/outbound", {"request_id": str(uuid.uuid4()), "tenant": tenant_tag,
                                                "status": status})


def health(node: PbxNode, *, timeout: float = 2.0) -> dict | None:
    """``GET /v1/health``, or None when the worker cannot be reached."""
    if not node.control_url or not node.crm_token_encrypted:
        return None
    req = urllib.request.Request(
        node.control_url.rstrip("/") + "/v1/health",
        headers={"Authorization": f"Bearer {decrypt_token(node.crm_token_encrypted)}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
