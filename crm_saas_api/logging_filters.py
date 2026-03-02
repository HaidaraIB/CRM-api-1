"""
Custom logging filters to reduce noise and duplicates in log files.
"""
import logging
import time
from collections import defaultdict


class ImportantOnlyFilter(logging.Filter):
    """
    Allow only important records: ERROR, CRITICAL, and security-related WARNINGs.
    Used for django_important.log to keep only actionable entries.
    """
    def filter(self, record):
        if record.levelno >= logging.ERROR:
            return True
        if record.levelno == logging.WARNING:
            msg = (record.getMessage() or "").lower()
            # Keep security/auth warnings
            if "unauthorized" in msg or "forbidden" in msg or "invalid" in msg and "key" in msg:
                return True
        return False


class SkipNoiseFilter(logging.Filter):
    """
    Skip noisy records that fill the main log:
    - HTTP request logs from django.server (every GET/POST 200)
    - Repeated identical messages within a time window (deduplication)
    """
    # Loggers that only go to console, not to file (handled via logger config).
    # This filter is extra safety: skip basehttp/django.server if they reach file handler.
    NOISY_LOGGERS = (
        "django.server",
        "django.core.servers.basehttp",
    )
    # Message prefixes that are too verbose for file (e.g. every successful request)
    NOISY_PREFIXES = (
        '"GET ',
        '"POST ',
        '"PUT ',
        '"PATCH ',
        '"DELETE ',
        '"OPTIONS ',
        "Watching for file changes",
        "autoreload ",
        "Firebase Admin SDK initialized",
    )
    # Deduplication: same message key within this many seconds = skip
    DEDUP_SECONDS = 300
    _last_seen = defaultdict(float)

    def filter(self, record):
        name = getattr(record, "name", "")
        if name in self.NOISY_LOGGERS:
            return False
        msg = record.getMessage()
        for prefix in self.NOISY_PREFIXES:
            if prefix in msg or msg.startswith(prefix):
                return False
        # Deduplicate: same message (first 100 chars) within DEDUP_SECONDS
        key = (record.name, msg[:100] if len(msg) > 100 else msg)
        now = time.time()
        if now - self._last_seen[key] < self.DEDUP_SECONDS:
            return False
        self._last_seen[key] = now
        return True
