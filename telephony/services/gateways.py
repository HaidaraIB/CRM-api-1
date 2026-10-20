"""
GSM gateway onboarding (spec section 5; CRM-pbx contract section 5, gateway peers).

1. A tenant admin adds a gateway. The CRM pins a tunnel address on the tenant's
   home node (from the node's wg-gw network), creates the gateway's trunk
   (kind gsm_gateway, identified by that address only, status PENDING) and a
   one-time enrollment token. Only the token's sha256 is stored.
2. The installer, on site, enrolls the gateway's WireGuard public key with the
   token (public endpoint, throttled). The token is spent; the reply is the
   tunnel and SIP settings to enter on the gateway. The node adds the peer on its
   next desired-state pull (nudged).
3. The node qualifies gateway trunks (SIP OPTIONS over the tunnel) and reports
   reachability as gateway.status_changed. Verify marks the trunk READY only
   when the gateway is enrolled and the node has reported it reachable.

A gateway that is replaced is reset: its key is dropped (the node removes the
peer) and a new token is issued.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import PbxGatewayPeer, PbxNode, PbxTenant, PbxTrunk
from .calls import CallRefused

GATEWAY_NETWORK_PREFIX = 16  # each node's wg-gw network: <gateway_wg_address>/16
TOKEN_PREFIX = "lpg_"


def _token_ttl() -> timedelta:
    return timedelta(hours=int(getattr(settings, "TELEPHONY_GATEWAY_TOKEN_TTL_HOURS", 72)))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token(peer: PbxGatewayPeer) -> str:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    peer.enrollment_token_hash = hash_token(token)
    peer.token_expires_at = timezone.now() + _token_ttl()
    return token


def _allocate_ip(node: PbxNode) -> str:
    """Next free address of the node's gateway network (never the node's own)."""
    network = ipaddress.ip_network(f"{node.gateway_wg_address}/{GATEWAY_NETWORK_PREFIX}", strict=False)
    used = set(PbxGatewayPeer.objects.filter(node=node).values_list("tunnel_ip", flat=True))
    used.add(str(node.gateway_wg_address))
    for host in network.hosts():
        if str(host) not in used:
            return str(host)
    raise CallRefused("gateway_pool_exhausted", "No gateway address is free on this phone system node.", 503)


def create_gateway(company, *, name: str) -> tuple[PbxGatewayPeer, str]:
    from subscriptions.entitlements import require_quota

    tenant = PbxTenant.objects.filter(company=company).select_related("home_node").first()
    if tenant is None or tenant.home_node is None or tenant.status == PbxTenant.Status.DEPROVISIONED:
        raise CallRefused("not_provisioned", "The phone system is not ready for your company yet.")
    require_quota(
        company, "max_pbx_trunks", PbxTrunk.objects.filter(company=company).count(),
        message="Your plan's trunk limit has been reached.", error_key="max_pbx_trunks",
    )
    with transaction.atomic():
        node = PbxNode.objects.select_for_update().get(pk=tenant.home_node_id)  # serialises address allocation
        ip = _allocate_ip(node)
        trunk = PbxTrunk.objects.create(
            company=company, name=name, kind=PbxTrunk.Kind.GSM_GATEWAY, host=ip, port=5060,
            transport=PbxTrunk.Transport.UDP, allowed_sources=[f"{ip}/32"], status=PbxTrunk.Status.PENDING,
        )
        peer = PbxGatewayPeer(company=company, trunk=trunk, node=node, tunnel_ip=ip)
        token = _new_token(peer)
        peer.save()
    return peer, token


def reset_enrollment(peer: PbxGatewayPeer) -> str:
    """New token; drops an enrolled key (the node removes the peer) and the READY state."""
    with transaction.atomic():
        token = _new_token(peer)
        peer.public_key = ""
        peer.status = PbxGatewayPeer.Status.AWAITING_ENROLLMENT
        peer.enrolled_at = peer.verified_at = None
        peer.save()
        PbxTrunk.objects.filter(pk=peer.trunk_id).update(status=PbxTrunk.Status.PENDING, updated_at=timezone.now())
    _nudge(peer.node_id)
    return token


def _public_key(value) -> str:
    value = (value or "").strip() if isinstance(value, str) else ""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(value) != 44 or len(raw) != 32:
        raise CallRefused("invalid_public_key", "Enter the gateway's WireGuard public key (44 characters).", 400)
    return value


def enroll(token, public_key) -> tuple[PbxGatewayPeer, dict]:
    """Spend a one-time token on the gateway's public key. Every refusal looks the same to a guesser."""
    refused = CallRefused("invalid_token", "This enrollment code is not valid or has expired.", 400)
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        raise refused
    key = _public_key(public_key)
    with transaction.atomic():
        peer = (
            PbxGatewayPeer.objects.select_for_update().select_related("node", "trunk")
            .filter(enrollment_token_hash=hash_token(token), status=PbxGatewayPeer.Status.AWAITING_ENROLLMENT)
            .first()
        )
        if peer is None or peer.token_expires_at is None or peer.token_expires_at < timezone.now():
            raise refused
        node = peer.node
        if not (node.gateway_wg_public_key and node.gateway_wg_endpoint):
            # Keep the token: the operator still has to finish the node's wg-gw setup.
            raise CallRefused("gateway_link_not_configured", "The phone system is not ready to accept gateways yet.", 503)
        if PbxGatewayPeer.objects.filter(public_key=key).exclude(pk=peer.pk).exists():
            raise CallRefused("invalid_public_key", "This key is already enrolled for another gateway.", 400)
        peer.public_key = key
        peer.status = PbxGatewayPeer.Status.ENROLLED
        peer.enrolled_at = timezone.now()
        peer.enrollment_token_hash = ""
        peer.token_expires_at = None
        peer.save()
    _nudge(peer.node_id)
    return peer, gateway_config(peer)


