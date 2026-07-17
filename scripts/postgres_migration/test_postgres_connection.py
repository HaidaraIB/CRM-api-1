#!/usr/bin/env python3
"""
Quick smoke test after switching .env to PostgreSQL.

  ./venv/bin/python scripts/postgres_migration/test_postgres_connection.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crm_saas_api.settings")


def main() -> int:
    import django

    django.setup()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import connection

    engine = settings.DATABASES["default"]["ENGINE"]
    print(f"ENGINE = {engine}")
    if "postgresql" not in engine:
        print("WARNING: DB_ENGINE is not postgresql. Check .env")
        return 2

    with connection.cursor() as cur:
        cur.execute("SELECT version();")
        print(cur.fetchone()[0])

    User = get_user_model()
    print(f"users            = {User.objects.count()}")

    try:
        from crm.models import Client, Deal

        print(f"clients          = {Client.objects.count()}")
        print(f"deals            = {Deal.objects.count()}")
    except Exception as exc:
        print(f"(crm models) {exc}")

    try:
        from notifications.models import Notification

        print(f"notifications    = {Notification.objects.count()}")
    except Exception as exc:
        print(f"(notifications) {exc}")

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
