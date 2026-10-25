"""
Config compiler (CRM-pbx/docs/contract.md §2).

Renders one tenant's PJSIP and dialplan files from typed rows. Rules:

* every name comes from ``telephony.contract`` (tenant-qualified, never company id);
* nothing a tenant typed reaches the config except through a strict allowlist
  (numbers are validated E.164/extension strings, hosts and CIDRs are parsed,
  display names are reduced to letters/digits/spaces);
* extensions and DIDs are listed explicitly and every entry context ends in
  catch-all rejects (default-reject);
* the output is deterministic — same rows, same bytes — and is checked with
  ``contract.check_config_text`` (the same check the node applier runs) before it
  can be published.

The compiler only reads; versioning and publishing live in ``provisioning``.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field

from django.conf import settings

from integrations.encryption import decrypt_token

from .. import contract
from ..models import (
    DestinationType,
    DeviceType,
    PbxBusinessHours,
    PbxExtension,
    PbxInboundRoute,
    PbxIvrMenu,
    PbxNumber,
    PbxOutboundRoute,
    PbxQueue,
    PbxSound,
    PbxTenant,
    PbxTrunk,
    RecordingMode,
    RecordingNotice,
)
from .emergency import numbers as emergency_numbers

COMPILER_VERSION = "1"

# Q.850 causes used for rejects (contract §2): 1 = unallocated number (unknown
# internal extension / DID), 21 = call rejected (destination not allowed),
# 17 = user busy (a paused agent; the caller hears busy, SIP 486).
CAUSE_UNALLOCATED = 1
CAUSE_REJECTED = 21
CAUSE_BUSY = 17

HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_DISPLAY_KEEP_RE = re.compile(r"[^\w .']|_", re.UNICODE)

# Recordings (contract §7). The worker parses this spool layout:
#   <spool>/<tag>/<call_id>/<epoch>-<uniqueid>.wav, plus "<file>.done" once
# MixMonitor has closed the file. Absolute, so it never depends on the node's
# monitor-directory setting.
RECORDING_SPOOL = "/var/spool/asterisk/recording/loop"
RECORDING_FORMAT = "wav"
# Installed on every node under <astdatadir>/sounds/loop/ (CRM-pbx runbook). A
# missing prompt is logged and skipped by Playback; the call still connects.
NOTICE_PROMPTS = {
    RecordingNotice.NONE: [],
    RecordingNotice.AR: ["loop/recording-notice-ar"],
    RecordingNotice.EN: ["loop/recording-notice-en"],
    RecordingNotice.AR_EN: ["loop/recording-notice-ar", "loop/recording-notice-en"],
}

TRANSPORTS = {
    PbxTrunk.Transport.UDP: "transport-udp",
    PbxTrunk.Transport.TCP: "transport-tcp",
    PbxTrunk.Transport.TLS: "transport-tls",
}

# Edge mode (contract §10): Kamailio strips this header from everything it
# receives and sets it only after its own check (auth_ephemeral for browsers, the
# permissions source map for trunks). Endpoints with no Asterisk-side auth are
# identified by it alone, so a forged From user can never select them.
EDGE_IDENTITY_HEADER = "X-Loop-Endpoint"


class CompileError(ValueError):
    pass


@dataclass
class CompiledConfig:
    files: dict[str, str | bytes]  # config text; sounds (custom greetings) as bytes
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Value validation shared with the serializers
# ---------------------------------------------------------------------------


def clean_display_name(value: str) -> str:
    """Letters, digits, spaces, '.' and apostrophe only (no quotes, <>, ;, -, _)."""
    return re.sub(r"\s+", " ", _DISPLAY_KEEP_RE.sub("", value or "")).strip()[:40]


def validate_host(value: str) -> str:
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not HOST_RE.match(value):
        raise CompileError(f"invalid host: {value!r}")
    return value.lower()


def validate_cidr(value: str) -> str:
    try:
        return str(ipaddress.ip_network(str(value).strip(), strict=False))
    except ValueError as exc:
        raise CompileError(f"invalid network: {value!r}") from exc


def validate_pattern(item) -> tuple[str, int]:
    """``{"prefix": "+9647", "digits": 9}`` → ("+9647", 9); total ≤ 15 digits (E.164)."""
    if not isinstance(item, dict):
        raise CompileError("pattern must be an object with prefix and digits")
    prefix, digits = item.get("prefix"), item.get("digits")
    if not isinstance(prefix, str) or not re.fullmatch(r"\+[1-9][0-9]{0,14}", prefix):
        raise CompileError(f"invalid prefix: {prefix!r}")
    if not isinstance(digits, int) or isinstance(digits, bool) or not (0 <= digits <= 14):
        raise CompileError(f"invalid digits: {digits!r}")
    if len(prefix) - 1 + digits > 15 or len(prefix) - 1 + digits < 7:
        raise CompileError("prefix + digits must give a 7-15 digit E.164 number")
    return prefix, digits


def dialplan_pattern(prefix: str, digits: int) -> str:
    return f"_{prefix}{'X' * digits}" if digits else prefix


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _secret(encrypted: str) -> str:
    value = decrypt_token(encrypted) if encrypted else ""
    if value and not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", value):
        # Generated secrets are url-safe base64; anything else could break the
        # config line (or inject one), so refuse rather than render it.
        raise CompileError("stored SIP secret has an unexpected format")
    return value


def _webrtc_auth_mode() -> str:
    mode = getattr(settings, "TELEPHONY_WEBRTC_AUTH", "static")
    return mode if mode in ("static", "edge") else "static"


def _sip_domain() -> str:
    """Platform SIP domain (settings, never tenant data), validated like any host."""
    raw = (getattr(settings, "TELEPHONY_SIP_DOMAIN", "") or "").strip()
    return validate_host(raw) if raw else ""


def _edge_proxy() -> str:
    """Validated private ``host:port`` for Asterisk -> Kamailio traffic."""
    raw = (getattr(settings, "TELEPHONY_SIP_EDGE_PROXY", "") or "").strip()
    if not raw:
        if _webrtc_auth_mode() == "edge":
            raise CompileError("TELEPHONY_SIP_EDGE_PROXY is required in edge mode")
        return ""
    host, sep, port = raw.rpartition(":")
    if not sep or not port.isdigit() or not (1 <= int(port) <= 65535):
        raise CompileError("TELEPHONY_SIP_EDGE_PROXY must be host:port")
    validate_host(host)
    return f"{host.lower()}:{int(port)}"


def _render_pjsip(rk: str, tag: str, extensions, trunks, warnings) -> str:
    out = [f"; Loop PBX tenant config {tag}. Generated by the CRM compiler; do not edit.", ""]
    sip_domain = _sip_domain()
    edge = _webrtc_auth_mode() == "edge"
    edge_proxy = _edge_proxy() if edge else ""
    if edge and not sip_domain:
        raise CompileError("TELEPHONY_SIP_DOMAIN is required in edge mode")

    for ext in extensions:
        name = contract.extension_endpoint(rk, ext.number)
        webrtc = ext.device_type == DeviceType.WEBRTC
        secret = _secret(ext.sip_secret_encrypted)
        # WebRTC auth happens at the edge (Kamailio auth_ephemeral) in production;
        # "static" is the dev profile where browsers register straight to Asterisk.
        with_auth = bool(secret) and not (webrtc and _webrtc_auth_mode() == "edge")
        display = clean_display_name(ext.display_name) or f"Ext {ext.number}"
        out += [f"; extension {ext.number} ({ext.device_type})", f"[{name}]", "type = endpoint",
                f"context = {contract.context_from_agent(rk)}"]
        if webrtc and not edge:
            out += ["transport = transport-ws", "webrtc = yes", "disallow = all", "allow = alaw,opus"]
            if sip_domain:
                # Otherwise Asterisk puts the node's hostname in From; a hostname
                # that is not a valid SIP host (e.g. starts with a digit) makes
                # JsSIP drop the INVITE silently (verified on the dev stack).
                out.append(f"from_domain = {sip_domain}")
        else:
            out += ["transport = transport-udp", "disallow = all", "allow = alaw", "direct_media = no"]
            if webrtc and edge:
                # Preserve the public AOR in the Request-URI while forcing the
                # only permitted private hop through Kamailio.
                out += [_outbound_proxy(edge_proxy), "identify_by = header"]
            elif edge:
                out.append("identify_by = username")  # desk phones keep digest auth
        if with_auth:
            out.append(f"auth = {name}-auth")
        out += [f"aors = {name}", f'callerid = "{display}" <{ext.number}>', f"set_var = __LOOP_TENANT={tag}", ""]
        if with_auth:
            out += [f"[{name}-auth]", "type = auth", "auth_type = userpass", f"username = {name}",
                    f"password = {secret}", ""]
        elif not secret and not webrtc:
            warnings.append(f"extension {ext.number} has no SIP secret; it cannot register")
        if webrtc and edge:
            # The edge answers this qualify from its registrations (no WebSocket is
            # involved), and the worker places calls only to an "online" endpoint: a
            # short interval keeps a browser that just registered from waiting long.
            out += [f"[{name}]", "type = aor", f"contact = sip:{name}@{sip_domain}", _outbound_proxy(edge_proxy),
                    "qualify_frequency = 10", ""]
            out += _edge_identify(name)
        else:
            out += [f"[{name}]", "type = aor", f"max_contacts = {2 if webrtc else 1}", "remove_existing = yes"]
            if edge:
                out += ["support_path = yes", "qualify_frequency = 30"]
            out.append("")

    for trunk in trunks:
        name = contract.trunk_endpoint(rk, trunk.id)
        host = validate_host(trunk.host)
        sources = [validate_cidr(s) for s in (trunk.allowed_sources or [])]
        auth_secret = _secret(trunk.auth_secret_encrypted)
        auth_user = trunk.auth_username if re.fullmatch(r"[A-Za-z0-9_.+\-]{1,80}", trunk.auth_username or "") else ""
        # Behind the edge, Asterisk's only hop is loopback UDP to Kamailio; the
        # trunk's own transport is carried in the contact for Kamailio to use.
        transport = "transport-udp" if edge else TRANSPORTS[trunk.transport]
        out += [f"; trunk {contract.short_id(trunk.id)} ({trunk.kind})", f"[{name}]", "type = endpoint",
                f"context = {contract.context_from_trunk(rk)}", f"transport = {transport}",
                "disallow = all", "allow = alaw", "direct_media = no", f"aors = {name}"]
        if edge:
            out += [_outbound_proxy(edge_proxy), "identify_by = header"]
        if auth_user and auth_secret:
            out.append(f"outbound_auth = {name}-auth")
        out += [f"set_var = __LOOP_TENANT={tag}", ""]
        # GSM gateways are qualified (SIP OPTIONS over their tunnel) so the node can
        # report their reachability (gateway.status_changed); carrier trunks are not pinged.
        qualify = 30 if trunk.kind == PbxTrunk.Kind.GSM_GATEWAY else 0
        contact = f"sip:{host}:{int(trunk.port)}"
        if edge and trunk.transport != PbxTrunk.Transport.UDP:
            contact += f"\\;transport={trunk.transport.lower()}"
        aor = [f"[{name}]", "type = aor", f"contact = {contact}"]
        if edge:
            aor.append(_outbound_proxy(edge_proxy))  # qualify OPTIONS take the same private hop
        out += [*aor, f"qualify_frequency = {qualify}", ""]
        if auth_user and auth_secret:
            out += [f"[{name}-auth]", "type = auth", "auth_type = userpass", f"username = {auth_user}",
                    f"password = {auth_secret}", ""]
        if edge:
            out += _edge_identify(name)
        if sources:
            if not edge:
                out += [f"[{name}-identify]", "type = identify", f"endpoint = {name}", f"match = {','.join(sources)}", ""]
        else:
            warnings.append(f"trunk {trunk.name}: no allowed source networks; inbound calls cannot be identified")
    return "\n".join(out).rstrip("\n") + "\n"


def _outbound_proxy(edge_proxy: str) -> str:
    # ";" starts a comment in Asterisk config: unescaped, ";lr" is dropped and
    # Asterisk strict-routes (Request-URI = the proxy), verified on the dev edge.
    return f"outbound_proxy = sip:{edge_proxy}\\;lr"


def _edge_identify(name: str) -> list[str]:
    return [f"[{name}-identify]", "type = identify", f"endpoint = {name}",
            f"match_header = {EDGE_IDENTITY_HEADER}: {name}", ""]


def _render_kamailio_routes(tenant: PbxTenant, trunks, version: int) -> str:
    """Non-executable source-to-endpoint data loaded by Kamailio permissions."""
    rows = []
    for trunk in trunks:
        if trunk.status != PbxTrunk.Status.READY or not trunk.allowed_sources:
            continue
        rows.append({
            "endpoint": contract.trunk_endpoint(tenant.realm_key, trunk.id),
            "sources": sorted(validate_cidr(source) for source in trunk.allowed_sources),
        })
    payload = {
        "schema": contract.KAMAILIO_ROUTE_SCHEMA,
        "tenant": tenant.tag,
        "version": version,
        "trunks": sorted(rows, key=lambda row: row["endpoint"]),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    contract.check_kamailio_routes(tenant.realm_key, contract.kamailio_filename(tenant.realm_key), text)
    return text


def _validate_edge_source_ownership(tenant: PbxTenant, trunks) -> None:
    """A source network may identify exactly one ready trunk on a PBX node."""
    if tenant.home_node_id is None:
        return
    candidates = []
    for trunk in trunks:
        if trunk.status == PbxTrunk.Status.READY:
            candidates.extend((validate_cidr(source), trunk.name, tenant.tag) for source in trunk.allowed_sources)
    others = (
        PbxTrunk.objects.filter(
            status=PbxTrunk.Status.READY,
            company__pbx_tenant__home_node_id=tenant.home_node_id,
        )
        .exclude(company_id=tenant.company_id)
        .select_related("company__pbx_tenant")
    )
    for trunk in others:
        other_tag = trunk.company.pbx_tenant.tag
        candidates.extend((validate_cidr(source), trunk.name, other_tag) for source in trunk.allowed_sources)
    networks = [(ipaddress.ip_network(source), name, tag) for source, name, tag in candidates]
    for index, (network, name, tag) in enumerate(networks):
        for other, other_name, other_tag in networks[index + 1:]:
            if network.overlaps(other):
                raise CompileError(
                    f"edge source network overlap: {tag}/{name} {network} and "
                    f"{other_tag}/{other_name} {other}"
                )


def _rejects(cause: int) -> list[str]:
    return [f"exten => _X.,1,Hangup({cause})", f"exten => _+X.,1,Hangup({cause})", f"exten => i,1,Hangup({cause})"]


def _render_recording_subs(tag: str, sub_record: str, sub_notice: str, prompts: list[str]) -> list[str]:
    """
    ``sub-record`` starts one MixMonitor per channel (``LOOP_REC`` guards repeat
    Gosubs, e.g. from-trunk → internal), conversation only (``b``: nothing before
    the bridge, so ringing and the notice are not recorded). The file name is
    built from FILTERed values only, because it also appears in the post-process
    command MixMonitor runs through the shell when it has closed the file.
    """
    out = [
        "; Record this call once, bridged audio only (contract section 7).",
        f"[{sub_record}]",
        'exten => s,1,GotoIf($["${LOOP_REC}" != ""]?done)',
        f" same => n,Set(LOOP_REC={RECORDING_SPOOL}/{tag}/${{FILTER(0-9a-f-,${{LOOP_CALL_ID}})}}/"
        f"${{EPOCH}}-${{FILTER(0-9.,${{UNIQUEID}})}}.{RECORDING_FORMAT})",
        " same => n,Set(__LOOP_RECORDING=1)",
        " same => n,MixMonitor(${LOOP_REC},b,/bin/touch ${LOOP_REC}.done)",
        " same => n(done),Return()",
        "",
    ]
    if prompts:
        out += [
            "; Recording notice for the outside party, played before the call connects.",
            f"[{sub_notice}]",
            f"exten => s,1,Playback({'&'.join(prompts)})",
            " same => n,Return()",
            "",
        ]
    return out


# ---------------------------------------------------------------------------
# Routing (Phase 4): destinations, business hours, IVR menus, queues
# ---------------------------------------------------------------------------

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TZ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")
_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_PROMPT_RE = re.compile(r"^loop/[a-z0-9][a-z0-9-]{0,40}$")
QUEUE_STRATEGIES = {"ringall", "rrmemory", "leastrecent", "fewestcalls", "random"}


@dataclass
class _Routing:
    """What one tenant's routing may point at, by id (only this company's rows)."""

    rk: str
    extensions: dict  # id -> PbxExtension (active)
    ivrs: dict
    queues: dict
    hours: dict
    warnings: list
    sounds: dict = field(default_factory=dict)  # id -> PbxSound a menu plays (this company's)

    def goto(self, dest_type: str, extension_id, ivr_id, queue_id, where: str) -> str:
        """One dialplan application that sends the call to a typed destination."""
        if dest_type == DestinationType.EXTENSION:
            ext = self.extensions.get(extension_id)
            if ext is not None:
                return f"Goto({contract.context_internal(self.rk)},{ext.number},1)"
        elif dest_type == DestinationType.IVR:
            if ivr_id in self.ivrs:
                return f"Goto({contract.context_ivr(self.rk, ivr_id)},s,1)"
        elif dest_type == DestinationType.QUEUE:
            if queue_id in self.queues:
                return f"Goto({contract.context_queue(self.rk, queue_id)},s,1)"
        elif dest_type == DestinationType.HANGUP:
            return "Hangup()"
        self.warnings.append(f"{where}: destination unavailable; the call is hung up")
        return "Hangup()"


def _hhmm_minus_one(value: str) -> str:
    """GotoIfTime ranges include their last minute; an opening period ends before ``end``."""
    hh, mm = int(value[:2]), int(value[3:])
    total = hh * 60 + mm - 1
    return f"{total // 60:02d}:{total % 60:02d}"


def _render_hours(rk: str, hours: PbxBusinessHours) -> list[str]:
    """``t<key>-sub-hours-<id8>``: sets LOOP_OPEN to 1 or 0 for "now" in the tenant's timezone."""
    tz = hours.timezone or ""
    if not _TZ_RE.match(tz):
        raise CompileError(f"business hours {hours.name!r}: invalid timezone")
    out = [
        f"; Business hours: {clean_display_name(hours.name)} ({tz})",
        f"[{contract.context_sub(rk, 'hours-' + contract.short_id(hours.id))}]",
        "exten => s,1,Set(LOOP_OPEN=0)",
    ]
    for day in sorted(set(hours.closed_dates or [])):
        if not (isinstance(day, str) and _DATE_RE.match(day)):
            raise CompileError(f"business hours {hours.name!r}: invalid closed date")
        out.append(f' same => n,GotoIf($["${{STRFTIME(${{EPOCH}},{tz},%Y-%m-%d)}}" = "{day}"]?done)')
    for rule in hours.rules or []:
        days, start, end = rule.get("days"), rule.get("start"), rule.get("end")
        if (not isinstance(days, list) or not days or any(d not in range(7) for d in days)
                or not _HHMM_RE.match(str(start)) or not _HHMM_RE.match(str(end)) or end <= start):
            raise CompileError(f"business hours {hours.name!r}: invalid opening period")
        weekdays = "&".join(_WEEKDAYS[d] for d in sorted(set(days)))
        out.append(f" same => n,GotoIfTime({start}-{_hhmm_minus_one(end)},{weekdays},*,*,{tz}?open)")
    out += [" same => n,Goto(done)", " same => n(open),Set(LOOP_OPEN=1)", " same => n(done),Return()", ""]
    return out


