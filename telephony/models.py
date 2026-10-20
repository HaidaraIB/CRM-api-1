"""
Loop Cloud PBX — CRM-side data model.

Tenancy follows the rest of the codebase: rows carry a ``company`` FK and every
query is scoped by the authenticated user's company (ADR 0002 in CRM-pbx). Rows
use UUID primary keys. Relations between two telephony rows (number → trunk,
route → extension …) must stay inside one company; Django has no composite FKs,
so ``telephony.services.tenancy.assert_same_company`` enforces it in the service
layer, and the isolation tests try to break it.

On the PBX side a tenant is named by ``PbxTenant.realm_key``, never by company id.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from . import contract


class PbxNode(models.Model):
    """An Asterisk node (PBX VPS). Talks to the CRM over the private link."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DRAINING = "draining", "Draining (no new tenants)"
        OFFLINE = "offline", "Offline"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, unique=True)
    region = models.CharField(max_length=64, blank=True, default="")
    # Worker control API base on the private link, e.g. http://10.77.0.2:7070
    control_url = models.CharField(max_length=200, blank=True, default="")
    # SHA-256 of the token the node sends to the CRM (contract §4). Never the token.
    node_token_hash = models.CharField(max_length=64, unique=True)
    # Token the CRM sends to the node's worker; the CRM needs the plaintext, so
    # it is stored Fernet-encrypted (integrations.encryption).
    crm_token_encrypted = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    max_tenants = models.PositiveIntegerField(default=100)
    # Customer GSM gateways reach this node over its own WireGuard interface
    # (wg-gw, never the CRM link). Set by the platform operator; handed to an
    # enrolling gateway as its peer.
    gateway_wg_public_key = models.CharField(max_length=44, blank=True, default="")
    gateway_wg_endpoint = models.CharField(max_length=200, blank=True, default="")  # public host:port
    gateway_wg_address = models.GenericIPAddressField(protocol="IPv4", default="10.78.0.1")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    asterisk_version = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RecordingMode(models.TextChoices):
    OFF = "off", "Off"
    EXTERNAL = "external", "Calls with outside numbers"
    ALL = "all", "All calls, including internal"


class RecordingNotice(models.TextChoices):
    """Announcement played to the outside party before a recorded call connects."""

    NONE = "none", "No announcement"
    AR = "ar", "Arabic"
    EN = "en", "English"
    AR_EN = "ar_en", "Arabic, then English"


