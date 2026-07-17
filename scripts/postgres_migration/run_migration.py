#!/usr/bin/env python3
"""
One-time migration: CRM-api-1 SQLite -> PostgreSQL (no data loss).

Same idea as subscription_crm_bot's migrate script: dry-run first, then copy,
then verify. Uses Django dumpdata/loaddata with dual DB aliases so .env is
not flipped until you opt in with --update-env.

Run from the API repo root with the project venv:

  # Windows
  .\\.venv\\Scripts\\python.exe scripts/postgres_migration/run_migration.py --dry-run
  .\\.venv\\Scripts\\python.exe scripts/postgres_migration/run_migration.py --all

  # Linux VPS
  ./venv/bin/python scripts/postgres_migration/run_migration.py --dry-run
  ./venv/bin/python scripts/postgres_migration/run_migration.py --all --update-env

Steps (also runnable individually):
  backup | check | schema | dump | load | verify | update-env | all
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    BACKUP_DIR,
    DEFAULT_EXCLUDE,
    ENV_PATH,
    FIXTURE_DIR,
    ROOT as PROJECT_ROOT,
    SQLITE_PATH,
    VERIFY_LABELS,
    load_dotenv_file,
    postgres_settings_from_env,
    sqlite_settings,
    update_env_for_postgres,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def setup_django(pg: dict, sqlite_path: Path) -> None:
    """Boot Django with dual aliases: `sqlite` (source) and `postgres` (target)."""
    # Force sqlite while importing settings so default is valid before we patch.
    os.environ["DB_ENGINE"] = "sqlite3"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crm_saas_api.settings")

    import django
    from django.conf import settings
    from django.db import connections

    django.setup()

    # Deep copies so default/sqlite/postgres never share the same dict object.
    settings.DATABASES["sqlite"] = copy.deepcopy(sqlite_settings(sqlite_path))
    settings.DATABASES["postgres"] = copy.deepcopy(pg)
    settings.DATABASES["default"] = copy.deepcopy(settings.DATABASES["sqlite"])

    _reload_db_connections()


def _reload_db_connections() -> None:
    """Close and discard DB wrappers so aliases are rebuilt from settings.DATABASES.

    Django's connections.close_all() only closes sockets; it keeps the old
    DatabaseWrapper objects (with the old ENGINE) in the thread-local cache.
    """
    from django.conf import settings as dj_settings
    from django.db import connections

    aliases = list(dj_settings.DATABASES.keys())
    for alias in aliases:
        try:
            if hasattr(connections._connections, alias):
                getattr(connections._connections, alias).close()
                delattr(connections._connections, alias)
        except Exception:
            pass

    connections.__dict__.pop("settings", None)
    if hasattr(connections, "_settings"):
        connections._settings = None


def _assert_alias_is_postgres(alias: str = "postgres") -> None:
    from django.db import connections

    engine = connections[alias].settings_dict.get("ENGINE", "")
    vendor = connections[alias].vendor
    if "postgresql" not in engine and vendor != "postgresql":
        raise RuntimeError(
            f"Expected alias '{alias}' to be PostgreSQL, got ENGINE={engine!r} vendor={vendor!r}"
        )


def _postgres_column_exists(table: str, column: str) -> bool:
    from django.db import connections

    with connections["postgres"].cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            [table, column],
        )
        return cur.fetchone() is not None


def _count_unapplied_migrations(alias: str) -> int:
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connections[alias])
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return len(plan)


def step_backup(sqlite_path: Path, *, dry_run: bool) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    dest = BACKUP_DIR / f"db.sqlite3.pre-pg-{_stamp()}"
    size_mb = sqlite_path.stat().st_size / (1024 * 1024)
    print(f"[backup] source={sqlite_path} ({size_mb:.2f} MB)")
    print(f"[backup] dest={dest}")
    if dry_run:
        print("[backup] dry-run: skipped copy")
        return dest
    shutil.copy2(sqlite_path, dest)
    # Also keep a stable "latest" pointer for convenience.
    latest = BACKUP_DIR / "db.sqlite3.pre-pg-latest"
    shutil.copy2(sqlite_path, latest)
    print("[backup] ok")
    return dest


def step_check(pg: dict, sqlite_path: Path) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    print(
        f"[check] postgres {pg['USER']}@{pg['HOST']}:{pg['PORT']}/{pg['NAME']}"
    )
    try:
        conn = psycopg2.connect(
            dbname=pg["NAME"],
            user=pg["USER"],
            password=pg["PASSWORD"],
            host=pg["HOST"],
            port=pg["PORT"],
            connect_timeout=10,
        )
    except psycopg2.OperationalError as exc:
        # Try connecting to maintenance DB to see if server is up / DB missing.
        try:
            admin = psycopg2.connect(
                dbname="postgres",
                user=pg["USER"],
                password=pg["PASSWORD"],
                host=pg["HOST"],
                port=pg["PORT"],
                connect_timeout=10,
            )
            admin.close()
            raise SystemExit(
                f"[check] PostgreSQL is reachable but database "
                f"'{pg['NAME']}' is missing or not accessible.\n"
                f"Create it first (see vps_setup_postgres.sh), then retry.\n"
                f"Original error: {exc}"
            ) from exc
        except psycopg2.OperationalError as admin_exc:
            raise SystemExit(
                f"[check] Cannot connect to PostgreSQL.\n"
                f"  host={pg['HOST']} port={pg['PORT']} user={pg['USER']}\n"
                f"  error={admin_exc}\n"
                f"Run scripts/postgres_migration/vps_setup_postgres.sh on the VPS first."
            ) from admin_exc

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        version = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public';"
        )
        table_count = cur.fetchone()[0]
    conn.close()
    print(f"[check] ok - {version.split(',')[0]}")
    print(f"[check] public tables currently: {table_count}")
    print(f"[check] sqlite ok - {sqlite_path}")


def step_reset_schema(pg: dict, *, dry_run: bool) -> None:
    """Drop and recreate public schema so migrate can run cleanly on Postgres."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    print(
        f"[reset-schema] DROP SCHEMA public CASCADE on {pg['HOST']}/{pg['NAME']}"
    )
    if dry_run:
        print("[reset-schema] dry-run: skipped")
        return

    conn = psycopg2.connect(
        dbname=pg["NAME"],
        user=pg["USER"],
        password=pg["PASSWORD"],
        host=pg["HOST"],
        port=pg["PORT"],
        connect_timeout=10,
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
        cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC;")
        cur.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER;")
        # Also grant to app role explicitly when connected as that role / owner.
        cur.execute(f'GRANT ALL ON SCHEMA public TO "{pg["USER"]}";')
    conn.close()
    print("[reset-schema] ok - empty public schema ready")


def step_schema(*, dry_run: bool) -> None:
    from django.conf import settings
    from django.core.management import call_command

    print("[schema] applying Django migrations on postgres ...")
    if dry_run:
        print("[schema] dry-run: would run migrate --database=postgres")
        return

    _assert_alias_is_postgres("postgres")
    before = _count_unapplied_migrations("postgres")
    print(f"[schema] unapplied migrations on postgres before: {before}")
    print(f"[schema] postgres ENGINE={settings.DATABASES['postgres']['ENGINE']}")

    # Historical RunPython migrations often use Model.objects without .using().
    # Point default at a *copy* of postgres settings for the duration of migrate.
    settings.DATABASES["default"] = copy.deepcopy(settings.DATABASES["postgres"])
    _reload_db_connections()
    try:
        _assert_alias_is_postgres("default")
        _assert_alias_is_postgres("postgres")
        from django.db import connections as _conns

        print(
            "[schema] default vendor="
            f"{_conns['default'].vendor}, postgres vendor={_conns['postgres'].vendor}"
        )
        call_command("migrate", database="postgres", interactive=False, verbosity=1)
    finally:
        settings.DATABASES["default"] = copy.deepcopy(settings.DATABASES["sqlite"])
        _reload_db_connections()

    after = _count_unapplied_migrations("postgres")
    print(f"[schema] unapplied migrations on postgres after: {after}")
    if after:
        raise SystemExit(
            f"[schema] ERROR: {after} migrations still unapplied on postgres"
        )

    # Sanity check: column that broke loaddata must exist.
    if not _postgres_column_exists("companies", "last_data_entry_assigned_employee_id"):
        raise SystemExit(
            "[schema] ERROR: companies.last_data_entry_assigned_employee_id missing "
            "on postgres after migrate. django_migrations is out of sync with the "
            "schema. Re-run with: reset-schema schema dump load verify --update-env"
        )
    print("[schema] ok - postgres schema matches migrations")


def step_dump(fixture_path: Path, excludes: list[str], *, dry_run: bool) -> Path:
    from django.apps import apps
    from django.core.management import call_command
    from django.db import connections

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[dump] writing fixture -> {fixture_path}")
    print(f"[dump] excludes: {', '.join(excludes) or '(none)'}")

    counts: dict[str, int] = {}
    for model in apps.get_models():
        label = f"{model._meta.app_label}.{model._meta.model_name}"
        skip = any(
            label.lower() == ex.lower()
            or label.lower().startswith(ex.lower().rstrip(".") + ".")
            or model._meta.app_label == ex
            or f"{model._meta.app_label}.{model.__name__}".lower() == ex.lower()
            for ex in excludes
        )
        if skip:
            continue
        try:
            counts[label] = model.objects.using("sqlite").count()
        except Exception:
            counts[label] = -1

    non_empty = {k: v for k, v in counts.items() if v and v > 0}
    print(f"[dump] sqlite models with rows: {len(non_empty)}")
    for label, count in sorted(non_empty.items(), key=lambda x: (-x[1], x[0]))[:25]:
        print(f"         {count:>7}  {label}")
    if len(non_empty) > 25:
        print(f"         ... and {len(non_empty) - 25} more")

    if dry_run:
        meta = fixture_path.with_suffix(".dry-run-counts.json")
        meta.write_text(json.dumps(counts, indent=2), encoding="utf-8")
        print(f"[dump] dry-run: counts saved to {meta}")
        return fixture_path

    call_command(
        "dumpdata",
        database="sqlite",
        natural_foreign=True,
        natural_primary=True,
        indent=2,
        output=str(fixture_path),
        exclude=excludes,
        verbosity=1,
    )
    size_mb = fixture_path.stat().st_size / (1024 * 1024)
    print(f"[dump] ok - {size_mb:.2f} MB")
    connections["sqlite"].close()
    return fixture_path


def _mute_model_signals():
    """Temporarily clear model signals so loaddata does not re-seed / notify."""
    from contextlib import contextmanager

    from django.db.models import signals as model_signals

    @contextmanager
    def _ctx():
        targets = (
            model_signals.pre_save,
            model_signals.post_save,
            model_signals.pre_delete,
            model_signals.post_delete,
            model_signals.m2m_changed,
        )
        stashed = []
        for sig in targets:
            stashed.append((sig, list(sig.receivers)))
            sig.receivers = []
            if hasattr(sig, "sender_receivers_cache"):
                sig.sender_receivers_cache.clear()
        try:
            yield
        finally:
            for sig, receivers in stashed:
                sig.receivers = receivers
                if hasattr(sig, "sender_receivers_cache"):
                    sig.sender_receivers_cache.clear()

    return _ctx()


def _widen_postgres_varchars_for_fixture(fixture_path: Path) -> None:
    """SQLite ignores CharField max_length; Postgres does not.

    Scan the fixture for string values longer than the model max_length and
    ALTER the postgres columns so loaddata cannot fail with
    StringDataRightTruncation.
    """
    from django.apps import apps
    from django.db import connections

    print("[load] scanning fixture for varchar overflows (sqlite -> postgres) ...")
    with fixture_path.open(encoding="utf-8") as fh:
        objects = json.load(fh)

    # (db_table, column) -> required length
    needed: dict[tuple[str, str], int] = {}
    samples: dict[tuple[str, str], str] = {}

    for obj in objects:
        model_label = obj.get("model")
        if not model_label:
            continue
        try:
            model = apps.get_model(model_label)
        except LookupError:
            continue
        fields_by_name = {
            f.name: f
            for f in model._meta.fields
            if getattr(f, "max_length", None)
        }
        for name, value in (obj.get("fields") or {}).items():
            if not isinstance(value, str):
                continue
            field = fields_by_name.get(name)
            if field is None:
                continue
            length = len(value)
            if length <= field.max_length:
                continue
            key = (model._meta.db_table, field.column)
            if length > needed.get(key, 0):
                needed[key] = length
                samples[key] = value[:80]

    if not needed:
        print("[load] no varchar overflows found")
        return

    # Pad a bit so near-limit values still fit later.
    with connections["postgres"].cursor() as cur:
        for (table, column), length in sorted(needed.items()):
            new_len = max(int(length) + 8, 64)
            print(
                f"[load] widening {table}.{column} -> varchar({new_len}) "
                f"(fixture had len={length}, e.g. {samples[(table, column)]!r})"
            )
            # Type length cannot be a bind param in PostgreSQL.
            cur.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f"TYPE varchar({new_len})"
            )
    print(f"[load] widened {len(needed)} column(s) on postgres")


