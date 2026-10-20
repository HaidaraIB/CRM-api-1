"""
CRM side of the Loop Cloud PBX contract (CRM-pbx/docs/contract.md).

This mirrors the reference implementation in ``CRM-pbx/loop_pbx/contract`` (the
two repos share no code at runtime). ``tests/test_telephony_contract.py`` checks
this module against ``tests/fixtures/pbx_contract/*.json``, which are copies of
``CRM-pbx/contract/vectors``. If the contract changes, regenerate the vectors in
CRM-pbx and copy them here in the same change.

Sections:
  * naming       — tenant tags and every PBX object name (contract §1)
  * credentials  — ephemeral WebRTC/TURN creds and node tokens (contract §4)
  * call ids     — deterministic ids derived from linkedid (contract §3)
  * artifacts    — deterministic tarball + default-reject checks (contract §2)
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import ipaddress
import io
import json
import re
import secrets
import tarfile
import time
import uuid
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Naming (contract §1)
# ---------------------------------------------------------------------------

REALM_KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
REALM_KEY_LENGTH = 8
# At least one digit, so a tenant tag can never read as an ordinary config word
# (``transport-udp`` would otherwise be tenant ``ransport``).
REALM_KEY_PATTERN = r"(?=[a-z2-7]{0,7}[2-7])[a-z2-7]{8}"
REALM_KEY_RE = re.compile(rf"^{REALM_KEY_PATTERN}$")
TENANT_TAG_RE = re.compile(rf"^t({REALM_KEY_PATTERN})$")
EXTENSION_RE = re.compile(r"^[1-9][0-9]{1,5}$")
E164_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")
SHORT_ID_RE = re.compile(r"^[0-9a-f]{8}$")

ARI_APP = "loop"
ARTIFACT_FILE_KINDS = ("pjsip", "extensions", "queues", "kamailio", "sound")
CONFIG_FILE_KINDS = ("pjsip", "extensions", "queues")
# Where the applier's active release lives on every node; sounds are played by
# absolute path from here (no extension: Asterisk picks the .alaw file).
ACTIVE_DIR = "/etc/asterisk/loop/active"

_OWNED_NAME_RE = re.compile(rf"^t({REALM_KEY_PATTERN})[-_]")
_CHANNEL_NAME_RE = re.compile(rf"^(?:PJSIP|Local)/t({REALM_KEY_PATTERN})[-_]")
_ARTIFACT_FILE_RE = re.compile(rf"^(pjsip|extensions|queues)_t({REALM_KEY_PATTERN})\.conf$")
_KAMAILIO_FILE_RE = re.compile(rf"^kamailio_t({REALM_KEY_PATTERN})\.json$")
_SOUND_FILE_RE = re.compile(rf"^sound_t({REALM_KEY_PATTERN})_([0-9a-f]{{8}})\.alaw$")


class NamingError(ValueError):
    pass


def generate_realm_key() -> str:
    while True:
        key = "".join(secrets.choice(REALM_KEY_ALPHABET) for _ in range(REALM_KEY_LENGTH))
        if REALM_KEY_RE.match(key):
            return key


def validate_realm_key(realm_key: str) -> str:
    if not isinstance(realm_key, str) or not REALM_KEY_RE.match(realm_key):
        raise NamingError(f"invalid realm_key: {realm_key!r}")
    return realm_key


def tenant_tag(realm_key: str) -> str:
    return "t" + validate_realm_key(realm_key)


def realm_key_from_tag(tag: str) -> str:
    m = TENANT_TAG_RE.match(tag or "")
    if not m:
        raise NamingError(f"invalid tenant tag: {tag!r}")
    return m.group(1)


def validate_extension(ext: str) -> str:
    if not isinstance(ext, str) or not EXTENSION_RE.match(ext):
        raise NamingError(f"invalid extension: {ext!r}")
    return ext


def short_id(uuid_str) -> str:
    sid = str(uuid_str).replace("-", "").lower()[:8]
    if not SHORT_ID_RE.match(sid):
        raise NamingError(f"invalid uuid for short id: {uuid_str!r}")
    return sid


def extension_endpoint(realm_key: str, ext: str) -> str:
    return f"{tenant_tag(realm_key)}_{validate_extension(ext)}"


def extension_auth(realm_key: str, ext: str) -> str:
    return extension_endpoint(realm_key, ext) + "-auth"


def agent_device(realm_key: str, ext: str) -> str:
    """
    Custom device state an agent's pause lives in (contract section 6, agent status).
    The worker sets it through ARI (Asterisk keeps it in astdb across restarts); the
    compiled dialplan reads it before ringing the extension.
    """
    return f"Stasis:{extension_endpoint(realm_key, ext)}-agent"


def trunk_endpoint(realm_key: str, trunk_id) -> str:
    return f"{tenant_tag(realm_key)}_trk_{short_id(trunk_id)}"


def context_internal(realm_key: str) -> str:
    return f"{tenant_tag(realm_key)}-internal"


def context_from_agent(realm_key: str) -> str:
    return f"{tenant_tag(realm_key)}-from-agent"


def context_from_trunk(realm_key: str) -> str:
    return f"{tenant_tag(realm_key)}-from-trunk"


def context_ivr(realm_key: str, ivr_id) -> str:
    return f"{tenant_tag(realm_key)}-ivr-{short_id(ivr_id)}"


def context_sub(realm_key: str, name: str) -> str:
    return f"{tenant_tag(realm_key)}-sub-{name}"


def queue_name(realm_key: str, queue_id) -> str:
    return f"{tenant_tag(realm_key)}_q_{short_id(queue_id)}"


def context_queue(realm_key: str, queue_id) -> str:
    """Entry of one queue: Queue() then the overflow destination."""
    return f"{tenant_tag(realm_key)}-queue-{short_id(queue_id)}"


def context_agent(realm_key: str) -> str:
    """Queue members ring through here (Local channels), so a paused agent is skipped."""
    return f"{tenant_tag(realm_key)}-agent"


def artifact_filename(kind: str, realm_key: str) -> str:
    if kind not in CONFIG_FILE_KINDS:
        raise NamingError(f"invalid artifact file kind: {kind!r}")
    return f"{kind}_{tenant_tag(realm_key)}.conf"


def kamailio_filename(realm_key: str) -> str:
    return f"kamailio_{tenant_tag(realm_key)}.json"


def sound_filename(realm_key: str, sound_id) -> str:
    """``sound_t<key>_<id8>.alaw``: a tenant's custom audio (IVR greeting), raw A-law 8 kHz."""
    return f"sound_{tenant_tag(realm_key)}_{short_id(sound_id)}.alaw"