def _render_ivr(rk: str, menu: PbxIvrMenu, routing: _Routing) -> list[str]:
    prompt = menu.prompt if _PROMPT_RE.match(menu.prompt or "") else "loop/ivr-default"
    if menu.greeting_id is not None:
        if menu.greeting_id in routing.sounds:
            # The tenant's own recording, shipped in this artifact; played by
            # absolute path (contract section 2, sounds).
            prompt = contract.sound_path(rk, menu.greeting_id)
        else:
            routing.warnings.append(f"IVR {clean_display_name(menu.name)}: greeting unavailable; platform prompt played")
    timeout = min(max(int(menu.timeout_s or 5), 1), 30)
    retries = min(int(menu.max_retries or 0), 5)
    where = f"IVR {clean_display_name(menu.name)}"
    out = [
        f"; IVR menu: {clean_display_name(menu.name)}",
        f"[{contract.context_ivr(rk, menu.id)}]",
        "exten => s,1,Set(LOOP_IVR_TRIES=0)",
        " same => n,Answer()",
        f" same => n(menu),Background({prompt})",
        f" same => n,WaitExten({timeout})",
    ]
    for option in sorted(menu.options.all(), key=lambda o: o.digit):
        if option.digit not in ("0123456789*#") or len(option.digit) != 1:
            raise CompileError(f"{where}: invalid key")
        target = routing.goto(option.destination_type, option.extension_id, option.ivr_id, option.queue_id,
                              f"{where} key {option.digit}")
        out.append(f"exten => {option.digit},1,{target}")
    timeout_target = routing.goto(menu.timeout_type, menu.timeout_extension_id, menu.timeout_ivr_id,
                                  menu.timeout_queue_id, f"{where} timeout")
    out += [
        f"exten => t,1,{timeout_target}",
        # A wrong key replays the menu; after max_retries it is treated as silence.
        "exten => i,1,Set(LOOP_IVR_TRIES=$[${LOOP_IVR_TRIES} + 1])",
        f" same => n,GotoIf($[${{LOOP_IVR_TRIES}} > {retries}]?t,1)",
        " same => n,Goto(s,menu)",
        f"exten => _X.,1,Hangup({CAUSE_UNALLOCATED})",
        f"exten => _+X.,1,Hangup({CAUSE_UNALLOCATED})",
        "",
    ]
    return out