class PbxTenant(models.Model):
    """
    One per company that has telephony. ``status`` is the provisioning state from
    spec §5 (PENDING/ACTIVE/FAILED/SUSPENDED); versions track the compile/apply
    pipeline (contract §2).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        FAILED = "failed", "Failed"
        SUSPENDED = "suspended", "Suspended"
        DEPROVISIONED = "deprovisioned", "Deprovisioned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField("companies.Company", on_delete=models.CASCADE, related_name="pbx_tenant")
    realm_key = models.CharField(max_length=8, unique=True, default=contract.generate_realm_key, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    home_node = models.ForeignKey(PbxNode, on_delete=models.PROTECT, null=True, blank=True, related_name="tenants")
    # Outbound calling blocked (subscription lapse, fraud, kill switch). Internal
    # calls and inbound keep working; the compiler emits reject-only routes.
    outbound_suspended = models.BooleanField(default=False)
    suspended_reason = models.CharField(max_length=200, blank=True, default="")
    # Suspended by the subscription reconciliation (lapsed, or a plan without
    # telephony): only these are resumed automatically, never a fraud or operator one.
    suspended_for_billing = models.BooleanField(default=False)
    # Call recording (contract §7). Off by default: the legal basis is the
    # tenant's to establish, so turning it on records who attested to it and when.
    recording_mode = models.CharField(max_length=10, choices=RecordingMode.choices, default=RecordingMode.OFF)
    recording_notice = models.CharField(max_length=8, choices=RecordingNotice.choices, default=RecordingNotice.AR_EN)
    recording_accepted_at = models.DateTimeField(null=True, blank=True)
    recording_accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Retention (tenant setting): the purge job deletes recordings older than this,
    # except while the platform operator holds them (legal hold).
    recording_retention_days = models.PositiveSmallIntegerField(default=90)
    recording_legal_hold = models.BooleanField(default=False)
    # Set while stored recordings use the plan's max_pbx_recording_bytes: the
    # compiler stops starting new recordings until space is freed.
    recording_paused_quota = models.BooleanField(default=False)
    # Platform overrides of settings.TELEPHONY_FRAUD_DEFAULTS, shipped to the node
    # in the artifact manifest (contract section 2, ``fraud``).
    fraud_limits = models.JSONField(default=dict, blank=True)
    # The node's immediate outbound switch (Stasis:t<key>-outbound) is set: by its
    # fraud detector, or by a platform suspend. A resume must clear it on the node.
    outbound_blocked_on_node = models.BooleanField(default=False)
    # Node migration (migrate-node): the previous home node, still serving the
    # tenant until it applies the ``remove`` artifact ``draining_version``.
    draining_node = models.ForeignKey(
        PbxNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="draining_tenants"
    )
    draining_version = models.PositiveIntegerField(null=True, blank=True)
    # Highest compiled artifact version / version the home node confirmed live.
    config_version = models.PositiveIntegerField(default=0)
    applied_version = models.PositiveIntegerField(null=True, blank=True)
    last_apply_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.tag} ({self.company_id})"

    @property
    def tag(self) -> str:
        return contract.tenant_tag(self.realm_key)


class DeviceType(models.TextChoices):
    WEBRTC = "webrtc", "Browser (WebRTC)"
    DESK_PHONE = "desk_phone", "Desk phone"
    SOFTPHONE = "softphone", "Softphone app"


class PbxExtension(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    class AgentStatus(models.TextChoices):
        """Spec section 6 ``PUT /agents/me/status``. A paused agent is not rung."""

        AVAILABLE = "available", "Available"
        PAUSED = "paused", "Paused"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_extensions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pbx_extensions"
    )
    number = models.CharField(max_length=6)
    display_name = models.CharField(max_length=80, blank=True, default="")
    device_type = models.CharField(max_length=16, choices=DeviceType.choices, default=DeviceType.WEBRTC)
    # Static digest secret (desk phones/softphones; WebRTC in the dev profile).
    sip_secret_encrypted = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    # Device presence, reported by the node (agent.status_changed).
    registered = models.BooleanField(default=False)
    registered_device = models.CharField(max_length=16, blank=True, default="")
    registered_changed_at = models.DateTimeField(null=True, blank=True)
    # Set by the agent; mirrored to the home node (contract section 6, agent status).
    agent_status = models.CharField(max_length=12, choices=AgentStatus.choices, default=AgentStatus.AVAILABLE)
    agent_status_changed_at = models.DateTimeField(null=True, blank=True)
    # This employee's outbound SIM. The gateway chooses the physical port from the
    # caller id, so the number is enough. Null: the company outbound route's caller id.
    outbound_number = models.ForeignKey(
        "PbxNumber", on_delete=models.SET_NULL, null=True, blank=True, related_name="outbound_extensions"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="pbx_ext_unique_number_per_company"),
            models.UniqueConstraint(
                fields=["company", "user"], condition=Q(user__isnull=False), name="pbx_ext_one_per_user"
            ),
        ]

    def __str__(self):
        return f"{self.number} ({self.company_id})"


class PbxTrunk(models.Model):
    class Kind(models.TextChoices):
        SIP = "sip", "SIP trunk"
        GSM_GATEWAY = "gsm_gateway", "GSM gateway"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        READY = "ready", "Ready"
        DEGRADED = "degraded", "Degraded"
        DOWN = "down", "Down"
        DISABLED = "disabled", "Disabled"

    class Transport(models.TextChoices):
        UDP = "udp", "UDP"
        TCP = "tcp", "TCP"
        TLS = "tls", "TLS"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_trunks")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SIP)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    host = models.CharField(max_length=253)
    port = models.PositiveIntegerField(default=5060)
    transport = models.CharField(max_length=8, choices=Transport.choices, default=Transport.UDP)
    # Outbound auth toward the carrier (optional) — stored encrypted.
    auth_username = models.CharField(max_length=80, blank=True, default="")
    auth_secret_encrypted = models.TextField(blank=True, default="")
    # Inbound identification: only these source networks are this trunk (spec §3:
    # trunk ownership by source IP/VPN peer, never by From/Caller-ID).
    allowed_sources = models.JSONField(default=list, blank=True)
    # Reachability from the node's health checks (gateway.status_changed). Kept
    # apart from ``status``: an OPTIONS reply is not proof of ownership (spec §4),
    # so health never moves a trunk out of PENDING.
    health = models.CharField(max_length=16, blank=True, default="")
    health_reason = models.CharField(max_length=200, blank=True, default="")
    health_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="pbx_trunk_unique_name_per_company"),
        ]

    def __str__(self):
        return f"{self.name} ({self.company_id})"


class PbxGatewayPeer(models.Model):
    """
    A customer GSM gateway's WireGuard peer on its tenant's home node (spec section
    5, gateway onboarding). Created with a one-time enrollment token (only its
    sha256 is kept); the installer enrolls the gateway's public key with it and
    gets the tunnel and SIP settings back. The gateway's trunk is identified by the
    pinned tunnel address alone, and becomes READY only after verification.
    """

    class Status(models.TextChoices):
        AWAITING_ENROLLMENT = "awaiting_enrollment", "Awaiting enrollment"
        ENROLLED = "enrolled", "Enrolled"
        VERIFIED = "verified", "Verified"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_gateway_peers")
    trunk = models.OneToOneField(PbxTrunk, on_delete=models.CASCADE, related_name="gateway_peer")
    node = models.ForeignKey(PbxNode, on_delete=models.PROTECT, related_name="gateway_peers")
    tunnel_ip = models.GenericIPAddressField(protocol="IPv4")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AWAITING_ENROLLMENT)
    enrollment_token_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    public_key = models.CharField(max_length=44, blank=True, default="")
    enrolled_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["node", "tunnel_ip"], name="pbx_gw_unique_tunnel_ip_per_node"),
            models.UniqueConstraint(
                fields=["public_key"], condition=~Q(public_key=""), name="pbx_gw_unique_public_key"
            ),
        ]


class PbxNumber(models.Model):
    """A DID owned by a tenant. Globally unique: one number, one tenant."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_numbers")
    trunk = models.ForeignKey(PbxTrunk, on_delete=models.PROTECT, related_name="numbers")
    e164 = models.CharField(max_length=16, unique=True)
    label = models.CharField(max_length=80, blank=True, default="")
    # Set only by the platform operator after ownership checks (spec §5). Unverified
    # numbers are neither routed inbound nor usable as caller id.
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["e164"]

    def __str__(self):
        return self.e164


