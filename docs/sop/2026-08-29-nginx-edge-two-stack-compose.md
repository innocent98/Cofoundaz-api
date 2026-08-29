# SOP — nginx edge + two stacks on one VPS

> **Type:** infra / edge · **Date:** 2026-08-29 · **Area:** nginx, TLS, docker-compose, CD

## What shipped

Two things, in that order because the second could not work without the first.

1. **`docker-compose.prod.yml` can now run two stacks on one host.** It could not: the
   compose project name and all four container names were hardcoded, so a staging stack
   next to production collided on the project, on container names, and on port 8000.
2. **A complete, version-controlled nginx edge** (`deploy/nginx/`) for both hostnames —
   TLS 1.2+, HTTP/2, HTTP→HTTPS, HSTS, security headers, CSP, per-environment rate-limit
   zones, body-size ceilings sized to the logo endpoint, and JSON error pages in the app's
   own envelope — plus `docs/deployment/NGINX_TLS.md`.

Along the way, a **defect in the application's rate limiter behind a proxy** was diagnosed
and proven. The fix is one environment variable and is deliberately **not applied** — see
"The X-Forwarded-For defect" below.

## Why

**The two-stack blocker.** The deployment already assumed production and staging on one VPS
(`DEPLOY_PATH` is per-environment; CD deploys both), but the compose file it deploys could
only ever be one stack. `name: cofoundaz-api-prod` is not overridable, `container_name:` is
global to the Docker daemon rather than scoped to the project, and `API_PORT` defaulted to
8000 for both. The second `compose up` would have adopted the first stack's containers —
silently — and a later `down -v` would have destroyed the other environment's Postgres
volume.

**The edge did not exist.** `DEPLOYMENT_GUIDE.md` said "point nginx at it" and marked it NOT
VERIFIED. More consequentially, the staging E2E gate added on 2026-08-28 reaches
`vars.APP_URL` from a GitHub-hosted runner — and the API binds loopback only, so **without
nginx the gate could never pass and production was unreachable**. That prerequisite was
nowhere in the setup docs.

## How

### Compose parameterisation

- **`name: ${COMPOSE_PROJECT_NAME:-cofoundaz-api-prod}`.** Interpolated rather than fixed.
  Compose *also* reads `COMPOSE_PROJECT_NAME` natively from the env file, so the two
  mechanisms agree by construction — they read the same variable. The default is
  production's historical value, so an existing prod stack is not renamed (which would
  orphan its volumes and start it on an empty database).
- **All four `container_name:` keys removed.** A fixed container name is global to the
  daemon, so it collides no matter what the project is called. Without the key compose
  derives `<project>-<service>-<n>`, unique per stack for free. Every reference was checked
  first: `.github/actions/deploy-stack/action.yml` already resolved via
  `docker compose … ps -q api`, the `Makefile` goes through `$(PROD_COMPOSE)`, and
  `scripts/` never names a container. Only prose in three docs mentioned the old names.
- **`API_PORT` per environment** — production 8000, staging 8001 — with the per-stack
  variables documented in `.env.production.example` §9 and in the compose header.

**Rejected:** giving staging its own compose file. Two files diverge, and the one that
diverges is the one you discover during an incident — the same argument that made
`deploy-stack` a composite action.

### Bug found while parameterising

`API_PORT` was read as `$(grep -E '^API_PORT=' .env | cut -d= -f2 || echo 8000)` in three
places. In a pipeline `||` tests the **last** command's status and `cut` exits 0 on empty
input, so the fallback never fired: a missing `API_PORT` produced
`http://127.0.0.1:/api/v1/health/ready`, which fails every readiness poll and **rolls the
deploy back for the wrong reason**. Harmless while both environments used 8000; not harmless
now. Replaced with `sed` + a separate default in `deploy-stack/action.yml`, `cd.yml` and the
`Makefile`.

### nginx

Structured as http-level `conf.d/` + shared `snippets/` + thin vhosts, so the TLS posture
and proxy headers exist **once**. Decisions worth recording:

- **`X-Forwarded-For $remote_addr`, not `$proxy_add_x_forwarded_for`.** The near-universal
  default appends to a client-supplied header; overwriting is correct at a trust boundary.
  This turned out to be load-bearing — see below.
- **`proxy_read_timeout 65s`**, five seconds above gunicorn's `--timeout 60`, so gunicorn
  always fires first and the operator gets `WORKER TIMEOUT` in the app log rather than an
  opaque 504 with no upstream record.
