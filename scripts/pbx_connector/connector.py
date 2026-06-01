#!/usr/bin/env python3
"""
LOOP CRM PBX Connector — bridges ZYCOO CooVox (LAN) to cloud CRM API.

Run on a machine on the same network as the PBX.
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pbx_connector")

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

_SSL_HELP = (
    "SSL certificate verification failed. On macOS with python.org Python, run:\n"
    "  pip install -r requirements.txt\n"
    "  /Applications/Python 3.*/Install Certificates.command\n"
    "Or set \"ssl_verify\": false in config.json only for local testing (not recommended)."
)


def _ssl_context(cfg: dict) -> ssl.SSLContext:
    if cfg.get("ssl_verify") is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("ssl_verify is false — HTTPS certificates are not verified")
        return ctx
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _urlopen(cfg: dict, req: urlrequest.Request, timeout: int = 30):
    try:
        return urlrequest.urlopen(req, timeout=timeout, context=_ssl_context(cfg))
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError) or (
            reason is not None and "CERTIFICATE_VERIFY_FAILED" in str(reason)
        ):
            logger.error(_SSL_HELP)
        raise


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing {CONFIG_PATH} — copy config.example.json")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if isinstance(cfg.get("connector_api_key"), str):
        cfg["connector_api_key"] = cfg["connector_api_key"].strip()
    for field in ("api_base_url", "x_api_key", "app_api_key", "ami_username", "ami_password"):
        if isinstance(cfg.get(field), str):
            cfg[field] = cfg[field].strip()
    if not cfg.get("connector_api_key"):
        raise ValueError("connector_api_key is empty in config.json")
    return cfg


def build_api_url(cfg: dict, path: str) -> str:
    """
    Build full CRM API URL. Supports api_base_url as:
      https://api.example.com
      https://api.example.com/api/v1
    path may be legacy '/api/integrations/...' or '/integrations/...'
    """
    base = (cfg.get("api_base_url") or "").rstrip("/")
    if path.startswith("/api/integrations/"):
        path = path[len("/api") :]
    if not path.startswith("/"):
        path = "/" + path
    if base.endswith("/api/v1"):
        return base + path
    if base.endswith("/api"):
        return base + path
    return base + "/api" + path


def _request_headers(cfg: dict, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Use X-Connector-Key (not Authorization Bearer) — JWT auth treats Bearer as user tokens."""
    headers = {
        "X-Connector-Key": cfg["connector_api_key"],
        "User-Agent": "LOOP-PBX-Connector/1.0",
        "Accept": "application/json",
    }
    app_key = (cfg.get("x_api_key") or cfg.get("app_api_key") or "").strip()
    if app_key:
        headers["X-API-Key"] = app_key
    if extra:
        headers.update(extra)
    return headers


def _log_http_error(method: str, url: str, err: HTTPError) -> None:
    body = ""
    try:
        body = err.read().decode("utf-8", errors="replace")[:800]
    except Exception:
        pass
    logger.error("API %s %s failed: HTTP %s — %s", method, url, err.code, body or err.reason)
    if err.code == 403 and ("1010" in body or "cloudflare" in body.lower()):
        logger.error(
            "This often means Cloudflare/WAF blocked the request. "
            "Ask your host to allow POST from your office IP to /api/*/integrations/pbx/connector/* "
            "or add the CRM web app API key as x_api_key in config.json."
        )
    elif err.code == 401:
        if "missing_api_key" in body or "invalid_api_key" in body:
            logger.error(
                "CRM requires X-API-Key (app API key). Add to config.json:\n"
                '  "x_api_key": "<same as CRM web app API key>"'
            )
        else:
            logger.error(
                "Invalid connector API key. In CRM: Integrations → PBX → Connector API key.\n"
                "Copy it again (if you clicked Rotate key, the old key no longer works).\n"
                "Use header X-Connector-Key (not Authorization Bearer)."
            )