class DestinationType(models.TextChoices):
    """Where a call goes next (inbound routes, IVR options and timeouts, queue overflow, closed hours)."""

    EXTENSION = "extension", "Extension"
    IVR = "ivr", "IVR menu"
    QUEUE = "queue", "Queue"
    HANGUP = "hangup", "Hang up"


# A typed destination is "<prefix>_type" plus at most one target FK (extension,
# IVR menu, queue). Targets are rows of the same company: the serializers check
# that, and the compiler checks it again. PROTECT, so a target a route still uses
# cannot be deleted from under it (the API answers 409 in_use).


class PbxBusinessHours(models.Model):
    """
    When a tenant is open, in its own timezone. ``rules`` is typed data,
    ``[{"days": [0..6] (Mon=0), "start": "09:00", "end": "17:00"}]``, and
    ``closed_dates`` are whole days (``"2026-12-25"``) that override the weekly
    rules. The compiler renders them as GotoIfTime / STRFTIME checks.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_business_hours")
    name = models.CharField(max_length=80)
    timezone = models.CharField(max_length=64, default="Asia/Baghdad")
    rules = models.JSONField(default=list)
    closed_dates = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="pbx_hours_unique_name_per_company"),
        ]

    def __str__(self):
        return f"{self.name} ({self.company_id})"


class PbxSound(models.Model):
    """
    A tenant's own recorded audio (a custom IVR greeting). Stored as raw G.711
    A-law, 8 kHz mono (``services.sounds``), at most 60 s. The compiler ships the
    ones a menu uses inside the tenant's artifact as ``sound_t<key>_<id8>.alaw``
    (sha256-checked by the node) and plays them by absolute path.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_sounds")
    name = models.CharField(max_length=80)
    audio = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    duration_ms = models.PositiveIntegerField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="pbx_sound_unique_name_per_company"),
        ]

    def __str__(self):
        return f"{self.name} ({self.company_id})"


