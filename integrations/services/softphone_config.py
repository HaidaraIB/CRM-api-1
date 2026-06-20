"""Build softphone registration config for web/mobile clients."""

from __future__ import annotations

from integrations.encryption import decrypt_token
from integrations.models import PbxSettings, UserPbxExtension
from integrations.services.softphone_turn import build_turn_ice_entry


def _default_wss_uri(domain: str) -> str:
    host = (domain or "").strip().rstrip("/")
    if not host:
        return ""
    return f"wss://{host}:8089/ws"


def build_softphone_config(
    settings: PbxSettings,
    mapping: UserPbxExtension,
    *,
    platform: str = "web",
) -> dict:
    """Return SIP/WebRTC connection parameters for a mapped user."""
    domain = (settings.sip_domain or settings.pbx_host or "").strip()
    sip_password = ""
    if mapping.sip_password:
        try:
            sip_password = decrypt_token(mapping.sip_password) or ""
        except Exception:
            sip_password = ""

    wss_uri = (settings.wss_uri or "").strip() or _default_wss_uri(domain)
    stun = (settings.stun_server or "").strip()
    turn = (settings.turn_server or "").strip()

    ice_servers: list[dict] = []
    if stun:
        ice_servers.append({"urls": stun})
    if turn:
        ice_servers.append(build_turn_ice_entry(turn, user_id=mapping.user_id))

    extension = mapping.extension.strip()
    sip_port = settings.sip_port or 5162

    if platform == "web":
        sip_uri = f"sip:{extension}@{domain}"
        registrar = wss_uri
        transport = "wss"
    else:
        # sip_ua supports WSS (TransportType.WS) or plain TCP — no native TLS SIP.
        sip_uri = f"sip:{extension}@{domain}"
        if wss_uri:
            registrar = wss_uri
            transport = "wss"
        else:
            registrar = f"sip:{domain}:{sip_port};transport=tcp"
            transport = "tcp"

    return {
        "extension": extension,
        "sip_password": sip_password,
        "sip_domain": domain,
        "sip_port": sip_port,
        "sip_uri": sip_uri,
        "registrar_uri": registrar,
        "wss_uri": wss_uri,
        "transport": transport,
        "ice_servers": ice_servers,
        "display_name": mapping.user.get_full_name() or mapping.user.username,
    }


def user_softphone_ready(settings: PbxSettings, mapping: UserPbxExtension | None) -> bool:
    if not settings.is_enabled or not settings.softphone_enabled:
        return False
    if not mapping or not mapping.softphone_enabled:
        return False
    domain = (settings.sip_domain or settings.pbx_host or "").strip()
    if not domain or not mapping.extension:
        return False
    if not mapping.sip_password:
        return False
    return True
