#!/usr/bin/env bash
#
# Boot the built image THE WAY PRODUCTION BOOTS IT, and assert it works.
#
# WHY THIS EXISTS
# ---------------
# Nothing in this repository has ever started the image's own CMD.
#
#   - ci.yml's `build` job smoke test runs `python -c "import app.main"` and
#     `alembic --help`. Both override the CMD, so gunicorn never runs.
#   - scripts/e2e_run.sh boots `uvicorn app.main:app` directly on the host. That
#     is a different process model entirely: one process, no prefork, no
#     arbiter, no worker recycling, no signal forwarding through tini.
#   - docker-compose.yml (dev) explicitly overrides the CMD with reloading
#     uvicorn.
#
# So the Dockerfile's CMD - gunicorn with uvicorn workers, --graceful-timeout,
# --max-requests, WEB_CONCURRENCY - shipped to production completely
# unexercised. A change to any of it (a gunicorn major bump, a removed flag, a
# worker class uvicorn deprecated out from under us, a WEB_CONCURRENCY that
# stops being read) would first be discovered on the VPS.
#
# This script closes that gap. It runs the image with NO command override
# against throwaway Postgres and Redis on a user-defined network, and asserts:
#
#   1. the image still declares the gunicorn CMD (the check cannot silently
#      degrade into testing something else)
#   2. gunicorn loads the uvicorn worker class
#   3. exactly WEB_CONCURRENCY workers actually spawn, and are alive
#   4. the container reaches Docker's HEALTHY state via the image's own
#      HEALTHCHECK, at the image's own timings
#   5. /health returns 200
#   6. /api/v1/health/ready returns 200 with database AND redis both ok
#   7. SIGTERM drains gracefully: exit code 0, well inside the grace period.
#      NOT 137. A 137 is SIGKILL, which means in-flight requests are dropped on
#      every single deploy - and that is the regression this exists to catch.
#
# A user-defined network rather than GitHub Actions `services:`, deliberately:
# service containers are published on the RUNNER's localhost, which a container
# cannot reach without host-gateway plumbing that behaves differently on a
# developer machine. A network created here behaves identically everywhere, so
# `make image-check` locally is the same test CI runs.
#
# Usage:  scripts/image_cmd_check.sh [IMAGE]
#         IMAGE defaults to $IMAGE, else cofoundaz-api:ci
#
# Requires: docker, curl, python3.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-${IMAGE:-cofoundaz-api:ci}}"

# Deliberately NOT the image's baked-in default of 4. If this check asserted the
# default, a CMD that hardcoded `--workers 4` and ignored WEB_CONCURRENCY
# entirely would pass. Overriding proves the env var is actually the knob, which
# is what docker-compose.prod.yml relies on.
EXPECTED_WORKERS="${EXPECTED_WORKERS:-3}"
# What the Dockerfile bakes in, asserted separately without a second boot.
EXPECTED_DEFAULT_WORKERS="${EXPECTED_DEFAULT_WORKERS:-4}"

HOST_PORT="${IMAGE_CHECK_PORT:-18010}"
# Mirrors docker-compose.prod.yml's stop_grace_period. The point is to give the
# app the same room production gives it, then assert it did not need all of it.
STOP_TIMEOUT="${IMAGE_CHECK_STOP_TIMEOUT:-60}"
# gunicorn's --graceful-timeout is 30s. With no in-flight requests a clean drain
# is a second or two; anything approaching 30s means workers are not responding
# to SIGTERM and are being force-killed by the arbiter.
MAX_DRAIN_SECONDS="${IMAGE_CHECK_MAX_DRAIN:-25}"

# Unique per run so concurrent runs (parallel CI jobs, several local sessions on
# one machine) cannot collide on a name.
SUFFIX="$$-$(date +%s)"
NET="cfz-cmdcheck-${SUFFIX}"
PG="cfz-cmdcheck-db-${SUFFIX}"
RD="cfz-cmdcheck-redis-${SUFFIX}"
API="cfz-cmdcheck-api-${SUFFIX}"

PG_USER="cmdcheck"
PG_PASSWORD="cmdcheck"
PG_DB="cmdcheck"

fail() {
  echo ""
  echo "!! IMAGE CMD CHECK FAILED: $*" >&2
  echo "" >&2
  echo "---- container state ----" >&2
  docker inspect --format \
    'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    "${API}" 2>&1 >&2 || true
  echo "---- processes in the container ----" >&2
  docker top "${API}" 2>&1 >&2 || echo "(container not running)" >&2
  echo "---- last 60 lines of api logs ----" >&2
  docker logs --tail 60 "${API}" 2>&1 >&2 || true
  echo "---- healthcheck probe output ----" >&2
  docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}exit={{.ExitCode}} out={{.Output}}{{end}}{{end}}' \
    "${API}" 2>&1 >&2 || true
  echo "---- db logs ----" >&2
  docker logs --tail 20 "${PG}" 2>&1 >&2 || true
  exit 1
}