def gateway_config(peer: PbxGatewayPeer) -> dict:
    node = peer.node
    server = str(node.gateway_wg_address)
    wireguard = "\n".join([
        "[Interface]",
        f"Address = {peer.tunnel_ip}/32",
        "PrivateKey = <this gateway's private key>",
        "",
        "[Peer]",
        f"PublicKey = {node.gateway_wg_public_key}",
        f"Endpoint = {node.gateway_wg_endpoint}",
        f"AllowedIPs = {server}/32",
        "PersistentKeepalive = 25",
        "",
    ])
    return {
        "tunnel_address": f"{peer.tunnel_ip}/32",
        "wireguard": {
            "peer_public_key": node.gateway_wg_public_key,
            "endpoint": node.gateway_wg_endpoint,
            "allowed_ips": f"{server}/32",
            "persistent_keepalive": 25,
            "config": wireguard,
        },
        # The gateway sends calls to the node over the tunnel; its source address is its identity.
        "sip": {"server": server, "port": 5060, "transport": "udp"},
    }


def verify(peer: PbxGatewayPeer) -> PbxGatewayPeer:
    from . import provisioning

    if peer.status == PbxGatewayPeer.Status.AWAITING_ENROLLMENT or not peer.public_key:
        raise CallRefused("not_enrolled", "Enroll the gateway first.")
    trunk = peer.trunk
    if trunk.health != "ready":
        raise CallRefused("gateway_unreachable", "The phone system cannot reach this gateway yet. Check its tunnel and SIP settings.")
    with transaction.atomic():
        trunk.status = PbxTrunk.Status.READY
        trunk.save(update_fields=["status", "updated_at"])
        peer.status = PbxGatewayPeer.Status.VERIFIED
        peer.verified_at = timezone.now()
        peer.save(update_fields=["status", "verified_at", "updated_at"])
        provisioning.request_recompile_for_company(peer.company)
    return peer


def desired_peers(node: PbxNode) -> list[dict]:
    """The wg-gw peers this node should have: every enrolled gateway of its live tenants."""
    peers = (
        PbxGatewayPeer.objects.filter(node=node)
        .exclude(public_key="")
        .exclude(trunk__status=PbxTrunk.Status.DISABLED)
        .exclude(company__pbx_tenant__status=PbxTenant.Status.DEPROVISIONED)
        .order_by("tunnel_ip")
    )
    return [{"public_key": p.public_key, "allowed_ip": f"{p.tunnel_ip}/32"} for p in peers]


def _nudge(node_id) -> None:
    from .provisioning import nudge_node

    transaction.on_commit(lambda: nudge_node(node_id))