- **Upstream `keepalive_timeout 4s`**, below gunicorn's `--keep-alive 5`. The reverse is the
  classic source of intermittent unreproducible 502s: nginx writes into a socket gunicorn
  already closed.
- **No `$connection_upgrade` map.** The app has no WebSocket routes (verified). The usual
  boilerplate would set `Connection: close` on every request and silently disable the
  keepalive pool.
- **`client_max_body_size 1m` globally, `3m` on `= /api/v1/onboarding/logo`.** The app caps
  at 2 MiB and returns its own 422; 3m means a 2–3 MB request reaches the app and gets that
  actionable JSON instead of nginx's HTML 413. Setting the global limit to the largest any
  endpoint needs is the common mistake.
- **Rate limits ~15× above the app's per-user budget**, in separate zones per environment.
  nginx cannot see a JWT subject, so a tight per-IP limit would punish everyone behind one
  NAT for each other's traffic. Staging's auth zone is looser still (15 r/s vs 5) so the
  CD gate's serial burst from one runner address cannot 429 itself.
- **HSTS without `preload`, on both.** `preload` is the one irreversible directive here, and
  it is an apex-wide decision that would lock every future subdomain of `cofoundaz.com` to
  HTTPS for the `max-age`. Not a subdomain's call.
- **A `default_server` catch-all** using `ssl_reject_handshake on;`, so an unknown `Host` or
  a bare-IP probe gets nothing rather than being answered by whichever vhost sorts first.
- **JSON error pages** for 413/429/502/503/504 in the app's `{"error":{"code",…}}` envelope,
  with `proxy_intercept_errors off` so the *application's* 4xx/5xx bodies still pass through
  untouched. The one class of response a frontend is guaranteed to handle badly is the one
  it only sees during an incident.
- **OCSP stapling left on but documented as inert.** Let's Encrypt no longer publishes an
  OCSP URL, so nginx logs `"ssl_stapling" ignored…`. That warning is expected; saying so is
  cheaper than someone "fixing" it.
- **`certbot --nginx` deliberately not used** — it rewrites config that is now
  version-controlled. `--webroot` plus a bootstrap vhost that breaks the
  cannot-start-without-a-cert / cannot-get-a-cert-without-starting cycle.

## The `X-Forwarded-For` defect — diagnosed, proven, NOT fixed here

Behind nginx, every **unauthenticated** request keys the app's rate limiter on the same
address, so login, signup and forgot-password share one 120/minute bucket for the entire
internet.

`_rate_limit_key` falls back to `get_remote_address()` → `scope["client"]`. Uvicorn's
`ProxyHeadersMiddleware` **is** already installed (`proxy_headers=True` is the `Config`
default and `UvicornWorker` does not override it), but it only trusts a peer listed in
`forwarded_allow_ips`, default `"127.0.0.1,::1"`. **The peer is never `127.0.0.1`**: nginx
connects to the host's loopback, but Docker opens a second connection into the container
from the compose bridge gateway.

Observed against the real image and stack, 2026-08-29:

| Setup | Request | Client the app logged |
|---|---|---|
| default | none | `172.25.0.1` — the bridge gateway |
| default | `X-Forwarded-For: 203.0.113.9` | `172.25.0.1` — ignored |
| `FORWARDED_ALLOW_IPS=*` | `X-Forwarded-For: 203.0.113.9` | `203.0.113.9` ✅ |
| `FORWARDED_ALLOW_IPS=*` | `X-Forwarded-For: 198.51.100.7, 172.25.0.1` | `198.51.100.7` — the **leftmost** |

**The exact fix is one environment variable: `FORWARDED_ALLOW_IPS=*`.** Gunicorn reads it
natively (`gunicorn.config.ForwardedAllowIPS`) and `UvicornWorker` passes it into
`uvicorn.Config`. No code change, no CLI flag.

Two narrower options were tried and **both fail**:

- **A CIDR is rejected by gunicorn.** `validate_string_to_addr_list` calls
  `ipaddress.ip_address()` per entry, which raises on a network — the container crash-loops
  with `Error: '172.16.0.0/12' does not appear to be an IPv4 or IPv6 address`. Observed.
  uvicorn's own `_TrustedHosts` *does* accept CIDR; gunicorn never lets the value reach it.
  Guides recommending a CIDR here are describing bare uvicorn.