def step_load(fixture_path: Path, *, dry_run: bool, wipe: bool) -> None:
    from django.core.management import call_command
    from django.db import connections

    if dry_run:
        print(f"[load] dry-run: would loaddata {fixture_path} into postgres")
        return
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")

    with _mute_model_signals():
        if wipe:
            print("[load] flushing postgres business data before load ...")
            call_command(
                "flush",
                database="postgres",
                interactive=False,
                verbosity=1,
            )

        # Apply any pending schema fixes (e.g. widened phone fields), then
        # auto-widen any remaining overflows found in this fixture.
        print("[load] ensuring postgres migrations are up to date ...")
        from django.conf import settings
        import copy

        settings.DATABASES["default"] = copy.deepcopy(settings.DATABASES["postgres"])
        _reload_db_connections()
        try:
            call_command("migrate", database="postgres", interactive=False, verbosity=1)
        finally:
            settings.DATABASES["default"] = copy.deepcopy(settings.DATABASES["sqlite"])
            _reload_db_connections()

        _widen_postgres_varchars_for_fixture(fixture_path)

        print(f"[load] loading {fixture_path} into postgres ...")
        print("[load] model signals muted (avoid re-seed / notifications on restore)")
        call_command(
            "loaddata",
            str(fixture_path),
            database="postgres",
            verbosity=1,
        )
    connections["postgres"].close()
    print("[load] ok")


