"""Tests for softphone_diagnose management command helpers."""

from integrations.management.commands.softphone_diagnose import _probe_wss_tls


def test_probe_wss_tls_invalid_scheme():
    ok, msg = _probe_wss_tls("https://example.com/path")
    assert ok is False
    assert "unsupported scheme" in msg


def test_probe_wss_tls_missing_host():
    ok, msg = _probe_wss_tls("wss:///ws")
    assert ok is False
    assert "missing host" in msg
