# syntax=docker/dockerfile:1.7
#
# Production image for cofoundaz-api.
#
# Two stages:
#   builder  - has Poetry + a compiler toolchain, resolves the LOCKED dependency
#              set into a self-contained virtualenv at /opt/venv.
#   runtime  - has none of that. It receives /opt/venv and the application source
#              and nothing else, so build tooling never reaches production.
#
# The base image is pinned by DIGEST, not just by tag. `python:3.11-slim-bookworm`
# is a moving target that is rebuilt weekly; a digest makes `docker build` of a
# given commit produce the same base every time. To take base-image security
# patches, refresh the digest deliberately:
#
#   docker buildx imagetools inspect python:3.11-slim-bookworm
#
# (This is a multi-arch OCI index digest, so it resolves correctly on both
# linux/amd64 and linux/arm64.)
ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:0bee7276f83efd4a1ee05bbbf4281d95ed28e079220a9457f25a93e3f1e3c31b


# --------------------------------------------------------------------------- #
# Stage 1: builder
# --------------------------------------------------------------------------- #
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Must match the Poetry that generated poetry.lock. The lockfile is
    # lock-version 2.1, which Poetry 1.x CANNOT read - do not downgrade this
    # without regenerating the lockfile.
    POETRY_VERSION=2.2.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_NO_ANSI=1 \
    # Install into the venv we create below rather than letting Poetry manage
    # its own cache-dir venv, so the result is a single directory we can copy.
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
 && python -m venv "${VIRTUAL_ENV}"

WORKDIR /build

# Dependency manifests only. Kept in their own layer so that editing application
# source does NOT invalidate the (slow) dependency install layer.
COPY pyproject.toml poetry.lock README.md ./

# Fail the build loudly if poetry.lock is missing or out of sync with
# pyproject.toml. Note the COPY above has NO trailing glob (`poetry.lock*`):
# a glob would let a missing lockfile pass silently and resolve dependencies
# fresh at build time, which is exactly the non-reproducibility we are
# eliminating. A missing lockfile now fails at COPY.
RUN poetry check --lock

# --only main  : no dev/test dependencies in the production image.
# --no-root    : do not install the `app` package itself. The source is COPYed
#                into the runtime stage and imported from WORKDIR, so installing
#                it here would only serve to invalidate this layer on every
#                source edit.
RUN poetry install --only main --no-root \
 # Strip the installer toolchain out of the shipped venv. Production never
 # installs packages at runtime, so pip/setuptools/wheel are ~15MB of pure
 # attack surface (and a recurring source of Trivy findings for a capability
 # the container must never use). Package metadata under *.dist-info is left
 # intact so image scanners can still enumerate the dependency set.
 && "${VIRTUAL_ENV}/bin/pip" uninstall --yes pip setuptools wheel \
 # Belt and braces after the uninstall. NOTE: written as explicit paths, NOT
 # brace expansion - Docker RUN uses /bin/sh (dash on Debian), which does not
 # support `{a,b,c}`. A brace-expanded rm here looks correct, silently matches
 # nothing, and leaves the packages in the image.
 && rm -rf "${VIRTUAL_ENV}/lib/python3.11/site-packages/pkg_resources" \
           "${VIRTUAL_ENV}/lib/python3.11/site-packages/_distutils_hack" \
 && find "${VIRTUAL_ENV}" -type d -name '__pycache__' -prune -exec rm -rf {} + \
 && find "${VIRTUAL_ENV}" -type f -name '*.pyc' -delete


# --------------------------------------------------------------------------- #
# Stage 2: runtime
# --------------------------------------------------------------------------- #
FROM ${PYTHON_IMAGE} AS runtime

# Provenance, injected by CI. These make a running container traceable back to
# the exact commit that produced it - without them, "what is actually deployed?"
# is guesswork.
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG VERSION=0.0.0
ARG PYTHON_IMAGE

LABEL org.opencontainers.image.title="cofoundaz-api" \
      org.opencontainers.image.description="Cofoundaz FastAPI backend" \
      org.opencontainers.image.vendor="Beyric Tech" \
      org.opencontainers.image.source="https://github.com/innocent98/cofoundaz-api" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    # Read back by the app/ops for provenance; also surfaced by /health/ready.
    APP_GIT_SHA=${GIT_SHA} \
    APP_BUILD_VERSION=${VERSION} \
    # Gunicorn reads WEB_CONCURRENCY natively for its worker count. Sized for the
    # 4 vCPU baseline documented in docs/deployment/. Override per VPS.
    WEB_CONCURRENCY=4

