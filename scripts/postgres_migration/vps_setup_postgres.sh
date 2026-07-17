#!/usr/bin/env bash
# Provision PostgreSQL on a Linux VPS for CRM-api-1.
# Run as root (or with sudo): bash scripts/postgres_migration/vps_setup_postgres.sh
#
# Safe defaults - override via env:
#   DB_NAME=crm_db DB_USER=crm_user DB_PASSWORD='strong-pass' bash ...
#
# If a broken third-party apt repo blocks update (e.g. monarx):
#   SKIP_APT_UPDATE=1 DB_PASSWORD='...' bash scripts/postgres_migration/vps_setup_postgres.sh

set -euo pipefail

DB_NAME="${DB_NAME:-crm_db}"
DB_USER="${DB_USER:-crm_user}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
SKIP_APT_UPDATE="${SKIP_APT_UPDATE:-0}"

if [[ -z "${DB_PASSWORD}" ]]; then
  echo "Set DB_PASSWORD before running, e.g.:"
  echo "  sudo DB_PASSWORD='your-strong-password' bash scripts/postgres_migration/vps_setup_postgres.sh"
  exit 1
fi

disable_broken_apt_repos() {
  # Monarx questing mirror often 404s and aborts the whole apt update.
  local disabled=0
  for path in /etc/apt/sources.list.d/*monarx* /etc/apt/sources.list.d/*Monarx*; do
    [[ -e "$path" ]] || continue
    if [[ "$path" == *.disabled ]]; then
      continue
    fi
    echo "==> Disabling broken apt source: $path"
    mv "$path" "${path}.disabled"
    disabled=1
  done
  if [[ "$disabled" -eq 1 ]]; then
    echo "    (re-enable later by renaming *.disabled back if you need Monarx)"
  fi
}

echo "==> Installing PostgreSQL (if needed)"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive

  if dpkg -s postgresql >/dev/null 2>&1 && dpkg -s libpq-dev >/dev/null 2>&1; then
    echo "    postgresql + libpq-dev already installed — skipping apt install"
  else
    disable_broken_apt_repos

    if [[ "${SKIP_APT_UPDATE}" != "1" ]]; then
      echo "    running apt-get update ..."
      if ! apt-get update -y; then
        echo "WARNING: apt-get update failed (often a broken third-party repo)."
        echo "Retrying after disabling common broken sources, or set SKIP_APT_UPDATE=1"
        disable_broken_apt_repos
        apt-get update -y || {
          echo "ERROR: apt-get update still failing."
          echo "Fix/disable the bad repo under /etc/apt/sources.list.d/ then re-run,"
          echo "or: sudo SKIP_APT_UPDATE=1 DB_PASSWORD='...' bash scripts/postgres_migration/vps_setup_postgres.sh"
          exit 1
        }
      fi
    else
      echo "    SKIP_APT_UPDATE=1 — using existing apt cache"
    fi

    apt-get install -y postgresql postgresql-contrib libpq-dev
  fi
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y postgresql-server postgresql-contrib
  postgresql-setup --initdb || true
else
  echo "Unsupported package manager. Install PostgreSQL manually, then re-run."
  exit 1
fi

systemctl enable --now postgresql

echo "==> Creating role + database (idempotent)"
# Run as postgres OS user
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';
  ELSE
    ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec

GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

# Postgres 15+ needs schema grants on public
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
GRANT ALL ON SCHEMA public TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${DB_USER};
SQL

echo "==> Testing login as ${DB_USER}"
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c 'SELECT current_database(), current_user;'

echo
echo "PostgreSQL ready."
echo "Add these to your CRM-api .env (or let run_migration.py --update-env do it):"
echo "  DB_ENGINE=postgresql"
echo "  DB_NAME=${DB_NAME}"
echo "  DB_USER=${DB_USER}"
echo "  DB_PASSWORD=${DB_PASSWORD}"
echo "  DB_HOST=${DB_HOST}"
echo "  DB_PORT=${DB_PORT}"
echo
echo "Next (app stopped):"
echo "  cd /var/www/crm-api   # or your app path"
echo "  ./venv/bin/python scripts/postgres_migration/run_migration.py --dry-run --all"
echo "  ./venv/bin/python scripts/postgres_migration/run_migration.py --all --update-env"