- **A literal gateway address** is not stable — Docker allocates the bridge subnet from a
  pool at network-creation time.

Row 4 is why the nginx side matters: with `*`, uvicorn takes the **leftmost** value, so
under `$proxy_add_x_forwarded_for` any client could pick its own rate-limit identity per
request. The overwrite makes the header single-valued and the question moot. **Verified end
to end**: a request sent *through nginx* carrying `X-Forwarded-For: 203.0.113.9` reached the
app as nginx's real peer.

`FORWARDED_ALLOW_IPS=*` is documented in `.env.production.example` and is **inert** — the
real values live in the committed ciphertext, which only Adebayo can regenerate. Deliberately
left as its own review.

## Staging exposure

Staging must be publicly reachable — the CD gate drives it from a GitHub runner, which rules
out an IP allowlist. **Recommendation: ship it without authentication**, with
`X-Robots-Tag: noindex, nofollow, noarchive`, full TLS/header parity with production, and
rate limiting. Staging runs the same image and holds test data; the residual risks (signup
spam, enumeration) are covered by the rate limits and the app's own login lockout. Every
auth layer added here is a layer the production deploy path depends on.

**This holds only while staging's `.env` holds staging secrets.** Point it at production
data and the recommendation is void.

If Basic auth is wanted anyway, the mechanism needing **zero test changes** is documented
and verified: add a `staging` environment **secret** `E2E_BASE_URL` carrying userinfo
(`https://ci:<pass>@staging-api.cofoundaz.com`) and read it in `cd.yml` instead of
`vars.APP_URL` (which stays clean because it renders in the GitHub UI). `e2e/conftest.py`
builds `httpx.Client(base_url=BASE_URL)`, and **httpx 0.27.2 — the pinned version, tested
inside the project's own image with that exact construction — sends `Authorization: Basic`
from URL userinfo**. `auth_basic off;` is required on the health location, and the ACME
snippet is already exempt.

**OpenAPI docs:** served on staging, **404 on production**. Staging's `openapi.json` is not
optional — `e2e/test_smoke.py::test_openapi_served` gates production on it. Serving it there
is what makes closing production cheap: same image, so the schema is authoritative for the
frontend.

## What's involved

| File | Change |
|---|---|
| `docker-compose.prod.yml` | project name interpolated; four `container_name:` keys removed; `API_PORT` documented per stack |
| `.env.production.example` | new per-stack block (`COMPOSE_PROJECT_NAME`, `API_PORT`, `SERVER_HOST`); `FORWARDED_ALLOW_IPS` |
| `.github/actions/deploy-stack/action.yml` | `API_PORT` read with `sed` + default; logs the port and project |
| `.github/workflows/cd.yml` | same fix in the limiter-reset step |
| `Makefile` | same fix in `prod-up` |
| `deploy/nginx/conf.d/*.conf` | new — TLS, hardening + per-host maps, upstreams, rate-limit zones |
| `deploy/nginx/snippets/*.conf` | new — proxy headers, security headers, JSON errors, ACME |
| `deploy/nginx/sites-available/*.conf` | new — catch-all, production vhost, staging vhost |
| `deploy/nginx/bootstrap/acme-bootstrap.conf` | new — first-run HTTP-only vhost |
| `deploy/nginx/test/verify-local.sh` | new — 35-assertion local harness |
| `deploy/nginx/README.md` | new |
| `docs/deployment/NGINX_TLS.md` | new — setup, DNS, certificates, renewal, XFF, staging posture, troubleshooting |
| `docs/deployment/DEPLOYMENT_GUIDE.md` | §1, host prerequisites, walkthrough step 7, §13 rewritten for two stacks |
| `docs/deployment/GITHUB_ACTIONS_SETUP.md` | nginx named as a hard prerequisite for the E2E gate; checklist items added |

`.github/workflows/ci.yml` and `codeql.yml` were **out of scope and are untouched** —
another branch owns them.

## Verification

Local, 2026-08-29. macOS, Docker 29.1.3, Compose v2.40.3-desktop.1.

**Two stacks, simultaneously, from the one compose file:**

- Both up at once: `cfzproof-prod-{api,db,redis}-1` and `cfzproof-staging-{api,db,redis}-1`,
  compose-derived names, no collision.