def sound_path(realm_key: str, sound_id) -> str:
    """The absolute path the dialplan plays, without the extension."""
    return f"{ACTIVE_DIR}/{sound_filename(realm_key, sound_id)[:-len('.alaw')]}"


def outbound_block_device(realm_key: str) -> str:
    """
    Custom device state that blocks a tenant's outbound calls at once (contract
    section 6): BUSY = blocked. Set by the node's fraud detector or a platform
    suspend; the compiled outbound routes check it before dialling the trunk.
    """
    return f"Stasis:{tenant_tag(realm_key)}-outbound"


def parse_artifact_filename(name: str) -> tuple[str, str]:
    if m := _ARTIFACT_FILE_RE.match(name or ""):
        return m.group(1), m.group(2)
    if m := _SOUND_FILE_RE.match(name or ""):
        return "sound", m.group(1)
    if m := _KAMAILIO_FILE_RE.match(name or ""):
        return "kamailio", m.group(1)
    raise NamingError(f"invalid artifact file name: {name!r}")


def owner_realm_key(name: str) -> str | None:
    m = _OWNED_NAME_RE.match(name or "")
    return m.group(1) if m else None


def channel_realm_key(channel_name: str) -> str | None:
    m = _CHANNEL_NAME_RE.match(channel_name or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Credentials (contract §4)
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS = 8 * 3600
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 24 * 3600
REFRESH_MARGIN_SECONDS = 30 * 60


@dataclass(frozen=True)
class EphemeralCredential:
    username: str
    password: str
    expires_at: int


def ephemeral_password(secret: str, username: str) -> str:
    if not secret:
        raise ValueError("secret must be non-empty")
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def issue_credential(secret: str, sip_user: str, *, ttl: int = DEFAULT_TTL_SECONDS, now: int | None = None) -> EphemeralCredential:
    if not (MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS):
        raise ValueError(f"ttl out of range: {ttl}")
    if ":" in sip_user:
        raise ValueError("sip_user must not contain ':'")
    issued = int(time.time()) if now is None else int(now)
    expires_at = issued + ttl
    username = f"{expires_at}:{sip_user}"
    return EphemeralCredential(username, ephemeral_password(secret, username), expires_at)


def issue_extension_credential(secret: str, realm_key: str, ext: str, **kw) -> EphemeralCredential:
    return issue_credential(secret, extension_endpoint(realm_key, ext), **kw)


def generate_node_token() -> str:
    return "lpn_" + secrets.token_urlsafe(32)


def hash_node_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_sip_secret() -> str:
    """Static digest secret for desk phones and trunks (24+ chars, contract §4)."""
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Call ids (contract §3)
# ---------------------------------------------------------------------------


def derived_call_id(realm_key: str, linkedid: str) -> str:
    if not linkedid:
        raise ValueError("linkedid must be non-empty")
    digest = hashlib.sha1(f"loop-call:{tenant_tag(realm_key)}:{linkedid}".encode()).hexdigest()
    return str(uuid.UUID(hex=digest[:32]))


# ---------------------------------------------------------------------------
# Artifacts (contract §2)
# ---------------------------------------------------------------------------

MANIFEST_SCHEMA = "loop.pbx.artifact/2"
KAMAILIO_ROUTE_SCHEMA = "loop.pbx.kamailio-route/1"
MANIFEST_NAME = "manifest.json"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
# Sounds are raw A-law (8000 bytes a second), at most 60 s each, 20 per artifact.
MAX_SOUND_BYTES = 60 * 8000
MAX_SOUNDS = 20
# Reserved by the node applier's release stamps (contract §2 step 3).
RESERVED_EXTENSIONS = frozenset({"loopcfg"})
RESERVED_VARIABLES = frozenset({"LOOP_CFG_VERSION"})

_FORBIDDEN_DIRECTIVES = re.compile(r"^\s*#\s*(include|tryinclude|exec)\b", re.IGNORECASE | re.MULTILINE)
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]", re.MULTILINE)
_INCLUDE_RE = re.compile(r"^\s*include\s*=>\s*([^\s;]+)", re.IGNORECASE | re.MULTILINE)
_TAGGED_TOKEN_RE = re.compile(rf"\bt({REALM_KEY_PATTERN})(?=[-_])")