class PbxIvrMenu(models.Model):
    """
    A voice menu: plays a prompt, waits for one key and routes it. A wrong key
    replays the prompt up to ``max_retries`` times; silence or too many wrong keys
    go to the timeout destination. Compiled into ``t<key>-ivr-<id8>``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_ivr_menus")
    name = models.CharField(max_length=80)
    # A platform prompt under <astdatadir>/sounds, installed on every node. The
    # serializer offers a fixed list; a tenant never types a path.
    prompt = models.CharField(max_length=64, default="loop/ivr-default")
    # The tenant's own recording, played instead of ``prompt`` when set.
    greeting = models.ForeignKey(PbxSound, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    timeout_s = models.PositiveSmallIntegerField(default=5)
    max_retries = models.PositiveSmallIntegerField(default=2)
    timeout_type = models.CharField(max_length=16, choices=DestinationType.choices, default=DestinationType.HANGUP)
    timeout_extension = models.ForeignKey(
        "telephony.PbxExtension", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    timeout_ivr = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    timeout_queue = models.ForeignKey(
        "telephony.PbxQueue", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="pbx_ivr_unique_name_per_company"),
        ]

    def __str__(self):
        return f"{self.name} ({self.company_id})"


class PbxIvrOption(models.Model):
    DIGITS = [str(d) for d in range(10)] + ["*", "#"]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="+")
    menu = models.ForeignKey(PbxIvrMenu, on_delete=models.CASCADE, related_name="options")
    digit = models.CharField(max_length=1)
    destination_type = models.CharField(max_length=16, choices=DestinationType.choices, default=DestinationType.EXTENSION)
    extension = models.ForeignKey(
        "telephony.PbxExtension", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    ivr = models.ForeignKey(PbxIvrMenu, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    queue = models.ForeignKey("telephony.PbxQueue", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["digit"]
        constraints = [
            models.UniqueConstraint(fields=["menu", "digit"], name="pbx_ivr_option_unique_digit"),
        ]


class PbxQueue(models.Model):
    """
    A call queue (Asterisk app_queue), compiled into ``queues_t<key>.conf``. Members
    are this tenant's extensions, and a paused agent is skipped (agent status).
    After ``max_wait_s`` the caller goes to the overflow destination.
    """

    class Strategy(models.TextChoices):
        RINGALL = "ringall", "Ring all"
        RRMEMORY = "rrmemory", "Round robin"
        LEASTRECENT = "leastrecent", "Least recent"
        FEWESTCALLS = "fewestcalls", "Fewest calls"
        RANDOM = "random", "Random"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_queues")
    name = models.CharField(max_length=80)
    strategy = models.CharField(max_length=16, choices=Strategy.choices, default=Strategy.RINGALL)
    member_timeout_s = models.PositiveSmallIntegerField(default=20)
    max_wait_s = models.PositiveIntegerField(default=300)
    wrapup_s = models.PositiveSmallIntegerField(default=0)
    # Overflow never goes to another queue (no queue-to-queue chains).
    overflow_type = models.CharField(max_length=16, choices=DestinationType.choices, default=DestinationType.HANGUP)
    overflow_extension = models.ForeignKey(
        "telephony.PbxExtension", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    overflow_ivr = models.ForeignKey(PbxIvrMenu, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="pbx_queue_unique_name_per_company"),
        ]

    def __str__(self):
        return f"{self.name} ({self.company_id})"


class PbxQueueMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="+")
    queue = models.ForeignKey(PbxQueue, on_delete=models.CASCADE, related_name="members")
    extension = models.ForeignKey(PbxExtension, on_delete=models.CASCADE, related_name="queue_memberships")
    penalty = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["penalty", "extension__number"]
        constraints = [
            models.UniqueConstraint(fields=["queue", "extension"], name="pbx_queue_member_unique"),
        ]


class PbxInboundRoute(models.Model):
    """
    DID → destination (extension, IVR menu or queue). With business hours, a call
    outside them goes to the ``closed_*`` destination instead.
    """

    DestinationType = DestinationType

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_inbound_routes")
    number = models.OneToOneField(PbxNumber, on_delete=models.CASCADE, related_name="inbound_route")
    destination_type = models.CharField(max_length=16, choices=DestinationType.choices, default=DestinationType.EXTENSION)
    extension = models.ForeignKey(PbxExtension, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    ivr = models.ForeignKey(PbxIvrMenu, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    queue = models.ForeignKey(PbxQueue, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    business_hours = models.ForeignKey(
        PbxBusinessHours, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    closed_type = models.CharField(max_length=16, choices=DestinationType.choices, default=DestinationType.HANGUP)
    closed_extension = models.ForeignKey(
        PbxExtension, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    closed_ivr = models.ForeignKey(PbxIvrMenu, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    closed_queue = models.ForeignKey(PbxQueue, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PbxOutboundRoute(models.Model):
    """
    Allowlisted destinations → trunk, presenting a verified number as caller id.

    ``patterns`` is typed data, not dialplan: a list of
    ``{"prefix": "+9647", "digits": 9}`` meaning "+9647 followed by exactly 9
    digits". The compiler turns it into ``_+9647XXXXXXXXX``. Anything no route
    matches is rejected (default-reject).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_outbound_routes")
    name = models.CharField(max_length=80)
    priority = models.PositiveIntegerField(default=100)
    patterns = models.JSONField(default=list)
    trunk = models.ForeignKey(PbxTrunk, on_delete=models.PROTECT, related_name="outbound_routes")
    caller_id = models.ForeignKey(PbxNumber, on_delete=models.PROTECT, related_name="+")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="pbx_outroute_unique_name_per_company"),
        ]