def _render_queue_entry(rk: str, queue: PbxQueue, routing: _Routing) -> list[str]:
    max_wait = min(max(int(queue.max_wait_s or 300), 10), 3600)
    overflow = routing.goto(queue.overflow_type, queue.overflow_extension_id, queue.overflow_ivr_id, None,
                            f"queue {clean_display_name(queue.name)} overflow")
    return [
        f"; Queue: {clean_display_name(queue.name)}",
        f"[{contract.context_queue(rk, queue.id)}]",
        f"exten => s,1,Queue({contract.queue_name(rk, queue.id)},,,,{max_wait})",
        f" same => n,{overflow}",
        "",
    ]


def _render_queues(rk: str, tag: str, queues, routing: _Routing) -> str:
    """``queues_t<key>.conf``: members ring through ``t<key>-agent`` (pause check), state from their endpoint."""
    out = [f"; Loop PBX tenant queues {tag}. Generated by the CRM compiler; do not edit.", ""]
    for queue in queues:
        strategy = queue.strategy if queue.strategy in QUEUE_STRATEGIES else "ringall"
        out += [
            f"; {clean_display_name(queue.name)}",
            f"[{contract.queue_name(rk, queue.id)}]",
            f"strategy = {strategy}",
            f"timeout = {min(max(int(queue.member_timeout_s or 20), 5), 120)}",
            "retry = 2",
            f"wrapuptime = {min(int(queue.wrapup_s or 0), 600)}",
            "ringinuse = no",
            "autopause = no",
            "joinempty = yes",
            "leavewhenempty = no",
        ]
        members = 0
        for member in queue.members.all():
            ext = routing.extensions.get(member.extension_id)
            if ext is None:
                routing.warnings.append(f"queue {clean_display_name(queue.name)}: extension unavailable; not a member")
                continue
            display = clean_display_name(ext.display_name) or f"Ext {ext.number}"
            out.append(
                f"member => Local/{ext.number}@{contract.context_agent(rk)},{min(int(member.penalty), 10)},"
                f"{display},PJSIP/{contract.extension_endpoint(rk, ext.number)}"
            )
            members += 1
        if not members:
            routing.warnings.append(f"queue {clean_display_name(queue.name)}: no members; callers go to overflow")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _emergency_exit(tenant: PbxTenant, routes, usable_trunks: dict, verified_numbers) -> tuple | None:
    """
    The trunk and caller id emergency calls leave by: the highest-priority outbound
    route that is usable, else the first verified number on a usable trunk (a tenant
    with no outbound route at all still reaches emergency services). None: no way out.
    """
    for route in routes:
        caller = route.caller_id
        if (route.trunk_id in usable_trunks and caller.verified_at is not None
                and caller.company_id == tenant.company_id and contract.E164_RE.match(caller.e164)):
            return route.trunk_id, caller.e164
    for number in verified_numbers:
        if number.trunk_id in usable_trunks and contract.E164_RE.match(number.e164):
            return number.trunk_id, number.e164
    return None