class ArtifactError(ValueError):
    pass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_config_text(realm_key: str, name: str, text: str) -> None:
    """Default-reject checks; identical to what the node applier enforces."""
    if _FORBIDDEN_DIRECTIVES.search(text):
        raise ArtifactError(f"{name}: #include/#exec directives are not allowed")
    for section in _SECTION_RE.findall(text):
        base = section.split("(", 1)[0].strip()
        if owner_realm_key(base) != realm_key:
            raise ArtifactError(f"{name}: section [{base}] is not owned by t{realm_key}")
    for target in _INCLUDE_RE.findall(text):
        if owner_realm_key(target) != realm_key:
            raise ArtifactError(f"{name}: include => {target} crosses the tenant boundary")
    for other in set(_TAGGED_TOKEN_RE.findall(text)):
        if other != realm_key:
            raise ArtifactError(f"{name}: references foreign tenant t{other}")


def check_sound(name: str, data: bytes) -> None:
    if not data:
        raise ArtifactError(f"{name}: empty sound")
    if len(data) > MAX_SOUND_BYTES:
        raise ArtifactError(f"{name}: sound longer than {MAX_SOUND_BYTES // 8000} s")


def check_kamailio_routes(realm_key: str, name: str, data: str | bytes) -> dict:
    try:
        payload = json.loads(data)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ArtifactError(f"{name}: invalid JSON") from exc
    tag = tenant_tag(realm_key)
    if not isinstance(payload, dict) or set(payload) != {"schema", "tenant", "version", "trunks"}:
        raise ArtifactError(f"{name}: invalid edge route object")
    if payload["schema"] != KAMAILIO_ROUTE_SCHEMA or payload["tenant"] != tag:
        raise ArtifactError(f"{name}: edge route tenant/schema mismatch")
    if not isinstance(payload["version"], int) or payload["version"] < 1:
        raise ArtifactError(f"{name}: invalid edge route version")
    trunks = payload["trunks"]
    if not isinstance(trunks, list) or len(trunks) > 64:
        raise ArtifactError(f"{name}: invalid edge trunk list")
    endpoint_re = re.compile(rf"^{tag}_trk_[0-9a-f]{{8}}$")
    seen_endpoints: set[str] = set()
    for trunk in trunks:
        if not isinstance(trunk, dict) or set(trunk) != {"endpoint", "sources"}:
            raise ArtifactError(f"{name}: invalid edge trunk")
        endpoint, sources = trunk["endpoint"], trunk["sources"]
        if not isinstance(endpoint, str) or not endpoint_re.fullmatch(endpoint) or endpoint in seen_endpoints:
            raise ArtifactError(f"{name}: invalid or duplicate edge endpoint")
        seen_endpoints.add(endpoint)
        if not isinstance(sources, list) or not sources or len(sources) > 16:
            raise ArtifactError(f"{name}: edge trunk must have 1-16 source networks")
        canonical = set()
        for source in sources:
            try:
                network = ipaddress.ip_network(source, strict=False)
            except (TypeError, ValueError) as exc:
                raise ArtifactError(f"{name}: invalid edge source network") from exc
            if network.prefixlen == 0 or network.is_multicast or network.is_unspecified:
                raise ArtifactError(f"{name}: unsafe edge source network {network}")
            if str(network) in canonical:
                raise ArtifactError(f"{name}: duplicate edge source network {network}")
            canonical.add(str(network))
    return payload