class PbxArtifact(models.Model):
    """
    A compiled, immutable config artifact (contract §2). Contains secrets, so the
    tarball is stored Fernet-encrypted and served only to the tenant's home node.
    """

    class Action(models.TextChoices):
        APPLY = "apply", "Apply"
        REMOVE = "remove", "Remove"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(PbxTenant, on_delete=models.CASCADE, related_name="artifacts")
    version = models.PositiveIntegerField()
    action = models.CharField(max_length=8, choices=Action.choices, default=Action.APPLY)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    blob_encrypted = models.TextField()
    manifest = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "version"], name="pbx_artifact_unique_version"),
        ]


class PbxProvisioningJob(models.Model):
    """
    One requested change to a tenant's PBX config, run asynchronously and
    idempotently (spec §5: never touch live config from a web request).

    pending → compiling → awaiting_apply → succeeded
                       ↘ failed           ↘ failed (node rejected / rolled back)
    """

    class Kind(models.TextChoices):
        PROVISION = "provision", "Provision"
        RECOMPILE = "recompile", "Recompile"
        SUSPEND = "suspend", "Suspend outbound"
        RESUME = "resume", "Resume outbound"
        DEPROVISION = "deprovision", "Deprovision"
        MIGRATE = "migrate", "Move to another node"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPILING = "compiling", "Compiling"
        AWAITING_APPLY = "awaiting_apply", "Awaiting node"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SUPERSEDED = "superseded", "Superseded"

    ACTIVE_STATUSES = (Status.PENDING, Status.COMPILING, Status.AWAITING_APPLY)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_jobs")
    tenant = models.ForeignKey(PbxTenant, on_delete=models.CASCADE, related_name="jobs")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    target_version = models.PositiveIntegerField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status"], name="pbx_job_tenant_status")]


# ---------------------------------------------------------------------------
# Calls (Phase 2): written only by the internal events API (contract §5)
# ---------------------------------------------------------------------------