def _render_emergency(rk: str, sub_entry: str, exit_, warnings) -> list[str]:
    """
    Emergency numbers (services/emergency.py): compiled for every tenant, before and
    regardless of an outbound suspension, and without the node's outbound switch, so
    billing, plan, fraud and platform suspensions never block them. No recording and
    no notice: nothing may delay the call.
    """
    numbers = emergency_numbers()
    if not numbers:
        return []
    if exit_ is None:
        warnings.append("emergency numbers are not routed: no usable trunk with a verified number")
        return []
    trunk_id, caller = exit_
    trunk_name = contract.trunk_endpoint(rk, trunk_id)
    out = ["; Emergency numbers (platform list): always routed, even while outbound calling is suspended."]
    for number in numbers:
        out += [
            f"exten => {number},1,Gosub({sub_entry},s,1)",
            f' same => n,Set(CALLERID(all)="{caller}" <{caller}>)',
            f" same => n,Dial(PJSIP/${{EXTEN}}@{trunk_name},60)",
            " same => n,Hangup()",
        ]
    return out


def _render_outbound_identity(rk: str, extensions, usable_trunks: dict, warnings) -> tuple[list[str], str | None]:
    """A subroutine that sets LOOP_OUT_TRUNK / LOOP_OUT_CID for an extension's own SIM.

    Empty when no extension has one. The gateway picks the SIM port from the caller id.
    """
    rows = []
    for ext in extensions:
        number = ext.outbound_number
        if number is None:
            continue
        if number.verified_at is None or number.trunk_id not in usable_trunks or not contract.E164_RE.match(number.e164 or ""):
            warnings.append(f"extension {ext.number}: outbound SIM is not usable; company caller id used")
            continue
        endpoint = contract.extension_endpoint(rk, ext.number)
        trunk_name = contract.trunk_endpoint(rk, number.trunk_id)
        match = (
            f'$["${{CHANNEL(endpoint)}}" = "{endpoint}" | "${{CHANNEL(endpoint)}}" = "PJSIP/{endpoint}"]'
        )
        rows.append(f" same => n,ExecIf({match}?Set(LOOP_OUT_TRUNK={trunk_name}))")
        rows.append(f" same => n,ExecIf({match}?Set(LOOP_OUT_CID={number.e164}))")
    if not rows:
        return [], None
    sub = contract.context_sub(rk, "outbound-id")
    return [
        "; Per-extension outbound SIM. Empty LOOP_OUT_CID means the route's caller id.",
        f"[{sub}]",
        "exten => s,1,Set(LOOP_OUT_TRUNK=)",
        " same => n,Set(LOOP_OUT_CID=)",
        *rows,
        " same => n,Return()",
        "",
    ], sub


