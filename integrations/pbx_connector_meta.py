"""LAN PBX connector package metadata (shared with CRM UI and download ZIP)."""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = (
    Path(__file__).resolve().parents[1] / "scripts" / "pbx_connector" / "VERSION"
)


def get_pbx_connector_version() -> str:
    try:
        text = _VERSION_FILE.read_text(encoding="utf-8").strip()
        return text or "1.1.0"
    except OSError:
        return "1.1.0"
