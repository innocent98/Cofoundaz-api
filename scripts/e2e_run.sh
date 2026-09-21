#!/usr/bin/env bash
#
# Live end-to-end runner for the auth API.
#
# Boots a REAL uvicorn server against an isolated `cofoundaz_e2e` Postgres DB
# (migrated from zero) and live Redis, with EMAIL_BACKEND=file so the e2e suite
# can read one-time tokens back out of captured emails. Runs the sanity + smoke
# + journey layers, then tears the server down.
#
# Usage:  scripts/e2e_run.sh
# Requires: docker compose (db + redis), poetry, a populated .env (SECRET_KEY,
#           MFA_ENCRYPTION_KEY).
set -euo pipefail
cd "$(dirname "$0")/.."

# --- config (override via env) ---
PG_USER="${E2E_PG_USER:-user}"
PG_PASSWORD="${E2E_PG_PASSWORD:-password}"
PG_HOST="${E2E_PG_HOST:-localhost}"
PG_PORT="${E2E_PG_PORT:-5432}"
ADMIN_DB="${E2E_ADMIN_DB:-cofoundaz-api_db}"
E2E_DB="${E2E_DB:-cofoundaz_e2e}"
PORT="${E2E_PORT:-8010}"
MAIL_DIR="${E2E_MAIL_DIR:-./var/mail-e2e}"

export DATABASE_URL="postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${E2E_DB}"
export EMAIL_BACKEND="file"
export EMAIL_FILE_DIR="${MAIL_DIR}"
# The FE origin for emailed deep links (share `/shared/{token}`, signing `/sign/{token}`,
# and auth verify/reset links). Distinct from the API origin (E2E_BASE_URL, :8010) so the
# captured emails prove the link opens an FE page, not an API route -- exactly how
# staging/prod are configured (APP_BASE_URL=https://app.cofoundaz.com). Exported to BOTH
# the uvicorn server and this pytest process so the e2e can assert the link against it.
export APP_BASE_URL="${E2E_APP_BASE_URL:-http://localhost:3000}"
# Deterministic, offline AI: both the server process and the pytest process (which
# runs the in-process worker drain for e2e/test_ai_assessment_narrative.py) use the
# stub LLM client -- no key, no network, no dependency on a real model being reachable.
export LLM_PROVIDER="stub"
export RATE_LIMIT_PER_MINUTE="100000"   # keep the limiter in the path but out of the way of journeys
export E2E_BASE_URL="http://127.0.0.1:${PORT}"
export E2E_MAIL_DIR="${MAIL_DIR}"

# The e2e server talks plain HTTP, so a Secure cookie would (correctly) never be
# sent back by the client — relax it here to exercise real cookie-transport, the
# same way a local dev server over http:// would be configured.
export REFRESH_COOKIE_SECURE="False"

# Self-contained MFA: generate an ephemeral Fernet key so the run never depends on
# a populated .env. (Note: a real deployment MUST set a persistent MFA_ENCRYPTION_KEY.)
export MFA_ENCRYPTION_KEY="${MFA_ENCRYPTION_KEY:-$(poetry run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"

# Self-contained journal encryption: same pattern as MFA above, so Module 21's
# journal writes work in e2e. (A real deployment MUST set a persistent JOURNAL_ENCRYPTION_KEY.)
export JOURNAL_ENCRYPTION_KEY="${JOURNAL_ENCRYPTION_KEY:-$(poetry run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"

SERVER_PID=""
SERVER_LOG="$(mktemp -t cfz-e2e-server.XXXXXX.log)"

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "==> stopping server (pid ${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> [sanity] docker db + redis up"
# `--wait` blocks until both services report HEALTHY via their compose
# healthchecks, rather than merely "started". This is the primary gate.
#
# Why this matters (found the hard way on a cold CI runner): the previous
# version polled `pg_isready` in a loop that `break`s on success but FELL
# THROUGH SILENTLY when it never succeeded - so a timeout looked identical to
# success. Worse, on a FIRST-EVER start Postgres runs initdb, which boots a
# temporary internal server; `pg_isready` can answer "accepting connections"
# against THAT, after which initdb stops it to start the real one. The wait
# passed and the very next psql hit nothing:
#
#   psql: error: connection to server on socket "...PGSQL.5432" failed:
#         No such file or directory
#
# It never reproduced locally because the dev volume already existed, so initdb
# never ran. Reproduced locally only after `docker compose down -v`.
if ! docker compose up -d --wait db redis; then
  echo "!! docker compose up --wait failed for db/redis" >&2
  docker compose ps
  docker compose logs --tail=50 db redis
  exit 1
fi

# Belt and braces: prove the REAL server answers a REAL query on the admin
# database - which is exactly what the next command needs. A healthcheck can pass
# against initdb's temporary server; `SELECT 1` on the target database cannot.
echo "==> [sanity] waiting for Postgres to accept queries on ${ADMIN_DB}"
db_ready=""
for _ in $(seq 1 30); do
  if docker compose exec -T db psql -U "${PG_USER}" -d "${ADMIN_DB}" -c "SELECT 1;" >/dev/null 2>&1; then
    db_ready="1"; break
  fi
  sleep 1
done
# FAIL LOUDLY on timeout. The whole point of the original bug was that this
# branch did not exist and the script carried on into a guaranteed failure with
# a confusing error thirty lines later.
if [[ -z "${db_ready}" ]]; then
  echo "!! Postgres never accepted a query on ${ADMIN_DB} after 30s" >&2
  docker compose ps
  docker compose logs --tail=50 db
  exit 1
fi

echo "==> [sanity] recreate ${E2E_DB} (clean state)"
docker compose exec -T db psql -U "${PG_USER}" -d "${ADMIN_DB}" \
  -c "DROP DATABASE IF EXISTS ${E2E_DB};" -c "CREATE DATABASE ${E2E_DB};" >/dev/null

echo "==> [sanity] alembic upgrade head on ${E2E_DB}"
poetry run alembic upgrade head >/dev/null

echo "==> [sanity] app imports"
poetry run python -c "import app.main" >/dev/null

echo "==> [sanity] fresh mail dir ${MAIL_DIR}"
rm -rf "${MAIL_DIR}" && mkdir -p "${MAIL_DIR}"

echo "==> launching uvicorn on :${PORT}"
poetry run uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --no-access-log \
  >"${SERVER_LOG}" 2>&1 &
SERVER_PID=$!

echo "==> [sanity] waiting for /health"
ready=""
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then ready="1"; break; fi
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "!! server died during startup — log:"; tail -30 "${SERVER_LOG}"; exit 1
  fi
  sleep 0.5
done
if [[ -z "${ready}" ]]; then
  echo "!! server never became healthy — log:"; tail -30 "${SERVER_LOG}"; exit 1
fi
echo "==> server healthy"

echo "==> running e2e suite (smoke + journeys)"
set +e
poetry run pytest e2e/ -o addopts="" -v -p no:cacheprovider
CODE=$?
set -e

echo "==> server log tail:"; tail -15 "${SERVER_LOG}" || true
exit "${CODE}"