cleanup() {
  docker rm -f "${API}" "${PG}" "${RD}" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> image under test: ${IMAGE}"

# --------------------------------------------------------------------------- #
# 1. The image must still declare the gunicorn CMD.
# --------------------------------------------------------------------------- #
# Without this, someone could change the Dockerfile to CMD ["uvicorn", ...] and
# every assertion below would still pass while testing the wrong process model.
IMAGE_CMD="$(docker image inspect --format '{{json .Config.Cmd}}' "${IMAGE}")"
echo "==> declared CMD: ${IMAGE_CMD}"
case "${IMAGE_CMD}" in
  *gunicorn*) : ;;
  *) echo "!! image CMD is not gunicorn: ${IMAGE_CMD}" >&2; exit 1 ;;
esac
for flag in "--worker-class" "uvicorn.workers.UvicornWorker" "--graceful-timeout"; do
  case "${IMAGE_CMD}" in
    *"${flag}"*) : ;;
    *) echo "!! image CMD no longer contains ${flag}: ${IMAGE_CMD}" >&2; exit 1 ;;
  esac
done

# The baked-in default worker count, checked without paying for a second boot.
BAKED_CONCURRENCY="$(docker image inspect \
  --format '{{range .Config.Env}}{{println .}}{{end}}' "${IMAGE}" \
  | sed -n 's/^WEB_CONCURRENCY=//p')"
if [ "${BAKED_CONCURRENCY}" != "${EXPECTED_DEFAULT_WORKERS}" ]; then
  echo "!! image default WEB_CONCURRENCY is '${BAKED_CONCURRENCY}', expected '${EXPECTED_DEFAULT_WORKERS}'" >&2
  echo "   docker-compose.prod.yml falls back to this when WEB_CONCURRENCY is unset." >&2
  exit 1
fi
echo "==> CMD and baked WEB_CONCURRENCY=${BAKED_CONCURRENCY} OK"

# --------------------------------------------------------------------------- #
# 2. Throwaway dependencies on their own network.
# --------------------------------------------------------------------------- #
echo "==> creating network ${NET} + throwaway postgres/redis"
docker network create "${NET}" >/dev/null

# Images match docker-compose.prod.yml so the check exercises the same major
# versions production runs.
docker run -d --name "${PG}" --network "${NET}" \
  -e POSTGRES_USER="${PG_USER}" \
  -e POSTGRES_PASSWORD="${PG_PASSWORD}" \
  -e POSTGRES_DB="${PG_DB}" \
  --health-cmd "pg_isready -U ${PG_USER} -d ${PG_DB}" \
  --health-interval 2s --health-timeout 3s --health-retries 15 \
  postgres:17-alpine >/dev/null

docker run -d --name "${RD}" --network "${NET}" \
  --health-cmd "redis-cli ping" \
  --health-interval 2s --health-timeout 3s --health-retries 15 \
  redis:7-alpine >/dev/null

for svc in "${PG}" "${RD}"; do
  ok=""
  for _ in $(seq 1 60); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' "${svc}" 2>/dev/null)" = "healthy" ]; then
      ok="1"; break
    fi
    sleep 1
  done
  if [ -z "${ok}" ]; then
    echo "!! dependency ${svc} never became healthy" >&2
    docker logs --tail 40 "${svc}" >&2 || true
    exit 1
  fi
done
echo "==> dependencies healthy"

# --------------------------------------------------------------------------- #
# 3. Start the image with NO command override. This is the whole point.
# --------------------------------------------------------------------------- #
echo "==> starting ${IMAGE} with its own CMD (WEB_CONCURRENCY=${EXPECTED_WORKERS})"
docker run -d --name "${API}" --network "${NET}" \
  -p "127.0.0.1:${HOST_PORT}:8000" \
  -e WEB_CONCURRENCY="${EXPECTED_WORKERS}" \
  -e SECRET_KEY="image-cmd-check-not-a-real-key" \
  -e DATABASE_URL="postgresql://${PG_USER}:${PG_PASSWORD}@${PG}:5432/${PG_DB}" \
  -e REDIS_URL="redis://${RD}:6379/0" \
  -e FIRST_SUPERUSER_EMAIL="admin@example.com" \
  -e FIRST_SUPERUSER_PASSWORD="image-cmd-check" \
  -e LOG_FILE_PATH="" \
  "${IMAGE}" >/dev/null