class PbxCall(models.Model):
    """
    One call as the CRM sees it: every leg sharing an Asterisk linkedid.

    ``id`` is the contract call id: a uuid4 the CRM chose (click-to-call) or the
    value derived from (tenant, linkedid) — CRM-pbx contract §3 / ADR 0004.
    Live states (queued/ringing/in_progress) are replaced by exactly one terminal
    status on call.ended, and a terminal call never goes back.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"
        INTERNAL = "internal", "Internal"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RINGING = "ringing", "Ringing"
        IN_PROGRESS = "in_progress", "In progress"
        ANSWERED = "answered", "Answered"
        BUSY = "busy", "Busy"
        NO_ANSWER = "no_answer", "No answer"
        FAILED = "failed", "Failed"
        ABANDONED = "abandoned", "Abandoned"
        MISSED = "missed", "Missed"
        CANCELLED = "cancelled", "Cancelled"

    LIVE_STATUSES = (Status.QUEUED, Status.RINGING, Status.IN_PROGRESS)

    id = models.UUIDField(primary_key=True, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_calls")
    tenant = models.ForeignKey(PbxTenant, on_delete=models.CASCADE, related_name="calls")
    linked_id = models.CharField(max_length=64, blank=True, default="")
    direction = models.CharField(max_length=16, choices=Direction.choices, blank=True, default="")
    origin = models.CharField(max_length=8, blank=True, default="")  # "crm" | "pbx"
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RINGING)
    from_party = models.CharField(max_length=20, blank=True, default="")
    to_party = models.CharField(max_length=20, blank=True, default="")
    did = models.CharField(max_length=16, blank=True, default="")
    trunk = models.ForeignKey(PbxTrunk, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    extension = models.ForeignKey(PbxExtension, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    answered_by = models.ForeignKey(PbxExtension, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    crm_contact = models.ForeignKey("crm.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="pbx_calls")
    on_hold = models.BooleanField(default=False)
    # Queue wait (call.queued / call.dequeued; wallboard). The last queue the caller
    # waited in; queue_left_at is empty while the caller is still waiting.
    queue = models.ForeignKey("telephony.PbxQueue", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    queued_at = models.DateTimeField(null=True, blank=True)
    queue_left_at = models.DateTimeField(null=True, blank=True)
    queue_outcome = models.CharField(max_length=10, blank=True, default="")  # answered | abandoned | overflow
    hangup_cause = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_s = models.PositiveIntegerField(null=True, blank=True)
    billsec_s = models.PositiveIntegerField(null=True, blank=True)
    recorded = models.BooleanField(default=False)
    # Set once the missed-call push went out (services/missed.py), so a late event never sends it twice.
    missed_notified_at = models.DateTimeField(null=True, blank=True)
    # Set once the emergency-call alert went out (services/emergency.py), once per call.
    emergency_notified_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "linked_id"], condition=~Q(linked_id=""), name="pbx_call_unique_linkedid"
            ),
        ]
        indexes = [
            models.Index(fields=["company", "-started_at"], name="pbx_call_company_started"),
            models.Index(fields=["company", "status"], name="pbx_call_company_status"),
            models.Index(fields=["company", "queued_at"], name="pbx_call_company_queued"),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status not in self.LIVE_STATUSES


class PbxCallLeg(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="+")
    call = models.ForeignKey(PbxCall, on_delete=models.CASCADE, related_name="legs")
    uniqueid = models.CharField(max_length=64)
    endpoint = models.CharField(max_length=80, blank=True, default="")
    extension = models.ForeignKey(PbxExtension, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    source = models.CharField(max_length=20, blank=True, default="")
    destination = models.CharField(max_length=20, blank=True, default="")
    disposition = models.CharField(max_length=16, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["started_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "uniqueid"], name="pbx_leg_unique_uniqueid"),
        ]


class PbxCallEvent(models.Model):
    """
    Durable, deduplicated log of what nodes reported (at-least-once delivery):
    the primary key is the node's event_id, so a replayed event is a no-op.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="+")
    tenant = models.ForeignKey(PbxTenant, on_delete=models.CASCADE, related_name="events")
    node = models.ForeignKey(PbxNode, on_delete=models.SET_NULL, null=True, related_name="+")
    call = models.ForeignKey(PbxCall, on_delete=models.CASCADE, null=True, blank=True, related_name="events")
    type = models.CharField(max_length=32)
    seq = models.BigIntegerField()
    occurred_at = models.DateTimeField()
    data = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_at", "seq"]
        indexes = [models.Index(fields=["company", "received_at"], name="pbx_event_company_received")]


