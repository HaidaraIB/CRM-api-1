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
import sys
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
_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def _read_connector_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "1.1.0"
    except OSError:
        return "1.1.0"


CONNECTOR_VERSION = _read_connector_version()

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


def _ami_response_ok(response: str) -> bool:
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("response:"):
            return "success" in stripped.lower()
    return "Success" in response and "Response: Error" not in response


def _ami_response_message(response: str) -> str:
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith("Message:"):
            return stripped.split(":", 1)[1].strip()
    return response.strip()[:500]


class AmiClient:
    """Minimal Asterisk AMI client for Originate."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_tls: bool = False,
        channel_driver: str = "PJSIP",
        originate_context: str = "from-internal",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.channel_driver = (channel_driver or "PJSIP").strip() or "PJSIP"
        self.originate_context = (originate_context or "from-internal").strip() or "from-internal"
        self._sock: socket.socket | ssl.SSLSocket | None = None

    def _open_tcp(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=10)
        if self.use_tls:
            context = ssl.create_default_context()
            assert self._sock is not None
            self._sock = context.wrap_socket(self._sock, server_hostname=self.host)
            logger.info("AMI TLS enabled for %s:%s", self.host, self.port)

    def test_connection(self) -> None:
        """Raw TCP (+ optional TLS) and AMI banner only; no login."""
        try:
            self._open_tcp()
            logger.info(
                "TCP connection established to %s:%s",
                self.host,
                self.port,
            )
            banner = self._read_banner()
            logger.info("AMI banner received:\n%s", banner)
        finally:
            self.close()

    def connect(self) -> None:
        self._open_tcp()
        logger.info("AMI connected to %s:%s", self.host, self.port)
        banner = self._read_banner()
        logger.info("AMI banner:\n%s", banner)
        self._send_action(
            {
                "Action": "Login",
                "Username": self.username,
                "Secret": self.password,
            }
        )
        resp = self._read_block("AMI login response")
        logger.info("AMI login response:\n%s", resp)
        if "Success" not in resp:
            logger.error("AMI authentication failed: %s", resp)
            raise RuntimeError(f"AMI login failed: {resp}")

    def close(self) -> None:
        if self._sock:
            try:
                self._send_action({"Action": "Logoff"})
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send_action(self, fields: dict[str, str]) -> None:
        assert self._sock
        lines = "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
        self._sock.sendall(lines.encode("utf-8"))

    def _read_banner(self) -> str:
        """AMI greeting is one line (e.g. Asterisk Call Manager/7.0.1), not a double-CRLF block."""
        assert self._sock
        chunks: list[str] = []
        try:
            while True:
                data = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    break
                chunks.append(data)
                if "\n" in "".join(chunks):
                    break
        except socket.timeout as exc:
            partial = "".join(chunks)
            raise TimeoutError(
                f"Timed out waiting for AMI banner from {self.host}:{self.port}."
                + (f" Partial data: {partial[:200]!r}" if partial else "")
            ) from exc
        return "".join(chunks)

    def _read_block(self, purpose: str = "AMI data") -> str:
        assert self._sock
        chunks: list[str] = []
        try:
            while True:
                data = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    break
                chunks.append(data)
                if "\r\n\r\n" in "".join(chunks):
                    break
        except socket.timeout as exc:
            partial = "".join(chunks)
            hint = (
                f"Timed out waiting for {purpose} from {self.host}:{self.port}. "
                "TCP connected but the server sent no AMI banner. "
            )
            if not self.use_tls and self.port == 5038:
                hint += "Try ami_port 5039 with ami_use_tls true. "
            hint += "Also confirm AMI/Manager is enabled on the PBX."
            if partial:
                hint += f" Partial data received: {partial[:200]!r}"
            raise TimeoutError(hint) from exc
        return "".join(chunks)

    def originate(self, extension: str, phone_number: str, caller_id: str = "") -> str:
        self.connect()
        try:
            channel = f"{self.channel_driver}/{extension}"
            logger.info(
                "Originate request ext=%s phone=%s channel=%s",
                extension,
                phone_number,
                channel,
            )
            self._send_action(
                {
                    "Action": "Originate",
                    "Channel": channel,
                    "Context": self.originate_context,
                    "Exten": phone_number,
                    "Priority": "1",
                    "CallerID": caller_id or phone_number,
                    "Async": "true",
                    "Timeout": "30000",
                }
            )
            response = self._read_block("AMI Originate response")
            logger.info("Originate response:\n%s", response)
            if not _ami_response_ok(response):
                message = _ami_response_message(response)
                logger.error("AMI Originate failed: %s", message)
                raise RuntimeError(f"AMI Originate failed: {message}")
            return response
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


def upload_recording_file(cfg: dict, record_id: int, file_path: Path) -> None:
    """Upload a PBX WAV from local filesystem to CRM storage."""
    url = build_api_url(cfg, f"/integrations/pbx/connector/recordings/{record_id}/upload/")
    filename = file_path.name
    file_bytes = file_path.read_bytes()
    boundary = f"----LoopPbx{int(time.time() * 1000)}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode(),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    headers = _request_headers(
        cfg,
        {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    req = urlrequest.Request(url, data=body, headers=headers, method="POST")
    try:
        with _urlopen(cfg, req, timeout=120) as resp:
            logger.info(
                "Uploaded recording record_id=%s (%s bytes): %s",
                record_id,
                len(file_bytes),
                resp.read().decode()[:200],
            )
    except HTTPError as e:
        _log_http_error("POST", url, e)
        raise


def process_recording_job(cfg: dict, job: dict[str, Any]) -> None:
    record_id = job.get("record_id")
    path_str = (job.get("file") or "").strip()
    if not record_id or not path_str:
        return
    file_path = Path(path_str)
    if not file_path.is_file():
        return
    try:
        upload_recording_file(cfg, int(record_id), file_path)
    except Exception:
        logger.exception("Recording upload failed record_id=%s", record_id)


def poll_recordings(cfg: dict) -> None:
    interval = float(cfg.get("recording_poll_interval_sec", 5))
    while True:
        try:
            resp = api_request(cfg, "GET", "/integrations/pbx/connector/recording-jobs/")
            data = resp.get("data") or resp
            for job in data.get("jobs") or []:
                process_recording_job(cfg, job)
        except Exception:
            logger.exception("Recording poll error")
        time.sleep(interval)


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
                logger.exception("Connector POST failed")
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


def _make_ami_client(cfg: dict) -> AmiClient:
    return AmiClient(
        cfg["pbx_host"],
        int(cfg.get("ami_port", 5038)),
        cfg["ami_username"],
        cfg["ami_password"],
        use_tls=bool(cfg.get("ami_use_tls", False)),
        channel_driver=str(cfg.get("channel_driver", "PJSIP")),
        originate_context=str(cfg.get("originate_context", "from-internal")),
    )


def run_test_ami(cfg: dict) -> None:
    """Connect, log banner + login response, then exit (no CRM polling)."""
    ami = _make_ami_client(cfg)
    use_tls = bool(cfg.get("ami_use_tls", False))
    logger.info(
        "AMI test: host=%s port=%s tls=%s driver=%s",
        ami.host,
        ami.port,
        use_tls,
        ami.channel_driver,
    )
    try:
        ami.connect()
        logger.info("AMI test: authentication succeeded")
    except Exception as exc:
        logger.error("AMI test failed: %s", exc)
        raise SystemExit(1) from exc
    finally:
        ami.close()


def main() -> None:
    import sys

    cfg = load_config()
    if "--test-ami" in sys.argv:
        logger.info("LOOP PBX Connector v%s — AMI test mode", CONNECTOR_VERSION)
        run_test_ami(cfg)
        return

    logger.info("LOOP PBX Connector v%s", CONNECTOR_VERSION)
    logger.info("CRM API base: %s", cfg.get("api_base_url"))
    logger.info(
        "Heartbeat URL: %s",
        build_api_url(cfg, "/integrations/pbx/connector/heartbeat/"),
    )
    logger.info("Auth: X-Connector-Key (key length %s)", len(cfg["connector_api_key"]))

    try:
        api_request(cfg, "POST", "/api/integrations/pbx/connector/heartbeat/", {})
        logger.info("CRM heartbeat OK — connector authenticated")
    except Exception:
        logger.error(
            "CRM heartbeat failed. If you see JWT/Bearer errors, replace connector.py "
            "with v%s+ from CRM download (old builds used Authorization Bearer).",
            CONNECTOR_VERSION,
        )
        raise SystemExit(1) from None

    ami = _make_ami_client(cfg)
    logger.info(
        "AMI config: port=%s tls=%s channel_driver=%s",
        ami.port,
        ami.use_tls,
        ami.channel_driver,
    )
    try:
        ami.test_connection()
    except Exception:
        logger.exception(
            "AMI startup connection test failed (TCP/banner). "
            "Check transport, TLS (ami_use_tls), and port."
        )

    poll_thread = threading.Thread(target=poll_commands, args=(cfg, ami), daemon=True)
    poll_thread.start()
    recording_thread = threading.Thread(target=poll_recordings, args=(cfg,), daemon=True)
    recording_thread.start()

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
    logger.info("Connector listening on %s:%s (Ctrl+C to stop)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Connector stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Connector stopped.")
        sys.exit(0)
