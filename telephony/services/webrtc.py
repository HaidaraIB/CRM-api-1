"""
Browser softphone credentials (CRM-pbx contract section 4).

Production (``TELEPHONY_WEBRTC_AUTH=edge``): short-lived HMAC credentials,
``username = "<expiry>:t<key>_<ext>"``, ``password = base64(HMAC-SHA1(secret,
username))``; Kamailio (auth_ephemeral) and coturn (use-auth-secret) verify them
without a database. SIP and TURN use different secrets. The browser never holds
a static SIP password. The response is cached per user until ``refresh_after``
(the CRM VPS guardrail in the plan), so a page reload does not mint new ones.

Development (``static``, no Kamailio): the dev Asterisk only knows the
extension's digest secret, so that is what the dev softphone gets. Refused
unless DEBUG or ``TELEPHONY_ALLOW_STATIC_WEBRTC`` is set, so a misconfigured
production can never hand a browser a long-lived secret.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache import cache

from integrations.encryption import decrypt_token

from .. import contract
from ..models import DeviceType, PbxExtension, PbxTenant


class CredentialsUnavailable(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_key(user_id, ext: PbxExtension) -> str:
    # The extension's id and updated_at: a rotated secret or a renumbered
    # extension invalidates the cached response.
    return f"telephony:webrtc:{user_id}:{ext.pk}:{int(ext.updated_at.timestamp())}"


def credentials_for(user, *, now: int | None = None) -> dict:
    tenant = PbxTenant.objects.filter(company_id=user.company_id).first()
    if tenant is None or tenant.status not in (PbxTenant.Status.ACTIVE, PbxTenant.Status.SUSPENDED):
        raise CredentialsUnavailable("not_provisioned", "The phone system is not ready for your company yet.")
    ext = PbxExtension.objects.filter(
        company_id=user.company_id, user=user, status=PbxExtension.Status.ACTIVE
    ).first()
    if ext is None:
        raise CredentialsUnavailable("no_extension", "You do not have a phone extension.", 403)
    if ext.device_type != DeviceType.WEBRTC:
        raise CredentialsUnavailable("not_webrtc", "Your extension is set up for a desk phone, not the browser.")

    mode = getattr(settings, "TELEPHONY_WEBRTC_AUTH", "static")
    now = int(time.time()) if now is None else int(now)
    key = _cache_key(user.pk, ext)
    cached = cache.get(key)
    if cached and cached.get("_refresh_ts", 0) > now:
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    if mode == "edge":
        data, ttl = _edge(tenant, ext, now)
    else:
        data, ttl = _static(tenant, ext, now)
    cache.set(key, {**data, "_refresh_ts": now + ttl - contract.REFRESH_MARGIN_SECONDS}, timeout=max(60, ttl))
    return data


def _edge(tenant: PbxTenant, ext: PbxExtension, now: int) -> tuple[dict, int]:
    sip_secret = getattr(settings, "PBX_SIP_EPHEMERAL_SECRET", "")
    turn_secret = getattr(settings, "PBX_TURN_SECRET", "")
    ws_uri = getattr(settings, "TELEPHONY_SIP_WS_URI", "")
    domain = getattr(settings, "TELEPHONY_SIP_DOMAIN", "")
    turn_urls = list(getattr(settings, "TELEPHONY_TURN_URLS", []) or [])
    if not (sip_secret and turn_secret and ws_uri.startswith("wss://") and domain and turn_urls):
        raise CredentialsUnavailable("not_configured", "The browser phone is not configured on this server.", 503)
    if sip_secret == turn_secret:
        raise CredentialsUnavailable("not_configured", "The browser phone is not configured on this server.", 503)
    ttl = int(getattr(settings, "TELEPHONY_CREDENTIAL_TTL_SECONDS", contract.DEFAULT_TTL_SECONDS))
    ttl = min(max(ttl, contract.MIN_TTL_SECONDS), contract.MAX_TTL_SECONDS)
    sip = contract.issue_extension_credential(sip_secret, tenant.realm_key, ext.number, ttl=ttl, now=now)
    turn = contract.issue_extension_credential(turn_secret, tenant.realm_key, ext.number, ttl=ttl, now=now)
    endpoint = contract.extension_endpoint(tenant.realm_key, ext.number)
    return {
        "extension": ext.number,
        "sip": {"ws_uri": ws_uri, "uri": f"sip:{endpoint}@{domain}", "username": sip.username,
                "password": sip.password, "realm": domain},
        "turn": {"urls": turn_urls, "username": turn.username, "credential": turn.password},
        "expires_at": _iso(sip.expires_at),
        "refresh_after": _iso(sip.expires_at - contract.REFRESH_MARGIN_SECONDS),
    }, ttl


def _static(tenant: PbxTenant, ext: PbxExtension, now: int) -> tuple[dict, int]:
    if not (settings.DEBUG or getattr(settings, "TELEPHONY_ALLOW_STATIC_WEBRTC", False)):
        raise CredentialsUnavailable("not_configured", "The browser phone is not configured on this server.", 503)
    secret = decrypt_token(ext.sip_secret_encrypted) if ext.sip_secret_encrypted else ""
    if not secret:
        raise CredentialsUnavailable("not_configured", "This extension has no SIP secret yet.", 503)
    ws_uri = getattr(settings, "TELEPHONY_SIP_WS_URI", "") or "ws://localhost:8088/ws"
    domain = getattr(settings, "TELEPHONY_SIP_DOMAIN", "") or "localhost"
    ttl = contract.DEFAULT_TTL_SECONDS
    endpoint = contract.extension_endpoint(tenant.realm_key, ext.number)
    return {
        "extension": ext.number,
        "sip": {"ws_uri": ws_uri, "uri": f"sip:{endpoint}@{domain}", "username": endpoint, "password": secret,
                "realm": domain},
        "turn": {"urls": list(getattr(settings, "TELEPHONY_TURN_URLS", []) or []), "username": "", "credential": ""},
        "expires_at": _iso(now + ttl),
        "refresh_after": _iso(now + ttl - contract.REFRESH_MARGIN_SECONDS),
        "mode": "static",
    }, ttl
