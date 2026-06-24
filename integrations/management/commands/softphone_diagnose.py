"""
Softphone readiness diagnostics for mobile SIP registration issues.
Run: .venv\\Scripts\\python.exe manage.py softphone_diagnose
     .venv\\Scripts\\python.exe manage.py softphone_diagnose --company-id=1
     .venv\\Scripts\\python.exe manage.py softphone_diagnose --username=hassan
"""
from __future__ import annotations

import socket
import ssl
from urllib.parse import urlparse

from django.core.management.base import BaseCommand

from integrations.models import PbxSettings, UserPbxExtension
from integrations.services.softphone_config import build_softphone_config, user_softphone_ready


def _probe_wss_tls(wss_uri: str, timeout: float = 8.0) -> tuple[bool, str]:
    """TCP + TLS handshake to WSS host:port (does not complete WebSocket upgrade)."""
    parsed = urlparse(wss_uri.strip())
    if parsed.scheme not in ("wss", "ws"):
        return False, f"unsupported scheme: {parsed.scheme}"
    host = parsed.hostname
    if not host:
        return False, "missing host in wss_uri"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    use_tls = parsed.scheme == "wss"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if use_tls:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                    cert = tls_sock.getpeercert()
                    subject = dict(x[0] for x in cert.get("subject", ()))
                    cn = subject.get("commonName", "")
                    return True, f"TLS OK (CN={cn or 'unknown'})"
            return True, "TCP OK (plain ws)"
    except ssl.SSLCertVerificationError as exc:
        return False, f"TLS cert verification failed: {exc}"
    except TimeoutError:
        return False, f"timeout connecting to {host}:{port}"
    except OSError as exc:
        return False, f"connection failed: {exc}"


class Command(BaseCommand):
    help = "Diagnose softphone config, WSS reachability, and PBX extension readiness."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, default=None)
        parser.add_argument("--username", type=str, default=None)

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        username = (options.get("username") or "").strip()

        qs = PbxSettings.objects.filter(is_enabled=True, softphone_enabled=True)
        if company_id:
            qs = qs.filter(company_id=company_id)
        settings_rows = list(qs.select_related("company"))
        if not settings_rows:
            self.stdout.write(self.style.ERROR("No company has PBX + softphone enabled."))
            return

        for settings in settings_rows:
            self._diagnose_company(settings, username=username)

    def _diagnose_company(self, settings: PbxSettings, *, username: str) -> None:
        company = settings.company
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nCompany: {company.name} (id={company.id})"))
        domain = (settings.sip_domain or settings.pbx_host or "").strip()
        wss_uri = (settings.wss_uri or "").strip() or (
            f"wss://{domain}:8089/ws" if domain else ""
        )
        self.stdout.write(f"  sip_domain: {domain or '(empty)'}")
        self.stdout.write(f"  wss_uri: {wss_uri or '(empty)'}")
        self.stdout.write(f"  stun_server: {settings.stun_server or '(empty)'}")
        self.stdout.write(f"  turn_server: {'set' if settings.turn_server else '(empty)'}")

        if not domain:
            self.stdout.write(self.style.ERROR("  FAIL: sip_domain is empty — set in Integrations → PBX"))
        if not wss_uri:
            self.stdout.write(self.style.ERROR("  FAIL: wss_uri is empty"))

        if wss_uri:
            ok, detail = _probe_wss_tls(wss_uri)
            if ok:
                self.stdout.write(self.style.SUCCESS(f"  WSS probe: {detail}"))
            else:
                self.stdout.write(self.style.ERROR(f"  WSS probe FAILED: {detail}"))
                self.stdout.write(
                    "  Hint: Sync certificate on ZYCOO (Addons → Remote Access) "
                    "and ensure port 8089 is open from the internet."
                )

        ext_qs = UserPbxExtension.objects.filter(company=company).select_related("user")
        if username:
            ext_qs = ext_qs.filter(user__username__iexact=username)
        mappings = list(ext_qs)
        if not mappings:
            self.stdout.write(self.style.WARNING("  No extension mappings found for filter."))
            return

        for mapping in mappings:
            user = mapping.user
            ready = user_softphone_ready(settings, mapping)
            self.stdout.write(f"\n  User: {user.username} (id={user.id}) ext={mapping.extension}")
            self.stdout.write(f"    softphone_enabled: {mapping.softphone_enabled}")
            self.stdout.write(f"    sip_password stored: {bool(mapping.sip_password)}")
            self.stdout.write(f"    user_softphone_ready: {ready}")
            if not ready:
                self.stdout.write(self.style.ERROR("    FAIL: not ready for GET /softphone/config/"))
                continue
            cfg = build_softphone_config(settings, mapping, platform="ios")
            pwd_len = len(cfg.get("sip_password") or "")
            self.stdout.write(f"    config extension: {cfg.get('extension')}")
            self.stdout.write(f"    config wss_uri: {cfg.get('wss_uri')}")
            self.stdout.write(f"    config sip_password length: {pwd_len}")
            if pwd_len == 0:
                self.stdout.write(
                    self.style.ERROR("    FAIL: decrypted sip_password is empty — re-save password in CRM")
                )
            else:
                self.stdout.write(self.style.SUCCESS("    OK: API would return valid iOS softphone config"))

        self.stdout.write("\n  CooVox manual checks (cannot verify from CRM):")
        self.stdout.write("    - Extension WebRTC enabled")
        self.stdout.write("    - SIP password matches CRM exactly")
        self.stdout.write("    - CooCall logged out / no duplicate registration on extension")
        self.stdout.write("    - Port 8089 WSS reachable from cellular (not only office LAN)")