def api_request(cfg: dict, method: str, path: str, body: dict | None = None) -> dict:
    url = build_api_url(cfg, path)
    headers = _request_headers(cfg)
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen(cfg, req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            if isinstance(raw, dict) and "data" in raw:
                return raw["data"] if raw["data"] is not None else raw
            return raw
    except HTTPError as e:
        _log_http_error(method, url, e)
        raise


class AmiClient:
    """Minimal Asterisk AMI client for Originate."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=10)
        self._read_block()
        self._send_action(
            {
                "Action": "Login",
                "Username": self.username,
                "Secret": self.password,
            }
        )
        resp = self._read_block()
        if "Success" not in resp:
            raise RuntimeError(f"AMI login failed: {resp}")

    def close(self) -> None:
        if self._sock:
            try:
                self._send_action({"Action": "Logoff"})
            except Exception:
                pass
            self._sock.close()
            self._sock = None

    def _send_action(self, fields: dict[str, str]) -> None:
        assert self._sock
        lines = "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
        self._sock.sendall(lines.encode("utf-8"))

    def _read_block(self) -> str:
        assert self._sock
        chunks: list[str] = []
        while True:
            data = self._sock.recv(4096).decode("utf-8", errors="replace")
            if not data:
                break
            chunks.append(data)
            if "\r\n\r\n" in "".join(chunks):
                break
        return "".join(chunks)

    def originate(self, extension: str, phone_number: str, caller_id: str = "") -> str:
        self.connect()
        try:
            channel = f"SIP/{extension}"
            self._send_action(
                {
                    "Action": "Originate",
                    "Channel": channel,
                    "Context": "from-internal",
                    "Exten": phone_number,
                    "Priority": "1",
                    "CallerID": caller_id or phone_number,
                    "Async": "true",
                    "Timeout": "30000",
                }
            )
            return self._read_block()
        finally:
            self.close()


def forward_event(cfg: dict, raw_body: bytes, content_type: str) -> None:
    url = build_api_url(cfg, "/integrations/pbx/connector/events/")
    headers = _request_headers(
        cfg,
        {"Content-Type": content_type or "application/json"},
    )
    req = urlrequest.Request(url, data=raw_body, headers=headers, method="POST")
    try:
        with _urlopen(cfg, req, timeout=30) as resp:
            logger.info("Forwarded event: %s", resp.read().decode()[:200])
    except HTTPError as e:
        _log_http_error("POST", url, e)
        raise


def poll_commands(cfg: dict, ami: AmiClient) -> None:
    while True:
        try:
            api_request(cfg, "POST", "/api/integrations/pbx/connector/heartbeat/", {})
            resp = api_request(cfg, "GET", "/api/integrations/pbx/connector/commands/", None)
            data = resp.get("data") or resp
            commands = data.get("commands") or []
            for cmd in commands:
                cmd_id = cmd["id"]
                ext = cmd["extension"]
                phone = cmd["phone_number"]
                try:
                    result = ami.originate(ext, phone)
                    api_request(
                        cfg,
                        "POST",
                        f"/api/integrations/pbx/connector/commands/{cmd_id}/ack/",
                        {"success": True, "message": result[:500]},
                    )
                    logger.info("Dialed %s via ext %s", phone, ext)
                except Exception as exc:
                    api_request(
                        cfg,
                        "POST",
                        f"/api/integrations/pbx/connector/commands/{cmd_id}/ack/",
                        {"success": False, "message": str(exc)},
                    )
                    logger.exception("Dial failed")
        except Exception:
            logger.exception("Command poll error")
        time.sleep(float(cfg.get("poll_interval_sec", 3)))


def make_webhook_handler(cfg: dict):
    class WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logger.debug(format, *args)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            try:
                forward_event(cfg, body, content_type)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as exc:
                logger.exception("Webhook forward failed")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode())

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"LOOP PBX Connector")

    return WebhookHandler


class ReuseAddrHTTPServer(HTTPServer):
    """Allow restart shortly after stopping (avoids 'Address already in use' on macOS)."""

    allow_reuse_address = True


def main() -> None:
    cfg = load_config()
    logger.info("CRM API base: %s", cfg.get("api_base_url"))
    logger.info(
        "Heartbeat URL: %s",
        build_api_url(cfg, "/integrations/pbx/connector/heartbeat/"),
    )
    ami = AmiClient(
        cfg["pbx_host"],
        int(cfg.get("ami_port", 5038)),
        cfg["ami_username"],
        cfg["ami_password"],
    )

    poll_thread = threading.Thread(target=poll_commands, args=(cfg, ami), daemon=True)
    poll_thread.start()

    host = cfg.get("listen_host", "0.0.0.0")
    port = int(cfg.get("listen_port", 8787))
    try:
        server = ReuseAddrHTTPServer((host, port), make_webhook_handler(cfg))
    except OSError as exc:
        if getattr(exc, "errno", None) in (48, 98) or "already in use" in str(exc).lower():
            logger.error(
                "Port %s is already in use. Another connector (or app) is running.\n"
                "  macOS: lsof -i :%s   then   kill <PID>\n"
                "  Or change listen_port in config.json (and update ZYCOO Push Event URL).",
                port,
                port,
            )
        raise
    logger.info("Connector listening on %s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
