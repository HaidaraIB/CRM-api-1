"""Shared config helpers for SQLite → PostgreSQL migration."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
SQLITE_PATH = ROOT / "db.sqlite3"
BACKUP_DIR = ROOT / "media" / "backups" / "postgres_migration"
FIXTURE_DIR = BACKUP_DIR / "fixtures"

# Ephemeral / recreate-on-login data. Safe to skip (users re-auth after cutover).
# contenttypes + auth.Permission are recreated by `migrate`; natural FKs still resolve.
DEFAULT_EXCLUDE = [
    "contenttypes",
    "auth.permission",
    "sessions.session",
    "admin.logentry",
    "token_blacklist.outstandingtoken",
    "token_blacklist.blacklistedtoken",
    "django_q.task",
    "django_q.success",
    "django_q.failure",
    "django_q.ormq",
]

# Keep django_q.Schedule so cron schedules survive.

# Business apps we always verify after load (subset of high-value tables).
VERIFY_LABELS = [
    "accounts.User",
    "companies.Company",
    "crm.Client",
    "crm.Deal",
    "crm.Task",
    "crm.ClientTask",
    "crm.ClientCall",
    "crm.ClientEvent",
    "notifications.Notification",
    "subscriptions.Subscription",
    "subscriptions.Payment",
    "subscriptions.Invoice",
    "tenant_chat.ChatMessage",
    "settings.LeadStatus",
    "settings.Channel",
    "integrations.IntegrationAccount",
    "django_q.Schedule",
]


def load_dotenv_file(path: Path = ENV_PATH) -> dict[str, str]:
    """Parse a .env file without relying on process env mutation."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        values[key] = value
    return values


def postgres_settings_from_env(
    env: dict[str, str] | None = None,
    *,
    name: str | None = None,
    user: str | None = None,
    password: str | None = None,
    host: str | None = None,
    port: str | None = None,
) -> dict:
    env = env or {}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name or env.get("DB_NAME") or os.getenv("DB_NAME", "crm_db"),
        "USER": user or env.get("DB_USER") or os.getenv("DB_USER", "crm_user"),
        "PASSWORD": password
        or env.get("DB_PASSWORD")
        or os.getenv("DB_PASSWORD", ""),
        "HOST": host or env.get("DB_HOST") or os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": port or env.get("DB_PORT") or os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 0,
    }


def sqlite_settings(sqlite_path: Path = SQLITE_PATH) -> dict:
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(sqlite_path),
        "OPTIONS": {"timeout": 30},
    }


def update_env_for_postgres(
    env_path: Path,
    *,
    name: str,
    user: str,
    password: str,
    host: str,
    port: str,
) -> None:
    """Set DB_* keys in .env for PostgreSQL (creates file if missing)."""
    replacements = {
        "DB_ENGINE": "postgresql",
        "DB_NAME": name,
        "DB_USER": user,
        "DB_PASSWORD": password,
        "DB_HOST": host,
        "DB_PORT": str(port),
    }
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
    else:
        text = ""

    for key, value in replacements.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        line = f'{key}="{value}"'
        if pattern.search(text):
            text = pattern.sub(line, text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += f"{line}\n"

    env_path.write_text(text, encoding="utf-8")