# tini is a real init: it reaps zombies and forwards signals to the whole process
# group. Without an init, a SIGTERM to PID 1 can leave gunicorn workers behind and
# turns every `docker compose down` into a 10s timeout-then-SIGKILL, which drops
# in-flight requests instead of draining them.
#
# NOTE: libpq is deliberately NOT installed. The previous image installed
# `libpq-dev` (a build-time headers package) at runtime. It was never needed:
# psycopg2-binary ships its own libpq inside the wheel
# (site-packages/psycopg2_binary.libs/libpq-*.so.5). Verified with ldd on the
# built extension module. Dropping it removes a compiler-adjacent package and
# its CVE surface from the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 # The base image's own pip/setuptools live OUTSIDE the venv and survive the
 # builder-stage strip. Remove them too: nothing in production installs
 # packages, and leaving an installer in the image is a privilege-escalation
 # convenience for anyone who gets code execution.
 #
 # Explicit paths, no brace expansion - /bin/sh here is dash, which treats
 # `{a,b}` as a literal filename. Trivy also reads setuptools' VENDORED
 # dist-info (jaraco.*, wheel), so leaving setuptools behind reports CVEs for
 # code that is never imported.
 && rm -rf /usr/local/lib/python3.11/site-packages/pip \
           /usr/local/lib/python3.11/site-packages/setuptools \
           /usr/local/lib/python3.11/site-packages/pkg_resources \
           /usr/local/lib/python3.11/site-packages/_distutils_hack \
 && rm -rf /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python3.11/site-packages/wheel-*.dist-info \
 && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 \
          /usr/local/bin/wheel \
 && groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Dependencies and application source are owned by root and NOT writable by the
# user the process runs as. The previous image ran `chown -R appuser /app`, which
# let the running application rewrite its own source - that turns any file-write
# bug into persistent code execution. appuser needs to READ this, never write it.
COPY --from=builder --chown=root:root /opt/venv /opt/venv
COPY --chown=root:root alembic.ini /app/alembic.ini
COPY --chown=root:root alembic/ /app/alembic/
COPY --chown=root:root app/ /app/app/

# The only writable paths in the image. LOCAL_STORAGE_DIR and EMAIL_FILE_DIR
# default under ./var, so these must exist and be owned by appuser for the
# container to run with a read-only root filesystem (see docker-compose.prod.yml).
#
# /app/logs exists as a safety net only. Production sets LOG_FILE_PATH="" so the
# loguru file sink is off and logs go to stderr for Docker's log driver to
# collect and rotate. If that env var is ever lost, the app must still start
# rather than crash-loop on PermissionError.
RUN mkdir -p /app/var/storage /app/var/mail /app/logs \
 && chown -R appuser:appuser /app/var /app/logs

# Numeric UID:GID rather than the name (hadolint DL3066). A name has to be
# resolved against /etc/passwd INSIDE the image; a numeric id is unambiguous to
# the host kernel and to any runtime that enforces "must not run as root"
# (Kubernetes runAsNonRoot cannot verify a username). 1000:1000 is the appuser
# account created above.
USER 1000:1000

EXPOSE 8000

# Liveness only - deliberately hits the cheap static /health, not /health/ready.
# Docker restarts nothing on its own here, but an unhealthy marker is the signal
# both `docker compose ps` and the deploy gate read. A readiness check that
# depends on Postgres would flap the API's health whenever the DB blipped, which
# is the DB's problem to report, not the API's.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]

# Gunicorn as the process manager, uvicorn as the worker implementation:
#   - N workers actually use the VPS's cores (the old single uvicorn process used one).
#   - A worker that wedges is killed by --timeout and respawned; a lone uvicorn
#     process just stays wedged.
#   - --max-requests recycles workers to bound slow memory growth; the jitter stops
#     every worker recycling on the same request and stalling the service.
#   - --graceful-timeout 30 gives in-flight requests 30s to finish on SIGTERM. It
#     must be LESS than compose's stop_grace_period (60s) or the drain is cut short
#     by SIGKILL.
# Worker count comes from WEB_CONCURRENCY (gunicorn reads it natively), so it is
# tunable per VPS without rebuilding the image.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
