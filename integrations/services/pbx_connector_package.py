"""Build a downloadable ZIP package for the LAN PBX connector."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from django.conf import settings as django_settings

from integrations.models import PbxSettings

CONNECTOR_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pbx_connector"


def _api_base_url(request) -> str:
    if request:
        return request.build_absolute_uri("/").rstrip("/")
    return getattr(django_settings, "PUBLIC_API_BASE_URL", "https://your-api.example.com").rstrip("/")


def build_connector_config(settings: PbxSettings, request) -> dict:
    return {
        "api_base_url": _api_base_url(request),
        "connector_api_key": settings.connector_api_key,
        "pbx_host": settings.pbx_host or "192.168.1.100",
        "ami_port": settings.ami_port or 5038,
        "ami_username": settings.ami_username or "",
        "ami_password": "",
        "listen_host": "0.0.0.0",
        "listen_port": 8787,
        "poll_interval_sec": 3,
        "ssl_verify": True,
    }


def build_connector_zip(settings: PbxSettings, request) -> bytes:
    buffer = io.BytesIO()
    config = build_connector_config(settings, request)

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("connector.py", "requirements.txt"):
            path = CONNECTOR_DIR / name
            if path.is_file():
                zf.writestr(name, path.read_text(encoding="utf-8"))

        zf.writestr("config.json", json.dumps(config, indent=2) + "\n")

        zf.writestr(
            "run.bat",
            "@echo off\r\n"
            "cd /d \"%~dp0\"\r\n"
            "python connector.py\r\n",
        )
        zf.writestr(
            "run.sh",
            "#!/bin/sh\n"
            "cd \"$(dirname \"$0\")\"\n"
            "python3 connector.py\n",
        )
        zf.writestr(
            "INSTALL.txt",
            "LOOP CRM — PBX LAN Connector\n"
            "============================\n\n"
            "1. Install Python 3.10+ on a PC on the same network as your ZYCOO PBX.\n"
            "2. pip install -r requirements.txt\n"
            "3. Edit config.json — set ami_password (not included for security).\n"
            "   macOS SSL error? Run: /Applications/Python 3.*/Install Certificates.command\n"
            "   Or ensure certifi is installed (included in requirements.txt).\n"
            "4. Run: python connector.py  (or run.bat / run.sh)\n"
            "5. In ZYCOO: Addons → API → Push Event → http://<this-pc-ip>:8787\n"
            "6. In CRM: Integrations → PBX — confirm Connector status is Online.\n\n"
            f"API base URL: {config['api_base_url']}\n"
            f"PBX host: {config['pbx_host']}\n",
        )

    return buffer.getvalue()