def step_verify(*, dry_run: bool) -> bool:
    from django.apps import apps

    print("[verify] comparing row counts (sqlite vs postgres) ...")
    if dry_run:
        print("[verify] dry-run: skipped")
        return True

    ok = True
    rows = []
    for label in VERIFY_LABELS:
        try:
            model = apps.get_model(label)
        except LookupError:
            print(f"  SKIP  {label} (model not found)")
            continue
        sqlite_n = model.objects.using("sqlite").count()
        pg_n = model.objects.using("postgres").count()
        match = sqlite_n == pg_n
        if not match:
            ok = False
        status = "OK" if match else "MISMATCH"
        rows.append((status, label, sqlite_n, pg_n))
        print(f"  {status:8} {label:40} sqlite={sqlite_n:<6} postgres={pg_n}")

    # Also compare total rows across all concrete models (minus excludes).
    exclude_set = {e.lower() for e in DEFAULT_EXCLUDE}
    total_s = total_p = 0
    for model in apps.get_models():
        label = f"{model._meta.app_label}.{model._meta.model_name}"
        full = f"{model._meta.app_label}.{model.__name__}".lower()
        if (
            label.lower() in exclude_set
            or full in exclude_set
            or model._meta.app_label in exclude_set
        ):
            continue
        try:
            total_s += model.objects.using("sqlite").count()
            total_p += model.objects.using("postgres").count()
        except Exception:
            continue
    print(f"  TOTAL    (non-excluded models)              sqlite={total_s:<6} postgres={total_p}")
    if total_s != total_p:
        ok = False
        print("[verify] FAIL - totals differ")
    elif ok:
        print("[verify] ok - checked models match")
    else:
        print("[verify] FAIL - see mismatches above")
    return ok