def _outbound_caller_lines(identity: str | None, caller: str, trunk_name: str, notice_sub: str | None) -> list[str]:
    """Route caller id, or the calling endpoint's own SIM when the subroutine set one."""
    notice = f",U({notice_sub})" if notice_sub else ""
    route = [
        f' same => n,Set(CALLERID(all)="{caller}" <{caller}>)',
        f" same => n,Dial(PJSIP/${{EXTEN}}@{trunk_name},60{notice})",
        " same => n,Hangup()",
    ]
    if identity is None:
        return route
    return [
        f" same => n,Gosub({identity},s,1)",
        ' same => n,GotoIf($["${LOOP_OUT_CID}" != ""]?ownsim)',
        *route,
        ' same => n(ownsim),Set(CALLERID(all)="${LOOP_OUT_CID}" <${LOOP_OUT_CID}>)',
        f" same => n,Dial(PJSIP/${{EXTEN}}@${{LOOP_OUT_TRUNK}},60{notice})",
        " same => n,Hangup()",
    ]


def _render_extensions(rk: str, tag: str, tenant: PbxTenant, extensions, trunks, routes, inbound, warnings,
                       routing: _Routing | None = None, verified_numbers=()) -> str:
    sub_entry = contract.context_sub(rk, "entry")
    sub_headers = contract.context_sub(rk, "agent-headers")
    sub_record = contract.context_sub(rk, "record")
    sub_notice = contract.context_sub(rk, "notice")
    internal = contract.context_internal(rk)
    # Storage full (max_pbx_recording_bytes): no new recordings until space is freed.
    # TELEPHONY_RECORDING_ALLOWED off (the platform's legal gate): no recording at all.
    recording = (
        settings.TELEPHONY_RECORDING_ALLOWED
        and tenant.recording_mode in (RecordingMode.EXTERNAL, RecordingMode.ALL)
        and not tenant.recording_paused_quota
    )
    prompts = NOTICE_PROMPTS.get(tenant.recording_notice, []) if recording else []
    out = [
        f"; Loop PBX tenant dialplan {tag}. Generated by the CRM compiler; do not edit.",
        "",
        "; Tag the call and derive LOOP_CALL_ID unless the CRM pre-assigned one (contract section 3).",
        f"[{sub_entry}]",
        f"exten => s,1,Set(__LOOP_TENANT={tag})",
        ' same => n,ExecIf($["${LOOP_CALL_ID}" = ""]?Set(_LOOP_H=${SHA1(loop-call:${LOOP_TENANT}:${CHANNEL(linkedid)})}))',
        ' same => n,ExecIf($["${LOOP_CALL_ID}" = ""]?Set(__LOOP_CALL_ID=${LOOP_H:0:8}-${LOOP_H:8:4}-${LOOP_H:12:4}-${LOOP_H:16:4}-${LOOP_H:20:12}))',
        " same => n,Return()",
        "",
        f"[{sub_headers}]",
        "exten => s,1,Set(PJSIP_HEADER(add,X-Loop-Call-Id)=${LOOP_CALL_ID})",
        " same => n,Return()",
        "",
    ]
    if recording:
        out += _render_recording_subs(tag, sub_record, sub_notice, prompts)
    out += [
        "; A paused agent (PUT /telephony/agents/me/status) is not rung: the worker",
        "; sets the extension's agent device to BUSY (contract section 6).",
        f"[{internal}]",
    ]
    for ext in extensions:
        out.append(f"exten => {ext.number},1,Gosub({sub_entry},s,1)")
        out.append(f' same => n,GotoIf($["${{DEVICE_STATE({contract.agent_device(rk, ext.number)})}}" = "BUSY"]?paused)')
        if recording and tenant.recording_mode == RecordingMode.ALL:
            out.append(f" same => n,Gosub({sub_record},s,1)")
        out += [
            # m: the caller hears hold music while this phone rings, including a second
            # call the browser offers instead of rejecting (call waiting).
            f" same => n,Dial(PJSIP/{contract.extension_endpoint(rk, ext.number)},30,b({sub_headers}^s^1)m)",
            " same => n,Hangup()",
            f" same => n(paused),Hangup({CAUSE_BUSY})",
        ]
    out += _rejects(CAUSE_UNALLOCATED) + [""]

    if routing is not None and routing.queues:
        # Queue members (Local channels from queues_t<key>.conf): the same pause
        # check as -internal, no Gosub(entry) (LOOP_CALL_ID is inherited) and no
        # second recording.
        out += ["; Queue members ring through here; a paused agent answers busy.", f"[{contract.context_agent(rk)}]"]
        for ext in extensions:
            out += [
                f'exten => {ext.number},1,GotoIf($["${{DEVICE_STATE({contract.agent_device(rk, ext.number)})}}" = "BUSY"]?paused)',
                f" same => n,Dial(PJSIP/{contract.extension_endpoint(rk, ext.number)},,b({sub_headers}^s^1))",
                " same => n,Hangup()",
                f" same => n(paused),Hangup({CAUSE_BUSY})",
            ]
        out += _rejects(CAUSE_UNALLOCATED) + [""]
    if routing is not None:
        for hours in routing.hours.values():
            out += _render_hours(rk, hours)
        for menu in routing.ivrs.values():
            out += _render_ivr(rk, menu, routing)
        for queue in routing.queues.values():
            out += _render_queue_entry(rk, queue, routing)

    out.append(f"[{contract.context_from_agent(rk)}]")
    emergency = set(emergency_numbers())
    for ext in extensions:
        if ext.number in emergency:
            # Created before the number was declared an emergency number (the API refuses it now).
            warnings.append(f"extension {ext.number} is an emergency number: dialling it calls emergency services")
            continue
        out.append(f"exten => {ext.number},1,Goto({internal},{ext.number},1)")

    usable_trunks = {t.id: t for t in trunks}
    identity_lines, identity = _render_outbound_identity(rk, extensions, usable_trunks, warnings)
    out += _render_emergency(rk, sub_entry, _emergency_exit(tenant, routes, usable_trunks, verified_numbers), warnings)
    if tenant.outbound_suspended:
        out.append("; outbound calling is suspended for this tenant")
    else:
        out += identity_lines
        seen: set[str] = set()
        for route in routes:
            if route.trunk_id not in usable_trunks:
                warnings.append(f"outbound route {route.name}: trunk unavailable; skipped")
                continue
            if route.caller_id.verified_at is None or route.caller_id.company_id != tenant.company_id:
                warnings.append(f"outbound route {route.name}: caller id is not a verified number; skipped")
                continue
            trunk_name = contract.trunk_endpoint(rk, route.trunk_id)
            caller = route.caller_id.e164
            if not contract.E164_RE.match(caller):
                raise CompileError(f"invalid caller id {caller!r}")
            for item in route.patterns or []:
                pattern = dialplan_pattern(*validate_pattern(item))
                if pattern in seen:
                    warnings.append(f"outbound route {route.name}: {pattern} already routed by a higher-priority route")
                    continue
                seen.add(pattern)
                out += [
                    f"; outbound route: {clean_display_name(route.name)}",
                    f"exten => {pattern},1,Gosub({sub_entry},s,1)",
                    # The node's immediate outbound switch (fraud block / platform suspend,
                    # contract section 6) holds even before this config is recompiled.
                    f' same => n,GotoIf($["${{DEVICE_STATE({contract.outbound_block_device(rk)})}}" = "BUSY"]?blocked)',
                    *([f" same => n,Gosub({sub_record},s,1)"] if recording else []),
                    *_outbound_caller_lines(identity, caller, trunk_name, sub_notice if prompts else None),
                    f" same => n(blocked),Hangup({CAUSE_REJECTED})",
                ]
    out += _rejects(CAUSE_REJECTED) + ["", f"[{contract.context_from_trunk(rk)}]"]

    ext_by_id = {e.id: e for e in extensions}
    for route in inbound:
        number = route.number
        if number.verified_at is None:
            warnings.append(f"inbound {number.e164}: number not verified; not routed")
            continue
        if number.trunk_id not in usable_trunks:
            warnings.append(f"inbound {number.e164}: trunk unavailable; not routed")
            continue
        if route.destination_type == DestinationType.EXTENSION and ext_by_id.get(route.extension_id) is None:
            warnings.append(f"inbound {number.e164}: destination extension unavailable; not routed")
            continue
        if not contract.E164_RE.match(number.e164):
            raise CompileError(f"invalid DID {number.e164!r}")
        where = f"inbound {number.e164}"
        target = routing.goto(route.destination_type, route.extension_id, route.ivr_id, route.queue_id, where)
        hours = routing.hours.get(route.business_hours_id) if route.business_hours_id else None
        if route.business_hours_id and hours is None:
            warnings.append(f"{where}: business hours unavailable; always open")
        closed = (
            routing.goto(route.closed_type, route.closed_extension_id, route.closed_ivr_id, route.closed_queue_id,
                         f"{where} closed")
            if hours is not None else None
        )
        # Carriers deliver the DID with or without the leading '+'; route both.
        for did in (number.e164, number.e164[1:]):
            out.append(f"exten => {did},1,Gosub({sub_entry},s,1)")
            if recording:
                out.append(f" same => n,Gosub({sub_record},s,1)")
            if prompts:
                out.append(f" same => n,Gosub({sub_notice},s,1)")
            if hours is not None:
                out += [
                    f" same => n,Gosub({contract.context_sub(rk, 'hours-' + contract.short_id(hours.id))},s,1)",
                    ' same => n,GotoIf($["${LOOP_OPEN}" = "1"]?open)',
                    f" same => n,{closed}",
                    f" same => n(open),{target}",
                ]
            else:
                out.append(f" same => n,{target}")
    out += _rejects(CAUSE_UNALLOCATED)
    return "\n".join(out) + "\n"