# --------------------------------------------------------------------------- #
# 4. The image's OWN healthcheck must reach healthy, at the image's own timings.
# --------------------------------------------------------------------------- #
# Not overridden with a faster interval on purpose: if --start-period=30s and
# --interval=30s are too slow to ever go healthy inside a deploy window, that is
# a real defect in the image and this is where it should surface.
echo "==> waiting for the container's own HEALTHCHECK to report healthy"
healthy=""
for _ in $(seq 1 120); do
  status="$(docker inspect -f '{{.State.Status}}' "${API}" 2>/dev/null || echo gone)"
  if [ "${status}" != "running" ]; then
    fail "container stopped before becoming healthy (status=${status}). A gunicorn flag rejected at startup looks exactly like this."
  fi
  if [ "$(docker inspect -f '{{.State.Health.Status}}' "${API}" 2>/dev/null)" = "healthy" ]; then
    healthy="1"; break
  fi
  sleep 1
done
[ -n "${healthy}" ] || fail "container never reported healthy within 120s"
echo "==> container healthy"

# --------------------------------------------------------------------------- #
# 5. gunicorn must have loaded the uvicorn worker class.
# --------------------------------------------------------------------------- #
LOGS="$(docker logs "${API}" 2>&1)"
echo "${LOGS}" | grep -q "Using worker: uvicorn.workers.UvicornWorker" \
  || fail "gunicorn did not report 'Using worker: uvicorn.workers.UvicornWorker'. The worker class may have been renamed, deprecated or removed."

# --------------------------------------------------------------------------- #
# 6. Exactly EXPECTED_WORKERS workers spawned.
# --------------------------------------------------------------------------- #
# gunicorn's own statement of how many workers it started. This is the exact
# assertion; it is authoritative and independent of process-title formatting.
BOOTED="$(printf '%s\n' "${LOGS}" | grep -c "Booting worker with pid:" || true)"
[ "${BOOTED}" -eq "${EXPECTED_WORKERS}" ] \
  || fail "gunicorn booted ${BOOTED} workers, expected ${EXPECTED_WORKERS}. WEB_CONCURRENCY is not being honoured."

# Corroborate that those are real, live OS processes rather than a line in a log
# - a master that booted workers which instantly died still prints the boot
# lines. Deliberately a LOWER BOUND (master + workers) rather than an equality:
# the exact count depends on how the entrypoint chain renders in argv, which is
# not something this check should be brittle about. The precise "did a worker
# die" assertion is the respawn check in step 8, which needs no argv at all.
#
# tini is excluded by name: its argv is `/usr/bin/tini -- gunicorn ...`, so it
# matches "gunicorn" too.
#
# Plain `docker top`, NOT `docker top -eo args`: docker filters the host ps
# output down to the container's processes by matching a PID column, so a format
# without one silently returns zero rows - which looks exactly like "every
# worker died".
LIVE="$(docker top "${API}" 2>/dev/null | grep "gunicorn" | grep -vc "tini" || true)"
MIN_LIVE=$((EXPECTED_WORKERS + 1))
[ "${LIVE}" -ge "${MIN_LIVE}" ] \
  || fail "expected at least ${MIN_LIVE} live gunicorn processes (1 master + ${EXPECTED_WORKERS} workers), found ${LIVE}. Workers booted and died."
echo "==> ${EXPECTED_WORKERS} workers booted, ${LIVE} gunicorn processes alive"

# --------------------------------------------------------------------------- #
# 7. The endpoints production and the deploy gate actually call.
# --------------------------------------------------------------------------- #
BASE="http://127.0.0.1:${HOST_PORT}"

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/health" || true)"
[ "${code}" = "200" ] || fail "/health returned ${code}, expected 200 (this is what the Docker HEALTHCHECK and nginx hit)"

READY_BODY="$(curl -s --max-time 10 "${BASE}/api/v1/health/ready" || true)"
READY_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/api/v1/health/ready" || true)"
[ "${READY_CODE}" = "200" ] \
  || fail "/api/v1/health/ready returned ${READY_CODE}, expected 200. Body: ${READY_BODY}"

# Parse rather than grep: this endpoint answers 200 with a per-dependency
# breakdown, so a body saying database=error would sail straight past a
# substring match. Distinguishing "200" from "200 AND actually ready" is the
# entire job of the gate CD waits on after a deploy.
#
# The body arrives via the environment, not stdin, because stdin is the program.
if ! READY_BODY="${READY_BODY}" python3 <<'PY'
import json
import os
import sys

body = os.environ["READY_BODY"]
try:
    data = json.loads(body)