def step_update_env(pg: dict, env_path: Path, *, dry_run: bool) -> None:
    print(f"[update-env] writing PostgreSQL settings to {env_path}")
    if dry_run:
        print(
            "[update-env] dry-run: would set "
            f"DB_ENGINE=postgresql DB_NAME={pg['NAME']} DB_USER={pg['USER']} "
            f"DB_HOST={pg['HOST']} DB_PORT={pg['PORT']}"
        )
        return
    update_env_for_postgres(
        env_path,
        name=pg["NAME"],
        user=pg["USER"],
        password=pg["PASSWORD"],
        host=pg["HOST"],
        port=str(pg["PORT"]),
    )
    print("[update-env] ok - restart gunicorn/systemd after this")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Migrate CRM-api-1 from SQLite to PostgreSQL without data loss."
    )
    p.add_argument(
        "steps",
        nargs="*",
        default=[],
        help="Steps: backup check reset-schema schema dump load verify update-env (or use --all)",
    )
    p.add_argument("--all", action="store_true", help="Run backup->check->schema->dump->load->verify")
    p.add_argument("--dry-run", action="store_true", help="Print actions / counts; no writes")
    p.add_argument("--update-env", action="store_true", help="Also flip .env to PostgreSQL")
    p.add_argument("--sqlite", type=Path, default=SQLITE_PATH, help="Path to db.sqlite3")
    p.add_argument("--env-file", type=Path, default=ENV_PATH, help="Path to .env with DB_*")
    p.add_argument("--db-name", default=None)
    p.add_argument("--db-user", default=None)
    p.add_argument("--db-password", default=None)
    p.add_argument("--db-host", default=None)
    p.add_argument("--db-port", default=None)
    p.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Fixture JSON path (default under media/backups/postgres_migration/fixtures/)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Extra dumpdata exclude label (repeatable). Defaults already exclude sessions/tokens/q history.",
    )
    p.add_argument(
        "--include-ephemeral",
        action="store_true",
        help="Do NOT exclude sessions/token_blacklist/django_q history/admin log",
    )
    p.add_argument(
        "--wipe-postgres",
        action="store_true",
        help="Flush postgres before loaddata (safe re-run). Required if load fails mid-way.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = load_dotenv_file(args.env_file)
    pg = postgres_settings_from_env(
        env,
        name=args.db_name,
        user=args.db_user,
        password=args.db_password,
        host=args.db_host,
        port=args.db_port,
    )
    if not pg["PASSWORD"] and not args.dry_run:
        # Allow dry-run without password; real steps need it.
        if args.all or any(
            s in (args.steps or [])
            for s in ("check", "reset-schema", "schema", "load", "verify")
        ):
            print(
                "ERROR: DB_PASSWORD is empty. Set it in .env or pass --db-password.",
                file=sys.stderr,
            )
            return 2

    excludes = [] if args.include_ephemeral else list(DEFAULT_EXCLUDE)
    excludes.extend(args.exclude)

    fixture = args.fixture or (FIXTURE_DIR / f"sqlite_dump_{_stamp()}.json")

    if args.all:
        steps = ["backup", "check", "schema", "dump", "load", "verify"]
    else:
        steps = [s.lower().replace("_", "-") for s in args.steps]

    if not steps:
        build_parser().print_help()
        print(
            "\nExamples:\n"
            "  python scripts/postgres_migration/run_migration.py --dry-run --all\n"
            "  python scripts/postgres_migration/run_migration.py --all --update-env\n"
            "  python scripts/postgres_migration/run_migration.py reset-schema schema load verify --update-env --fixture PATH --wipe-postgres\n"
            "  python scripts/postgres_migration/run_migration.py backup check\n"
        )
        return 1

    if args.update_env and "update-env" not in steps:
        steps.append("update-env")

    print("CRM-api-1 SQLite -> PostgreSQL migration")
    print(f"  root     = {PROJECT_ROOT}")
    print(f"  sqlite   = {args.sqlite}")
    print(f"  postgres = {pg['USER']}@{pg['HOST']}:{pg['PORT']}/{pg['NAME']}")
    print(f"  dry-run  = {args.dry_run}")
    print(f"  steps    = {' -> '.join(steps)}")
    print()

    needs_django = any(
        s in steps for s in ("schema", "dump", "load", "verify")
    )
    if needs_django:
        setup_django(pg, args.sqlite)

    for step in steps:
        if step == "backup":
            step_backup(args.sqlite, dry_run=args.dry_run)
        elif step == "check":
            step_check(pg, args.sqlite)
        elif step == "reset-schema":
            step_reset_schema(pg, dry_run=args.dry_run)
        elif step == "schema":
            step_schema(dry_run=args.dry_run)
        elif step == "dump":
            step_dump(fixture, excludes, dry_run=args.dry_run)
        elif step == "load":
            step_load(fixture, dry_run=args.dry_run, wipe=args.wipe_postgres)
        elif step == "verify":
            if not step_verify(dry_run=args.dry_run):
                return 3
        elif step == "update-env":
            step_update_env(pg, args.env_file, dry_run=args.dry_run)
        else:
            print(f"Unknown step: {step}", file=sys.stderr)
            return 2
        print()

    print("Done.")
    if not args.dry_run and "load" in steps:
        print(
            "Next:\n"
            "  1) Confirm verify passed\n"
            "  2) Ensure .env has DB_ENGINE=postgresql (use --update-env if needed)\n"
            "  3) sudo systemctl restart crm-api   # or your gunicorn unit\n"
            "  4) Smoke-test login + a few CRM pages\n"
            "  5) Keep the sqlite backup until you are confident\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
