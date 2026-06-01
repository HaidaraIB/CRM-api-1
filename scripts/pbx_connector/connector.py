#!/usr/bin/env python3
"""
LOOP CRM PBX Connector — bridges ZYCOO CooVox (LAN) to cloud CRM API.

Run on a machine on the same network as the PBX.
"""
from __future__ import annotations

import json
import logging
import socket
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


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing {CONFIG_PATH} — copy config.example.json")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def api_request(cfg: dict, method: str, path: str, body: dict | None = None) -> dict:
    url = cfg["api_base_url"].rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {cfg['connector_api_key']}",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            if isinstance(raw, dict) and "data" in raw:
                return raw["data"] if raw["data"] is not None else raw
            return raw
    except HTTPError as e:
        logger.error("API %s %s failed: %s", method, path, e.read().decode())
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
    url = cfg["api_base_url"].rstrip("/") + "/api/integrations/pbx/connector/events/"
    headers = {
        "Authorization": f"Bearer {cfg['connector_api_key']}",
        "Content-Type": content_type or "application/json",
    }
    req = urlrequest.Request(url, data=raw_body, headers=headers, method="POST")
    with urlrequest.urlopen(req, timeout=30) as resp:
        logger.info("Forwarded event: %s", resp.read().decode()[:200])


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


def main() -> None:
    cfg = load_config()
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
    server = HTTPServer((host, port), make_webhook_handler(cfg))
    logger.info("Connector listening on %s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