except ValueError:
    sys.exit("readiness body was not JSON: " + repr(body))

checks = data.get("checks", {})

for required in ("database", "redis"):
    if required not in checks:
        sys.exit(
            "readiness did not report a " + required + " check at all; got: "
            + repr(sorted(checks))
        )

problems = [
    name + "=" + repr(info.get("status")) + " reason=" + repr(info.get("reason"))
    for name, info in checks.items()
    if info.get("status") != "ok"
]
if data.get("status") != "ready" or problems:
    sys.exit(
        "readiness reported status=" + repr(data.get("status"))
        + "; failing checks: " + (", ".join(problems) or "none")
    )

print("    readiness revision=" + repr(data.get("revision")))
PY
then
  fail "readiness did not report both dependencies healthy. Body: ${READY_BODY}"
fi

echo "==> /health 200 and /api/v1/health/ready 200 with both dependencies ok"

# --------------------------------------------------------------------------- #
# 8. No worker churned while serving those requests.
# --------------------------------------------------------------------------- #
# A worker that dies is silently replaced by the arbiter, so the service still
# answers 200 and nothing above would notice. The tell is a SECOND "Booting
# worker" line for a replacement. Comparing the count before and after real
# traffic catches a worker that crashes on its first request - which is exactly
# how an incompatible worker class or a broken async stack presents.
LOGS_AFTER="$(docker logs "${API}" 2>&1)"
BOOTED_AFTER="$(printf '%s\n' "${LOGS_AFTER}" | grep -c "Booting worker with pid:" || true)"
[ "${BOOTED_AFTER}" -eq "${EXPECTED_WORKERS}" ] \
  || fail "workers respawned while serving requests: ${BOOTED_AFTER} boot lines, expected ${EXPECTED_WORKERS}. A worker is crashing and being replaced."

if printf '%s\n' "${LOGS_AFTER}" | grep -qE "Worker exiting|was sent SIGKILL|WORKER TIMEOUT"; then
  fail "gunicorn reported a worker exit/timeout/SIGKILL while serving. See the log dump above."
fi
echo "==> no worker churn while serving"

# --------------------------------------------------------------------------- #
# 9. Graceful shutdown. THE ASSERTION THIS SCRIPT EXISTS FOR.
# --------------------------------------------------------------------------- #
# `docker stop` sends SIGTERM to PID 1 (tini), which must forward it to the
# gunicorn master, which must drain its workers and exit 0 inside
# --graceful-timeout. Exit 137 is 128+SIGKILL: the grace period elapsed and the
# kernel killed it, which in production means every deploy severs whatever
# requests were in flight.
echo "==> sending SIGTERM (docker stop --timeout ${STOP_TIMEOUT})"
START_STOP="$(date +%s)"
docker stop --timeout "${STOP_TIMEOUT}" "${API}" >/dev/null
ELAPSED=$(( $(date +%s) - START_STOP ))
EXIT_CODE="$(docker inspect -f '{{.State.ExitCode}}' "${API}")"
OOM="$(docker inspect -f '{{.State.OOMKilled}}' "${API}")"

echo "==> stopped after ${ELAPSED}s with exit code ${EXIT_CODE} (oom_killed=${OOM})"

case "${EXIT_CODE}" in
  0) : ;;
  137)
    fail "SIGKILLed (exit 137) after ${ELAPSED}s. The container did NOT drain inside the ${STOP_TIMEOUT}s grace period, so every deploy drops in-flight requests. Check that tini forwards SIGTERM, that gunicorn's --graceful-timeout (30s) is below stop_grace_period, and that no worker is ignoring SIGTERM."
    ;;
  143)
    fail "exited 143 (128+SIGTERM) after ${ELAPSED}s: the process was terminated BY the signal rather than handling it and shutting down cleanly. gunicorn's SIGTERM handler did not run."
    ;;
  *)
    fail "exited ${EXIT_CODE} after ${ELAPSED}s on SIGTERM, expected 0."
    ;;
esac

if [ "${ELAPSED}" -gt "${MAX_DRAIN_SECONDS}" ]; then
  fail "exited 0 but took ${ELAPSED}s to drain, over the ${MAX_DRAIN_SECONDS}s budget. With no in-flight requests this should be near-instant; a slow drain means workers are only dying at gunicorn's --graceful-timeout, and real traffic would be cut off."
fi

echo ""
echo "==> IMAGE CMD CHECK PASSED"
echo "    CMD run unmodified, ${EXPECTED_WORKERS} uvicorn workers under gunicorn,"
echo "    healthy via the image's own HEALTHCHECK, /health + readiness 200,"
echo "    graceful shutdown exit 0 in ${ELAPSED}s."