- Distinct ports: `127.0.0.1:18000` and `127.0.0.1:18001`, both `/api/v1/health/ready`
  returning `{"status":"ready"}` with database and redis `ok`.
- Distinct networks and volumes: `cfzproof-{prod,staging}_{postgres,redis}_data`,
  `_app_storage`, `_backend`, with distinct `docker inspect` mount sources.
- **Volume separation proven by data, not by name:** a `volume_marker` row reading
  `PRODUCTION-VOLUME` / `STAGING-VOLUME` written into each Postgres and read back from the
  correct one.
- `compose down` on staging left production up and serving; staging's port refused.
- **`compose down -v` on staging destroyed only staging's three volumes** — production's
  three survived and `SELECT who FROM volume_marker` still returned `PRODUCTION-VOLUME`.
- `config --format json` confirms `container_name` is absent from all four services and the
  project name defaults to `cofoundaz-api-prod` when `COMPOSE_PROJECT_NAME` is unset.

Throwaway env files in a scratch directory with fake secrets; both projects and all six
volumes removed afterwards. No shared stack was touched.

**nginx** — `deploy/nginx/test/verify-local.sh`, real nginx 1.27-alpine, real backends,
self-signed certificates at the real `/etc/letsencrypt/live/…` paths so the vhosts are used
unedited: **`nginx -t` clean; 35 assertions, 0 failures.** Covering the 301 and its target,
the ACME path not being redirected, h2 negotiation, both upstreams proxied, per-host header
sets (prod HSTS with `includeSubDomains` and no `preload`; staging shorter and `noindex`),
headers surviving a non-2xx (`always`), docs 404-on-prod / 200-on-staging, the docs CSP,
three body-size boundaries with the JSON 413, `ssl_reject_handshake` on unknown SNI, the
auth-zone 429 with the `RATE_LIMITED` envelope, and general traffic unaffected by it.

Three of those assertions failed on the first run. All three were **harness** bugs, not
config bugs — two compared mixed-case header names against HTTP/2's lowercased ones, and one
re-requested after a 429 into a bucket that had already refilled. Fixed in the script, with
the reason written next to each so the next reader does not repeat them.

**`FORWARDED_ALLOW_IPS`** — the four-row table above, observed from real container logs.

**httpx userinfo** — run inside the project's own image against a local server, with the
exact `httpx.Client(base_url=…)` construction `e2e/conftest.py` uses.

## Operate / roll back

- Config change: edit `deploy/nginx/`, run `verify-local.sh`, copy to the host,
  `sudo nginx -t`, then `sudo systemctl reload nginx` — never `restart`. Roll back by
  restoring the previous file and reloading.
- The two per-stack values (`COMPOSE_PROJECT_NAME`, `API_PORT`) live in the encrypted
  `.env`s. Changing either means edit locally → `./scripts/env.sh encrypt <env>` → commit
  the `.enc` → redeploy.
- **Changing `COMPOSE_PROJECT_NAME` on a running stack points it at a different, empty set
  of volumes.** It is not a rename; it is a new stack. Back up first.

## Follow-ups

- **NOT VERIFIED: nothing has run against a real VPS or real DNS.** No certificate was
  obtained, no real handshake observed, no SSL Labs grade measured, no certbot renewal
  exercised, HTTP/3 and brotli never parsed by an nginx that could accept them. The first
  deploy is the verification run.
- **`FORWARDED_ALLOW_IPS=*` is not active anywhere.** It needs to be added to `.env.staging`
  and `.env.production` and re-encrypted — Adebayo's action. Until then the app's anonymous
  rate limit is a single global bucket, and `X-Forwarded-Proto` is not honoured either.
- The nginx upstreams **hardcode 8000/8001** while the stacks read `API_PORT` from `.env`.
  Nothing enforces agreement; disagreement presents as a 502 on one vhost only. A
  deploy-time assertion would close this.
- The `staging` GitHub environment must have `APP_URL` set to the public staging URL, and
  that URL must resolve and serve TLS, before CD can reach production.
- Staging Basic auth is documented and ready but **off**. Revisit if staging is ever given
  production-shaped data.
- Certificate expiry should be monitored from **off-host** — certbot can fail silently on a
  rate limit or a DNS change. Nothing is configured.
- `deploy/nginx/` is not covered by any linter in CI. `nginx -t` needs an nginx binary;
  `verify-local.sh` needs running backends. A CI job that runs just the parse check in the
  nginx container would be cheap and is not done.