def build_artifact(manifest_fields: dict, files: dict[str, str | bytes]) -> tuple[bytes, dict]:
    """
    Deterministic tar.gz: same input, same bytes (contract §2). Config files are
    text; sounds (``sound_t<key>_<id8>.alaw``) are bytes.
    """
    realm_key = realm_key_from_tag(manifest_fields["tenant"])
    entries, blobs = [], {}
    for fname in sorted(files):
        kind, rk = parse_artifact_filename(fname)
        if rk != realm_key:
            raise ArtifactError(f"{fname} does not belong to {manifest_fields['tenant']}")
        if kind == "sound":
            data = bytes(files[fname])
            check_sound(fname, data)
        elif kind == "kamailio":
            data = files[fname].encode() if isinstance(files[fname], str) else bytes(files[fname])
            check_kamailio_routes(realm_key, fname, data)
        else:
            check_config_text(realm_key, fname, files[fname])
            data = files[fname].encode()
        if len(data) > MAX_FILE_BYTES:
            raise ArtifactError(f"{fname} too large")
        blobs[fname] = data
        entries.append({"name": fname, "kind": kind, "sha256": sha256_hex(data), "bytes": len(data)})
    if sum(1 for e in entries if e["kind"] == "sound") > MAX_SOUNDS:
        raise ArtifactError(f"more than {MAX_SOUNDS} sounds")
    manifest = {"schema": MANIFEST_SCHEMA, **manifest_fields, "files": entries}

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        members = [(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True).encode())] + sorted(blobs.items())
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as fh:
        fh.write(raw.getvalue())
    if gz.tell() > MAX_ARTIFACT_BYTES:
        # The node refuses anything larger (contract section 2); fail the compile instead.
        raise ArtifactError("artifact too large")
    return gz.getvalue(), manifest