class PbxRecording(models.Model):
    """
    Metadata for audio the PBX side uploaded straight to object storage (contract
    §7). Django never holds the audio; playback is a short-lived signed URL. A
    recording reported before its call's events is kept with ``call`` empty and
    linked when the call appears (``call_uuid``).
    """

    class Format(models.TextChoices):
        WAV = "wav", "WAV"
        OGG = "ogg", "Ogg"
        MP3 = "mp3", "MP3"

    id = models.UUIDField(primary_key=True, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_recordings")
    tenant = models.ForeignKey(PbxTenant, on_delete=models.CASCADE, related_name="recordings")
    call = models.ForeignKey(PbxCall, on_delete=models.CASCADE, null=True, blank=True, related_name="recordings")
    call_uuid = models.UUIDField(db_index=True)
    storage_key = models.CharField(max_length=255, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    duration_s = models.PositiveIntegerField()
    format = models.CharField(max_length=4, choices=Format.choices)
    started_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at"]


# ---------------------------------------------------------------------------
# SaaS controls (Phase 5)
# ---------------------------------------------------------------------------


class PbxFraudAlert(models.Model):
    """
    A ``fraud.alert`` a node raised (contract section 5): the node's detector saw
    outbound traffic cross a tenant's limit. ``outbound_suspended`` means the node
    already blocked the tenant's outbound calls and the CRM suspended it too; only
    a platform operator resumes it.
    """

    class Rule(models.TextChoices):
        CALLS_PER_HOUR = "calls_per_hour", "Outbound calls per hour"
        NEW_DESTINATIONS = "new_destinations", "New destinations per hour"
        INTERNATIONAL_SHARE = "international_share", "International calls per hour"
        PREMIUM_PREFIX = "premium_prefix", "Premium-rate destination"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_fraud_alerts")
    tenant = models.ForeignKey(PbxTenant, on_delete=models.CASCADE, related_name="fraud_alerts")
    node = models.ForeignKey(PbxNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    event_id = models.UUIDField(unique=True)
    rule = models.CharField(max_length=24, choices=Rule.choices)
    severity = models.CharField(max_length=10, default="warning")  # warning | critical
    action_taken = models.CharField(max_length=24, default="none")  # none | outbound_suspended
    observed = models.FloatField(null=True, blank=True)
    threshold = models.FloatField(null=True, blank=True)
    destination = models.CharField(max_length=20, blank=True, default="")
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolution_note = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["resolved_at", "-occurred_at"], name="pbx_fraud_open")]


class PbxUsageDaily(models.Model):
    """
    One company's phone-system use on one day (platform time zone), rolled up from
    ``PbxCall`` / ``PbxRecording`` by ``manage.py rollup_pbx_usage``. Recomputed,
    never incremented, so a re-run or a late call fixes the day.
    """

    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="pbx_usage_days")
    day = models.DateField()
    calls_inbound = models.PositiveIntegerField(default=0)
    calls_outbound = models.PositiveIntegerField(default=0)
    calls_internal = models.PositiveIntegerField(default=0)
    calls_answered = models.PositiveIntegerField(default=0)
    calls_missed = models.PositiveIntegerField(default=0)  # inbound missed, abandoned or unanswered
    talk_seconds = models.PositiveIntegerField(default=0)
    outbound_minutes = models.PositiveIntegerField(default=0)  # billed like carriers: every started minute
    recordings = models.PositiveIntegerField(default=0)
    recording_bytes = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-day"]
        constraints = [models.UniqueConstraint(fields=["company", "day"], name="pbx_usage_unique_day")]