def compile_tenant(tenant: PbxTenant, *, version: int | None = None) -> CompiledConfig:
    """Render a tenant's files from the current rows. Read-only."""
    rk = tenant.realm_key
    tag = contract.tenant_tag(rk)
    company_id = tenant.company_id
    warnings: list[str] = []

    extensions = list(
        PbxExtension.objects.filter(company_id=company_id, status=PbxExtension.Status.ACTIVE)
        .select_related("outbound_number", "outbound_number__trunk")
        .order_by("number")
    )
    for ext in extensions:
        contract.validate_extension(ext.number)
        if ext.number in contract.RESERVED_EXTENSIONS:
            raise CompileError(f"extension {ext.number} is reserved")
    trunks = [
        t for t in PbxTrunk.objects.filter(company_id=company_id).exclude(status=PbxTrunk.Status.DISABLED)
    ]
    trunks.sort(key=lambda t: contract.short_id(t.id))
    shorts = [contract.short_id(t.id) for t in trunks]
    if len(set(shorts)) != len(shorts):
        raise CompileError("two trunks share an id prefix; recreate one of them")
    if _webrtc_auth_mode() == "edge":
        _validate_edge_source_ownership(tenant, trunks)
    routes = list(
        PbxOutboundRoute.objects.filter(company_id=company_id, enabled=True)
        .select_related("caller_id")
        .order_by("priority", "name")
    )
    inbound = list(
        PbxInboundRoute.objects.filter(company_id=company_id).select_related("number").order_by("number__e164")
    )
    ivrs = list(PbxIvrMenu.objects.filter(company_id=company_id).prefetch_related("options").order_by("id"))
    queues = list(PbxQueue.objects.filter(company_id=company_id).prefetch_related("members").order_by("id"))
    hours = list(PbxBusinessHours.objects.filter(company_id=company_id).order_by("id"))
    # Only the greetings a menu plays are shipped (the artifact stays small).
    sounds = list(
        PbxSound.objects.filter(company_id=company_id, pk__in=[m.greeting_id for m in ivrs if m.greeting_id])
        .order_by("id")
    )
    for group, label in ((ivrs, "IVR menus"), (queues, "queues"), (hours, "business hours"), (sounds, "greetings")):
        shorts = [contract.short_id(row.id) for row in group]
        if len(set(shorts)) != len(shorts):
            raise CompileError(f"two {label} share an id prefix; recreate one of them")
    routing = _Routing(
        rk=rk,
        extensions={e.id: e for e in extensions},
        ivrs={m.id: m for m in ivrs},
        queues={q.id: q for q in queues},
        hours={h.id: h for h in hours},
        warnings=warnings,
        sounds={snd.id: snd for snd in sounds},
    )

    files = {
        contract.artifact_filename("pjsip", rk): _render_pjsip(rk, tag, extensions, trunks, warnings),
        contract.artifact_filename("extensions", rk): _render_extensions(
            rk, tag, tenant, extensions, trunks, routes, inbound, warnings, routing,
            verified_numbers=list(
                PbxNumber.objects.filter(company_id=company_id, verified_at__isnull=False).order_by("e164")
            ),
        ),
    }
    if queues:
        files[contract.artifact_filename("queues", rk)] = _render_queues(rk, tag, queues, routing)
    if _webrtc_auth_mode() == "edge":
        files[contract.kamailio_filename(rk)] = _render_kamailio_routes(
            tenant, trunks, version if version is not None else tenant.config_version + 1
        )
    for name, text in files.items():
        try:
            kind, _ = contract.parse_artifact_filename(name)
            if kind == "kamailio":
                contract.check_kamailio_routes(rk, name, text)
            else:
                contract.check_config_text(rk, name, text)
        except contract.ArtifactError as exc:
            raise CompileError(str(exc)) from exc
    for snd in sounds:
        audio = bytes(snd.audio)
        if contract.sha256_hex(audio) != snd.sha256:
            raise CompileError(f"greeting {clean_display_name(snd.name)!r} is corrupt; upload it again")
        files[contract.sound_filename(rk, snd.id)] = audio
    return CompiledConfig(files=files, warnings=warnings)


__all__ = ["COMPILER_VERSION", "CompileError", "CompiledConfig", "compile_tenant"]
