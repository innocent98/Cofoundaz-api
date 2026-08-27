# Deployment Guide — cofoundaz-api

> **Type:** deployment reference · **Stack:** FastAPI + Postgres 17 + Redis 7 on a single
> Docker-Compose VPS behind nginx · **Last verified:** 2026-08-27

This is the operator's manual for running `cofoundaz-api` in production. It documents the
image, the compose stack, the CI/CD pipeline, and the runbooks for the things that go wrong.

## How to read the verification markers

Everything in this guide was written against the actual files in this repo. Where a claim was
**observed running** on 2026-08-27 (macOS, Docker 29.1.3, Compose v2.40.3-desktop.1) it is
stated plainly. Where it was **reasoned from config but never executed**, it carries an
explicit marker:

> **NOT VERIFIED —** what was not proven, and what would prove it.

**No real VPS was used to write this.** Nothing about SSH deploy, nginx, UFW, GHCR
push/pull, or GitHub Actions execution has been observed. Those sections are reasoned from
the config and are marked accordingly. Treat the first production deploy as the verification
run, and correct this document afterwards.

---

## 1. What gets deployed

| Layer | What it is |
|---|---|
| Image | Two-stage Dockerfile. Base pinned by multi-arch digest `python:3.11-slim-bookworm@sha256:0bee7276f83…`. Runtime carries `/opt/venv` + app source and nothing else. |
| Process model | `tini` (PID 1) → gunicorn master → 4 `uvicorn.workers.UvicornWorker` workers (`WEB_CONCURRENCY=4`). |
| Stack | `docker-compose.prod.yml`, compose project `cofoundaz-api-prod`: `migrate` (one-shot), `api`, `db`, `redis`. |
| Edge | nginx on the host terminates TLS and proxies to `127.0.0.1:${API_PORT}`. The API is **not** published on `0.0.0.0`. |
| Registry | GHCR — `ghcr.io/innocent98/cofoundaz-api`. Deploys use the **immutable digest**, not a tag. |

### Image hardening — what was verified

| Property | Verified observation |
|---|---|
| Size | 396MB → 376MB (−20MB, −5%). The size win is modest; the real win is attack surface. |
| No `libpq-dev` | `ldd` confirms `psycopg2-binary` bundles its own libpq at `site-packages/psycopg2_binary.libs/libpq-0249bd74.so.5.17`. The system package was never needed. |
| No installer | `pip`, `setuptools`, `wheel` removed from **both** the venv and the base image's `/usr/local`. Verified absent post-build. |
| Non-writable source | App source and `/opt/venv` are root-owned; the process runs as `appuser` (uid 1000). `touch /app/app/evil.py` → **Permission denied**. |
| Read-only rootfs | `read_only: true` on `api`. `touch /probe` → **Read-only file system**. `tmpfs /tmp` (64m) and the `app_storage` volume are writable — both verified. |
| Init behaviour | Process tree confirmed `tini` → 1 gunicorn master + 4 workers. |
| Provenance | OCI labels present: `revision`, `created`, `version`, `source`, `title`, `vendor`, `base.name`. |
| Graceful shutdown | `docker compose stop api` completed in **1s**, exit code **0** (not 137/SIGKILL). |

gunicorn flags, as shipped:

```
--worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
--timeout 60 --graceful-timeout 30 --keep-alive 5
--max-requests 1000 --max-requests-jitter 100
```

`--graceful-timeout 30` must stay **below** compose's `stop_grace_period: 60s`, or the drain
is cut short by SIGKILL on every deploy.

**Known future migration:** `uvicorn.workers.UvicornWorker` is deprecated upstream in favour
of the separate `uvicorn-worker` package. It works on uvicorn 0.32.1 (verified), but this
will need moving before a uvicorn major bump.

---

## 2. VPS provisioning and prerequisites

> **NOT VERIFIED —** this entire section. No VPS was provisioned. Verification is: run these
> steps on a fresh host and confirm `docker compose version` ≥ v2 and `make prod-config`
> renders without warnings.

### Target spec — CONFIRMED

**4 vCPU / 8 GB RAM / 80 GB SSD.** This is the confirmed production target, not a working
assumption. Every resource limit in `docker-compose.prod.yml` is derived from it. Section 5
carries the full arithmetic and how to re-derive it if a host ever differs.

### Required on the host

| Requirement | Notes |
|---|---|
| Docker Engine + Compose v2 plugin | `docker compose` (subcommand), not the legacy `docker-compose` binary. The compose file uses `name:`, `service_completed_successfully`, and `deploy.resources` under Compose v2. |
| A non-root `deploy` user | In the `docker` group. **Do not deploy as root** — one leaked SSH key would otherwise mean full host compromise. |
| nginx | Terminates TLS on 443, proxies to `127.0.0.1:${API_PORT}`. |
| `curl` | The deploy script polls readiness with it. |
| Deploy directory | `${DEPLOY_PATH}` (e.g. `/opt/cofoundaz-api`), owned by the deploy user. Must contain `docker-compose.prod.yml` and `.env.production`. |

### Files that must exist on the VPS

```
${DEPLOY_PATH}/
├── docker-compose.prod.yml     # copied from the repo
└── .env.production             # chmod 600, owned by the deploy user, NEVER in git
```

`.gitignore` denies `.env` and `.env.*` and re-allows only `.env.example` and
`.env.production.example`. That rule is what keeps a production credential file out of the
repository — do not weaken it.

---

## 3. Firewall posture — the point people get wrong

Inbound: **22** (SSH, key-only), **80** and **443** (nginx). Nothing else.

### Docker bypasses UFW. This is the important part.

Docker writes its own iptables `DOCKER` chain, and that chain is consulted **before** UFW's
rules. A `ufw deny 5432` therefore does **not** protect a container port published with
`ports: - "5432:5432"`. The rule is real, UFW reports it as active, and the port is still
reachable from the internet.

The defence is not the firewall — it is **not publishing the port at all**:

| Service | Port config | Consequence |
|---|---|---|
| `api` | `"127.0.0.1:${API_PORT:-8000}:8000"` | Loopback only. Reachable by nginx on the host, unreachable from the public IP. |
| `db` | **no `ports:` key** | Reachable only on the internal `backend` compose network. |
| `redis` | **no `ports:` key** | Same. |

Verified in the rendered compose config: the API is published on loopback only; `db` and
`redis` have no published ports.

If you need `psql` from your laptop, tunnel — never publish:

```bash
ssh -L 15432:localhost:5432 deploy@vps   # then point psql at localhost:15432
```

> **NOT VERIFIED —** UFW behaviour on the actual host. Verify with `sudo ufw status verbose`
> plus an external `nmap -Pn <vps-ip>` from off-host after the first deploy, and confirm only
> 22/80/443 answer.

---

## 4. First-time deploy walkthrough

> **NOT VERIFIED —** steps 1–6 were never executed against a VPS. The compose behaviour in
> step 5 (ordering, migrations, readiness) *was* verified locally; the SSH/host parts were not.

**1. Prepare the deploy directory.**

```bash
ssh deploy@vps
sudo mkdir -p /opt/cofoundaz-api && sudo chown deploy:deploy /opt/cofoundaz-api
```

**2. Copy the compose file and the env template.** Copy `docker-compose.prod.yml` and
`.env.production.example` from the repo to `${DEPLOY_PATH}`.

**3. Fill in `.env.production`.**

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Every `CHANGE_ME` must be replaced. The generators are documented inline in the template.
Two of them are **not rotatable in place** and deserve a second read:

| Variable | Generate with | Danger |
|---|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` | Rotating logs every user out immediately. Survivable, and the correct response to a suspected leak. |
| `MFA_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | **Not rotatable.** Every stored TOTP secret is encrypted with it. Change it and every MFA-enrolled user is permanently locked out. Back it up somewhere that survives the VPS. |
| `POSTGRES_PASSWORD` | `openssl rand -base64 32 \| tr -d '/+=' \| head -c 40` | Read **only on first start**, when initdb creates the data directory. Changing it later does not change the DB password — the app just fails to authenticate. Rotate with `ALTER USER` *and* this file together. |

Also confirm: `ENVIRONMENT=production` (anything else keeps the log level at DEBUG),
`LOG_FILE_PATH=` **empty**, `REFRESH_COOKIE_SECURE=True`, `EMAIL_BACKEND=smtp` (`console`
silently discards verification and reset emails — signup appears to work while nobody can
ever verify), and exact `BACKEND_CORS_ORIGINS` with no wildcard and no trailing slashes.

**4. Authenticate to GHCR on the host.**

```bash
printf '%s' "$GHCR_PULL_TOKEN" | docker login ghcr.io -u <ghcr-user> --password-stdin
```

**5. Bring the stack up.**

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Verified startup ordering (locally): `db` becomes healthy → `migrate` runs all 7 revisions →
`migrate` exits 0 → `api` starts. Final state: `alembic_version = 0007_roadmap_applied_templates`,
25 tables in `public`.

**6. Confirm readiness.**

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

**7. Point nginx at it.** nginx proxies to `127.0.0.1:${API_PORT}`.

> **NOT VERIFIED —** no nginx config exists in this repo and none was tested. Verify with
> `nginx -t`, then an external `curl -I https://api.yourdomain.com/health` returning 200 over
> TLS.

### Local rehearsal note

Running the prod stack on a laptop via the build fallback (`up --build`) logs a transient
`pull access denied for cofoundaz-api` for the `migrate` service before the `api` build
produces the tag. It then proceeds correctly — cosmetic only. It does **not** occur on the CD
path, because the deploy script does `docker pull` first. To avoid it locally:

```bash
docker compose -f docker-compose.prod.yml build   # then:
make prod-up
```

---

## 5. Resource limits and the sizing arithmetic

All limits were **verified as applied** via `docker inspect` on `HostConfig`:

| Service | CPU limit | Mem limit | Mem reservation | Verified `HostConfig` |
|---|---|---|---|---|
| `api` | 2.0 | 2048M | 1024M | `NanoCpus=2000000000`, `Memory=2147483648`, `MemoryReservation=1073741824` |
| `db` | 1.5 | 3072M | 1024M | `NanoCpus=1500000000`, `Memory=3221225472`, `MemoryReservation=1073741824` |
| `redis` | 0.5 | 512M | 128M | `NanoCpus=500000000`, `Memory=536870912`, `MemoryReservation=134217728` |
| `migrate` | 0.5 | 512M | — | one-shot; exits before `api` starts |
| **Nominal sum** | **4.5** | **6144M** | | *not a real peak — see below* |

### The nominal 4.5 CPU never actually occurs

Add the CPU column up and you get **4.5 on a 4 vCPU box — a 12.5% oversubscription.** State
it explicitly rather than leave someone to find it and worry, because it is not reachable:

`api` and `migrate` **cannot run at the same time.** `api` declares
`depends_on: migrate: condition: service_completed_successfully`, so `migrate` has already
exited before `api` starts. The two states that actually exist are:

| State | Services running | CPU | Memory |
|---|---|---|---|
| During migration | `db` + `redis` + `migrate` | **2.5** | 4096M |
| Steady state | `db` + `redis` + `api` | **4.0** | 5632M |

Both fit 4 vCPU, and steady state lands exactly on it. Verified against the compose file with
`docker compose config`, not estimated.

Two things follow, and both matter:

1. **Even if 4.5 were reachable it would be acceptable.** CPU limits are *ceilings*, not
   reservations — Docker does not refuse to schedule an oversubscribed set, it throttles under
   contention. Memory limits are the ones that kill a container.
2. **Memory has no equivalent escape.** 6144M nominal against 8192M is real headroom, and at
   steady state it is 5632M — leaving **2560M (31%)** for the host kernel, nginx, sshd, and
   most importantly the **page cache Postgres depends on** for read performance. Committing
   100% of RAM to container limits is how a VPS starts OOM-killing.

> **Note on where this was tested.** Limits were verified as *applied* on a local Docker VM
> with only 2.35 GiB RAM and 2 CPUs — smaller than the target spec, so `docker stats` displays
> a clamped limit for `db` and the limits could not be *stress-tested*. The arithmetic above is
> against the confirmed 4 vCPU / 8 GB spec. **NOT VERIFIED:** behaviour under real memory
> pressure on the target hardware.

### Verified caveat: CPU *reservations* do nothing here

`deploy.resources.reservations.cpus` maps to **nothing** outside Swarm. Verified on the `api`
container: `CpuShares=0`, `CpuQuota=0`, `CpuPeriod=0` — only `NanoCpus` (the *limit*) is set.

**Memory** reservations *do* apply (`MemoryReservation` is set). So:

- CPU **limits** — enforced.
- CPU **reservations** — declarative only on Compose v2.40.3. They document intent; they do
  not guarantee anything.
- Memory limits and reservations — both enforced.

Do not delete the CPU reservations (they encode intent), but do not rely on them either.

### Connection arithmetic — the constraint people miss

```
WEB_CONCURRENCY (4) × (DATABASE_POOL_SIZE 5 + DATABASE_MAX_OVERFLOW 5)  =  40
+ migrate one-shot                                                      ≈   2
POSTGRES_MAX_CONNECTIONS                                                =  100
                                                                        ─────
spare for psql / pg_dump / monitoring                                   ≈  58
```

**Confirmed correct for the 4 vCPU / 8 GB spec:** peak application demand is 42 of 100
connections (42%), leaving 58 for `psql`, `pg_dump`, and monitoring. `WEB_CONCURRENCY=4` also
matches the 4 vCPU count, which is the right shape here — the API's FastAPI handlers are
synchronous `def` functions served from each worker's thread pool, so workers are the unit of
parallelism and one per vCPU is the sane default.

Raising `WEB_CONCURRENCY` **without** raising `max_connections` is how you get
`FATAL: sorry, too many clients already` under load. The ceiling on this spec is
`WEB_CONCURRENCY=9` (9 × 10 + 2 = 92 < 100) — but CPU, not connections, binds first. Re-check
this arithmetic on every change to either number.

### Postgres tuning — verified applied inside the running container

`max_connections=100`, `shared_buffers=768MB` (~25% of the 3 GB limit), `work_mem=8MB`.
Also set: `effective_cache_size=2GB`, `maintenance_work_mem=256MB`, `random_page_cost=1.1`
(SSD), `wal_compression=on`, `log_min_duration_statement=1000` (anything slower than 1s is
findable in `docker compose logs db` without full statement logging).

`work_mem` is **per sort**, not per connection — a single query can use several. That is why
8MB against 100 connections stays small.

### Re-deriving for a different VPS

| Knob | Rule |
|---|---|
| `db` memory limit | ~35–40% of host RAM. |
| `POSTGRES_SHARED_BUFFERS` | ~25% of the `db` container's memory limit. |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | ~50–60% of host RAM (a planner hint, not an allocation — too low and it avoids index scans it should use). |
| `api` CPU limit | ~50% of vCPUs. |
| `WEB_CONCURRENCY` | Start at the vCPU count. Then re-run the connection arithmetic. |
| `POSTGRES_MAX_CONNECTIONS` | ≥ `WEB_CONCURRENCY × 10 + 20` headroom. |
| Sum of all memory limits | Leave ≥ 25% of host RAM unallocated. |

Every one of these is an env var in `.env.production` — no compose edit required.

> **NOT VERIFIED —** limits were confirmed *applied* but never **stress-tested**. The local
> Docker VM has 2.35 GiB RAM and 2 CPUs, smaller than the 4 vCPU / 8 GB baseline, so `docker
> stats` shows the `db` limit clamped to the VM size. On a real 8 GB VPS it would show 3 GiB.
> Verification: a load test on the real host with `docker stats` sampled throughout.

### Redis: persistence is off, deliberately

`--save "" --appendonly no`, `maxmemory 384mb`, `maxmemory-policy volatile-ttl`.

Redis holds **only TTL'd ephemeral data** — MFA tickets (5 min TTL, `app/services/auth/mfa.py`)
and signup-resend / forgot-password cooldowns. A Redis restart costs a user mid-MFA their
password re-entry; **no durable data is lost**. `maxmemory` (384M) sits below the container
limit (512M) on purpose, so Redis evicts by policy before the kernel OOM-kills it.

**If durable state is ever added to Redis, this must be revisited** — persistence back on,
and the backup section below extended.

---

## 6. The CI/CD flow, end to end

> **NOT VERIFIED —** none of the three workflows (`ci.yml`, `cd.yml`, `codeql.yml`) has ever
> run on GitHub. All pass `actionlint 1.7.12` with `shellcheck 0.11.0` integration (exit 0 on
> all three plus the composite action, and the shellcheck integration was confirmed live by
> feeding actionlint a deliberately-bad script and seeing SC2086 reported). Static validation
> is not execution. The first push is the verification run.

### CI — `.github/workflows/ci.yml`

Triggers on push and PR to `main` and `develop`. `cancel-in-progress` is true for
`pull_request` only — **not** for `main`, because a cancelled main CI run would leave that
commit undeployable.

**Job graph — 10 jobs.** Eight run fully in parallel with no `needs:` at all; only two are
gated:

```
lint · test · migrations · e2e · security · quality · trivy-repo · dependency-review
   │      │
   │      └──► sonarcloud    needs: [test]   (skips cleanly when SONAR_TOKEN is absent)
   │
   └──► build   needs: [lint, quality, test, migrations, e2e, security, trivy-repo]
```

`sonarcloud` and `dependency-review` are deliberately **not** in `build`'s `needs:`. One is
optional and one only exists on `pull_request` events — gating the image build on either would
make `build` unreachable on a push to `main`.

| Job | What it does | Local verification |
|---|---|---|
| `lint` | black, isort, ruff — now including **C901** (mccabe complexity) — and **mypy** | All green. mypy: *"Success: no issues found in 91 source files"*. Measured worst complexity: `validate_answer` in `app/services/assessment/engine.py` at **11**; everything else ≤ 9. |
| `test` | `postgres:17-alpine` + `redis:7-alpine` services, `pytest --cov-fail-under=95`, uploads `coverage.xml` as an artifact | **338 passed, 98.29% coverage** (baseline before this work: 331 passed, 98%). |
| `migrations` | exactly-one-head check, upgrade from empty, `alembic check` (model/schema drift), then `downgrade base` + `upgrade head` | 1 head (`0007_roadmap_applied_templates`), 0 extra. |
| `e2e` | writes a CI `.env`, runs `scripts/e2e_run.sh` | **25 passed.** |
| `security` | gitleaks (full history), Semgrep (SARIF → Security tab), **bandit** (`-r app/`), pip-audit on `poetry export --only main` | gitleaks exit 0 after baselining one historical `SECRET_KEY`; Semgrep 1.157.0 exit 0, no findings; **bandit exit 0**; pip-audit red — see §10. |
| `quality` | **pylint** `--fail-under=9.5`, **radon** `cc`/`mi` report, **hadolint** on the `Dockerfile` | pylint **9.94/10, 15 messages remaining**; radon **average complexity A (2.30)** across 301 blocks with every module's maintainability index rated **A**; hadolint **exit 0** at `failure-threshold: info`. |
| `trivy-repo` | `trivy fs` (locked dependency graph + secret scan, report-only) and `trivy config` (IaC misconfiguration, blocking) | `trivy fs`: **7 HIGH, 0 CRITICAL**, and **0 secrets found**. `trivy config`: **0 misconfigurations** on the Dockerfile. |
| `dependency-review` | **PR-only.** `fail-on-severity: high`; denies `GPL-3.0`, `AGPL-3.0`, `LGPL-3.0` | **NOT VERIFIED —** the action requires a real pull request to diff a manifest against a base commit and cannot be run locally. Verification is the first PR into `develop`. |
| `sonarcloud` | `needs: [test]`. Downloads the coverage artifact and scans — **only when `SONAR_TOKEN` exists**, skipping cleanly otherwise | **NOT VERIFIED —** no SonarCloud account was created; `sonar-project.properties` still carries `CHANGE_ME` placeholders. The skip-cleanly path is by construction (job-level `env` + a step `if` gate), not observed. Enablement steps: `GITHUB_ACTIONS_SETUP.md` → "Enabling SonarCloud". |
| `build` | `needs: [lint, quality, test, migrations, e2e, security, trivy-repo]`. Builds the image, smoke-tests it, generates a **CycloneDX SBOM**, Trivy-scans HIGH/CRITICAL `--ignore-unfixed`, uploads SARIF, **then** a separate failing gate step | Trivy 0.74.0: 6 HIGH — see §10. SBOM generated locally from the built image: **CycloneDX 1.7, 174 components** (173 library + 1 operating-system), ~301 KB. **NOT VERIFIED —** the SBOM has never been uploaded as a workflow artifact. |

### Code scanning needs GHAS on a private repo — gates are unaffected

**Verified on the first real CI run.** This repo is private, so SARIF upload and the Dependency
Graph require GitHub Advanced Security. Consequences, and the limit of them:

- Every scanner below that **BLOCKS still blocks** — enforcement is by exit code, not by upload.
- The **Security tab is unavailable**; SARIF uploads are best-effort and log a visible warning.
- **CodeQL and dependency-review skip cleanly** rather than sitting permanently red.

Re-enable by making the repo public (free) or by setting `ENABLE_CODE_SCANNING=true` after
buying GHAS. Both routes are in `docs/deployment/GITHUB_ACTIONS_SETUP.md` §9.

> **Do not reorder the scanning steps.** Scanners run *before* uploads on purpose. When the
> uploads sat first, a failed upload skipped `bandit`, `pip-audit`, `trivy config` and Checkov —
> four blocking gates that silently never ran. Uploads are last, best-effort, and guarded on the
> SARIF file existing.

### What blocks and what only reports — the deliberate split

Not every scanner gates the build, and which ones do was an explicit decision rather than an
accident of defaults.

| Gate | Mode | Why |
|---|---|---|
| `lint` — black, isort, ruff (incl. C901), mypy | **BLOCKS** | Deterministic, fast, zero false positives on this tree. |
| `quality` — pylint (`--fail-under=9.5`) | **BLOCKS** | Threshold measured, not invented: the tree scores 9.94. |
| `quality` — hadolint | **BLOCKS** | Green today, so it is a free ratchet against Dockerfile erosion. |
| `test` — coverage ≥ 95% | **BLOCKS** | Actual coverage is 98.29%. |
| `migrations`, `e2e` | **BLOCKS** | A broken migration or journey must never reach a deploy. |
| `security` — gitleaks, Semgrep, bandit, pip-audit | **BLOCKS** | All green except pip-audit, whose redness is the point (§10). |
| `trivy config` (IaC) | **BLOCKS** | 0 findings today. |
| `trivy image` (in `build`) | **BLOCKS** | The image is what ships. |
| **CodeQL** | *report-only* | A brand-new rollout has **no triaged baseline**, and `security-and-quality` is broad. Blocking on the first run is how teams end up disabling CodeQL within a fortnight. |
| **`trivy fs`** | *report-only* | **pip-audit already blocks** on the same dependency CVEs. Two blocking gates for one finding set is noise; `trivy fs`'s value is the *wider* view — the lockfile graph including unfixable CVEs, plus secret scanning. |
| **radon** `cc`/`mi` | *report-only* | ruff's C901 is the single complexity **gate**. Two tools counting complexity differently must not both block. |
| **SonarCloud** | *report-only* | Not yet enabled at all, and its verdict is a trend, not a pass/fail on one commit. |

Report-only does **not** mean invisible: every one of them publishes SARIF to the repository
Security tab, where a finding can be triaged and dismissed with a reason.

### SARIF categories are distinct per scan mode

Five scans upload SARIF, each under its own `category`: **`trivy-fs`**, **`trivy-config`**,
**`trivy-image`**, **`semgrep`**, and CodeQL's **`/language:python`**. GitHub keys a code-scanning
result set by category, so sharing one would make each upload silently overwrite the last —
the three Trivy modes in particular find genuinely different things and must stay separate.

### Two findings that were fixed, not ignored

- **bandit** originally reported 3 findings (1 MEDIUM, 2 LOW), *all* false positives: `B105`
  on a dict key `"access_token"` whose value is literally `None` (`login.py`), `B105` on the
  enum member `password_reset` (`enums.py`), and `B104` on `host="0.0.0.0"` in `main.py` —
  which is **required** inside a container, and which compose publishes only to `127.0.0.1`.
  Each carries an inline `# nosec <ID>` annotation **with a written reason at the site**, so a
  new occurrence of the same rule anywhere else still fails. bandit now exits 0.
- **hadolint** originally reported 2 findings. `DL3066` (non-numeric user id) was **fixed**:
  the Dockerfile now uses `USER 1000:1000` instead of `USER appuser`, because a numeric id is
  unambiguous to the host kernel and to runtimes enforcing `runAsNonRoot`, which cannot verify
  a username. Verified after rebuilding: `id` inside the container reports
  `uid=1000(appuser) gid=1000(appuser)`, the app imports OK, and the image's `Config.User` is
  `"1000:1000"`. Only `DL3008` (apt version pinning) is ignored, in `.hadolint.yaml`, with a
  written justification — see below.

### Scope limit: `trivy config` does not cover docker-compose

`trivy config`'s supported misconfiguration targets are **Dockerfile, Kubernetes, Terraform,
CloudFormation and Helm**. It has **no docker-compose scanner**, so `docker-compose.yml` and
`docker-compose.prod.yml` are **not** covered by that gate despite being infrastructure-as-code.
Verified by running it locally: only Dockerfiles appear in its target list. The workflow files
are covered by `actionlint` + `shellcheck` instead; the compose files are covered by review and
by `docker compose config`, and by nothing automated.

### Checkov — what it does and does not cover here

Checkov was added specifically to close the docker-compose gap above. **It cannot close it.**

Checkov 3.3.15 has **no `docker_compose` framework at all** — its frameworks are terraform,
cloudformation, kubernetes, helm, dockerfile, github_actions, ansible, secrets, sast and
friends. Pointed at the compose files under the generic `yaml` framework it emits **no
check_type and no results whatsoever** — not "a few findings", literally no policies apply.
Verified locally against both compose files.

A job that scanned compose here could never fail, which is worse than having no job: it would
read as coverage while providing none. So Checkov is wired to the framework where it *does*
earn its place:

| Target | Result |
|---|---|
| `docker-compose.yml`, `docker-compose.prod.yml` | **No coverage.** No framework supports them. |
| `.github/workflows/**` | **47 checks passed, 1 failed** on first run — real value. |

That one failure was `CKV_GHA_7` against `cd.yml`'s `workflow_dispatch` input, and it is worth
knowing why it is suppressed rather than fixed. The check requires dispatch inputs to be empty,
because an input that influences a build breaks the SLSA guarantee that build output derives
solely from source. Here the input **structurally cannot** do that: `build-and-push` is skipped
entirely when `image_tag` is set, so no build runs; the image reference is assembled from the
trusted `${REGISTRY}`/`${IMAGE_NAME}` with the input supplying only the tag; and the assembled
reference is regex-validated with a tag character class that excludes `/` and `@`. Removing the
input to satisfy the check would delete the manual rollback path and make the system less safe.
The suppression is an inline `#checkov:skip=CKV_GHA_7:` comment in `cd.yml` with that reasoning
written out — never a blanket skip.

Checkov **BLOCKS**, on the same principle as `trivy config`: it is green today, so it is a free
ratchet against regression.

**Net position on compose IaC: `docker-compose.yml` and `docker-compose.prod.yml` are scanned
by no IaC tool.** `trivy config` has no compose scanner and neither does Checkov. They are
covered by review and by `docker compose config` only. If that gap matters, it needs a
compose-specific linter, not another general IaC tool.

### Why `DL3008` is the one ignored hadolint rule

The only apt package this image installs is `tini`. Pinning it (`tini=0.19.0-1`) would be
*more* fragile, not less: Debian's archive keeps only the current version of a package, so the
moment bookworm ships a security update for tini the pinned build stops working outright.
Reproducibility is already handled at a stronger layer — the base image is pinned by
**digest**, which fixes the whole apt package set for that digest. `failure-threshold` is set
to `info`, the strictest setting hadolint offers.

### CI hygiene worth preserving

- The Trivy **SARIF upload** and the **failing gate** are deliberately separate steps, so
  findings always reach the Security tab even when the gate fails. A single step with
  `exit-code: 1` would abort before the upload and hide the detail you need to triage.
- CI Postgres was 15 while production ran 17 — a whole major version tested nowhere. Now
  aligned to **17 everywhere**.

All action versions are pinned to full commit SHAs. **All 18 pins across `ci.yml`, `cd.yml` and
`codeql.yml` were re-verified against the live GitHub API — zero mismatches.**
`permissions: contents: read` by default; `security-events: write` only where SARIF uploads,
and `pull-requests: write` only in `dependency-review`.
A shared composite action lives at `.github/actions/setup-python-poetry/action.yml`.

**Poetry must be 2.x.** `poetry.lock` is lock-version 2.1, which Poetry 1.x cannot read. The
old Dockerfile and CI pinned 1.8.4; that was masked only because `poetry.lock` was gitignored
and the Dockerfile used a `poetry.lock*` glob. Both are fixed — the glob is gone, so a
missing lockfile now fails at `COPY`.

### CodeQL — `.github/workflows/codeql.yml`

> **NOT VERIFIED —** CodeQL has never run. It cannot be executed locally; it needs GitHub
> Actions. Verification is the first push to `main` or `develop`.

A **separate workflow, not a job in `ci.yml`** — and that is the whole point. CodeQL needs a
`schedule:` trigger (`cron: "17 4 * * 1"`, Mondays 04:17 UTC) so the Security tab holds a
*current* baseline: query packs gain advisories continuously, so code that was clean in June
can be flagged in August without a line changing. Putting that cron in `ci.yml` would run the
entire 10-job pipeline weekly — Postgres and Redis service containers, the e2e suite, an image
build — to obtain one analysis.

| Setting | Value |
|---|---|
| Languages | `python` (no build step — CodeQL uses its `none` build mode and extracts from source) |
| Query suite | `security-and-quality` — the security suite **plus** maintainability and correctness queries |
| Timeout | 30 minutes |
| Triggers | push + PR on `main`/`develop`, plus the weekly cron |
| Gating | **REPORT-ONLY.** Findings land in the Security tab and on PR diffs; the job does not fail the build. |

**It is not redundant with Semgrep.** Semgrep is syntactic pattern matching — fast, and good at
"this shape of code is wrong". It reasons about one place at a time, so it cannot see that an
unvalidated path parameter three functions away reaches an `open()`. CodeQL builds a relational
database of the code and runs interprocedural dataflow/taint queries over it, following a value
from source to sink across function boundaries and reporting the path. Semgrep takes seconds and
blocks; CodeQL takes minutes and reports.

**To promote it to blocking later**, once the baseline is triaged: turn on branch protection's
code-scanning requirement for the `/language:python` category.

### Dependabot — `.github/dependabot.yml`

> **NOT VERIFIED —** Dependabot has never run.

This repo pins GitHub Actions to full commit SHAs and the Docker base image to a digest. That
is the correct posture — but **pinning without a mechanism to move the pin is strictly worse
than not pinning**: within months you are running a base image with months of unpatched CVEs,
confidently and reproducibly. Dependabot is what makes the pinning sustainable: it opens a PR
that moves the pin, CI proves the move is safe, and a human merges it.

| Ecosystem | Schedule | Grouping |
|---|---|---|
| `pip` (reads `poetry.lock`, now tracked) | weekly, Mondays 05:00 UTC | `dev-tooling` (all development deps, one PR) and `production-minor` (minor + patch). **Major bumps are deliberately ungrouped** — each gets its own PR because each needs real review. |
| `github-actions` | weekly | one grouped PR; this is what moves the SHA pins. |
| `docker` | weekly | moves the base-image **digest** — the mechanism that keeps OS-package findings at zero. |

> **NOT VERIFIED — flag this one specifically.** The base-image digest lives in an
> `ARG PYTHON_IMAGE=…` default rather than a bare `FROM` literal. Dependabot's Docker parser
> handles ARG-based `FROM` in most cases but is **not guaranteed to**. If no digest PRs appear
> within two weeks of enabling Dependabot, refresh the digest by hand and treat this row as
> unverified until a PR is actually observed.

### Reproducing the gates locally

Three Makefile targets mirror what CI runs, so a red pipeline can be debugged without pushing
commits and waiting. All three skip `.worktrees` and `htmlcov` (gitignored worktrees hold
*other* branches' files, which a CI checkout never contains).

| Target | What it runs | Observed |
|---|---|---|
| `make quality` | pylint (gate 9.5) → radon `cc`/`mi` report → hadolint | **exit 0** |
| `make scan` | six stages: bandit → Semgrep → gitleaks → `trivy config` → `trivy fs` → `trivy image`, each labelled BLOCKS or REPORT ONLY exactly as in CI | bandit PASS, Semgrep PASS, gitleaks "no leaks found", `trivy config` PASS, `trivy fs` reports **7 HIGH** and continues (report-only), `trivy image` **6 HIGH → exit 1** (blocking, as designed) |
| `make sbom` | builds the image and writes `sbom.cdx.json` (CycloneDX, via Trivy) | 174 components |

### CD — `.github/workflows/cd.yml`

Triggers: push to `main`, tags `v*.*.*`, and `workflow_dispatch` with an optional `image_tag`
input (the manual rollback path — see `ROLLBACK.md`).

```
build-and-push (skipped when image_tag is supplied)
  └─ GHCR push, tags sha-<short> / semver / latest, provenance + sbom
  └─ resolve the immutable DIGEST
       ↓
deploy   environment: production   concurrency: cd-production (cancel-in-progress: FALSE)
  1. record the previous image from the RUNNING container (compose ps -q api)
  2. docker login ghcr → docker pull
  3. pin API_IMAGE in .env.production
  4. run migrations one-shot, --exit-code-from migrate
  5. compose up -d
  6. poll /api/v1/health/ready for up to ~120s
  ── ERR trap on any failure: restore previous API_IMAGE, restart,
     re-verify health, dump last 80 api log lines, exit 1
```

Design points that matter:

- **Deploy by digest, not tag.** A tag is a mutable pointer; a digest is content-addressed, so
  what the VPS pulls is byte-identical to what was built and scanned.
- **The previous image is read from the running container**, not from `.env.production`. If a
  prior deploy failed after editing the file but before restarting, the file is a lie and the
  container is the truth. It resolves via `compose ps -q api` rather than a hardcoded
  container name, so renaming a service cannot silently break rollback.
- **`concurrency: cancel-in-progress: false`** — cancelling a half-finished deploy leaves the
  VPS in an unknown state, which is worse than queueing.
- **`environment: production`** gives you the manual approval gate (configure required
  reviewers), the deploy history, and per-environment secrets.
- `docker image prune -af --filter "until=72h"` at the end reclaims disk but deliberately
  keeps recent images, so a manual rollback to yesterday's build still has a local image.

### House-default deviation: GHCR pull, not scp-tarball

This project deploys by **GHCR pull**, which differs from the usual scp-tarball default.

| | GHCR pull (this project) | scp tarball |
|---|---|---|
| VPS needs GHCR auth | **Yes** — `GHCR_PULL_TOKEN` on the host | No |
| Network dependency at deploy time | **ghcr.io must be reachable** | GitHub runner → VPS only |
| Rollback source | GHCR + local image cache (72h prune window) | previous tarball on disk |
| Layer reuse on pull | Yes — only changed layers transfer | Full image every time |

The tradeoff is deliberate: faster deploys and a real registry, at the cost of a runtime
dependency on ghcr.io and a PAT living on the VPS. If ghcr.io is down, you cannot deploy —
but you *can* still roll back to any image still in the local cache.

### Image provenance — and why there is no cosign signature

`cd.yml`'s `build-and-push` step sets **`provenance: true`** and **`sbom: true`** on
`docker/build-push-action`, so BuildKit attaches a SLSA provenance attestation (what built this,
from which commit, with which inputs) and an SBOM attestation to the pushed GHCR manifest.

```bash
docker buildx imagetools inspect ghcr.io/innocent98/cofoundaz-api:latest \
  --format '{{ json .Provenance }}'
```

> **NOT VERIFIED —** no GHCR push has been performed, so no attestation has been inspected.

**cosign keyless signing was deliberately not added.** CD resolves the digest from the build it
just ran, and the VPS pulls *that digest*. A digest is content-addressed: the registry cannot
serve different bytes under it — which is precisely the tampering a signature would catch.
Without an admission controller or a `cosign verify` gate that can actually **refuse** a deploy,
a signature would be ceremony: a step that produces an artifact nothing checks.

Revisit this if either changes: the images are consumed by a third party, or a policy engine
(Kyverno, Sigstore policy-controller, a `cosign verify` gate in the deploy script) is introduced
that can turn a bad signature into a stopped deploy.

---

## 7. Health endpoints

| Endpoint | Purpose | Response |
|---|---|---|
| `GET /health` | Liveness, static, no dependencies (`app/main.py`). Used by the image `HEALTHCHECK`. | `200 {"status":"healthy"}` |
| `GET /api/v1/health` | Liveness | `200 {"status":"ok","timestamp":…}` |
| `GET /api/v1/health/ready` | **Readiness.** Checks Postgres *and* Redis. The CD deploy gate polls this. | `200` when both answer, `503` otherwise |

Readiness body shape (`app/api/v1/endpoints/health.py`):

```json
{
  "status": "ready",
  "timestamp": "…",
  "revision": "b0093a1",
  "checks": {
    "database": {"status": "ok", "reason": null},
    "redis":    {"status": "ok", "reason": null}
  }
}
```

`revision` comes from `APP_GIT_SHA`, baked in at build time — so you can confirm which commit
is actually serving without shelling into the box.

**Verified failure behaviour:** with the stack up, readiness returned `200` with revision
`b0093a1`. After `docker compose stop redis`, **liveness stayed HTTP 200** and readiness
returned **HTTP 503** with `checks.redis.status="error"` and `checks.database.status="ok"`.
The 503 body was confirmed to leak no DSN, credentials, or port.

That split is the design: liveness must not fail because a dependency blipped, or a
recoverable database hiccup becomes a full outage. Readiness is what the deploy gate and the
uptime monitor read.

---

## 8. Migrations

- `migrate` is a one-shot service running `alembic upgrade head`, with `restart: "no"`.
  A one-shot must not restart: alembic is not idempotent under concurrent runs, and a restart
  loop on a failed migration would hammer the database.
- `api` has `depends_on: migrate: condition: service_completed_successfully`. The API never
  starts against an un-migrated schema. `service_started` would race.
- `migrate` has **no `build:` block** on purpose. Two services declaring a build for the same
  image tag makes buildx fail with `image … already exists` — verified failure, then fixed.
  The `api` service owns the build; `migrate` consumes the resulting tag.
- All 7 revisions implement **real downgrade bodies** (`op.drop_table` / `op.drop_index`, not
  `pass`) — verified. That is what makes the rollback procedure in `ROLLBACK.md` trustworthy.

Run migrations by hand against the prod stack:

```bash
make prod-migrate
# = docker compose -f docker-compose.prod.yml --env-file .env.production \
#     up --no-build --exit-code-from migrate migrate
```

Rollback procedure: see **[ROLLBACK.md](./ROLLBACK.md)**.

---

## 9. Backups and restore

> **NOT VERIFIED — this entire section.** No backup or restore was executed. It is written
> from knowledge of the stack, not observation. Verification: run a full `pg_dump` → drop into
> a scratch database → `pg_restore` → row-count comparison, on the real host, before you rely
> on it. Until that drill runs, treat these as untested procedures.

### The scram-sha-256 consequence — read this first

Postgres is configured with `scram-sha-256` for **both** host and local auth
(`POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256 --auth-local=scram-sha-256"` plus
`POSTGRES_HOST_AUTH_METHOD: scram-sha-256`).

**Verified consequence:** `docker compose exec db psql -U <user>` **prompts for a password**.
It does not use trust auth. Any non-interactive script — a backup cron especially — must pass
`PGPASSWORD`, or it will hang waiting on a prompt that nobody answers.

### Backup

```bash
cd "${DEPLOY_PATH}"
set -a; . ./.env.production; set +a

docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc \
  > "backups/cofoundaz-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

`-Fc` (custom format) is what makes selective `pg_restore` possible. `-T` on `exec` disables
TTY allocation, which a cron job needs.

### Restore

**Take a safety dump of the current state first.** Restoring the wrong backup with no
pre-restore snapshot is unrecoverable.

```bash
# 1. safety snapshot (the command above)
# 2. stop the API so nothing writes mid-restore
docker compose -f docker-compose.prod.yml --env-file .env.production stop api
# 3. restore
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
  pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists \
  < backups/<chosen>.dump
# 4. bring the API back and confirm readiness
docker compose -f docker-compose.prod.yml --env-file .env.production up -d api
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

### Retention and drills

| Tier | Retention |
|---|---|
| Local on the VPS (`${DEPLOY_PATH}/backups`) | 7 days |
| Off-host / cloud | 30+ days — **the source of truth for restore** |
| Monthly archive | 1 year |

Run a **restore drill quarterly** into a staging-clone database. Untested backups are wishes.

Also back up, outside the database: **`MFA_ENCRYPTION_KEY`**. A database restore without that
key leaves every MFA-enrolled user permanently locked out — the dump is useless without it.

> **NOT VERIFIED —** no cron, no off-host upload, and no cloud provider is configured in this
> repo. There is no backup script in `scripts/` (it contains only `e2e_run.sh`). Wiring the
> cron and the off-host copy is outstanding work, not a documented existing capability.

---

## 10. Known security debt — read before the first CI run

**This is the one thing that will make CI red on its first run.** It is not a
misconfiguration. It is a real, deliberate, unsuppressed signal — now roughly half the size it
was, after the approved `python-multipart` bump below.

**Nothing else is newly red.** Every gate added in the scanning wave — pylint, hadolint,
bandit, `trivy config` — is **green on the current tree**. The dependency CVE debt below is
unchanged and is the only blocker.

Nothing is suppressed: `.trivyignore` and `.github/security/pip-audit-ignores.txt` contain
**only comments** explaining the policy.

### `python-multipart` 0.0.20 → 0.0.32 — SHIPPED, and what it actually fixed

Approved and landed. `pyproject.toml` now pins `python-multipart = ">=0.0.32,<0.1.0"` — an
explicit range rather than a caret, because Poetry's caret on a `0.0.x` version pins it
*exactly* (`^0.0.32` means `>=0.0.32,<0.0.33`) and would block the next security patch.

**Only that one package moved.** Verified by diffing the lockfile before and after: 122
packages before, 122 after, exactly one version change. `starlette`, `fastapi` and `anyio` were
**not** dragged along.

| Scanner | Before | After | Fixed |
|---|---|---|---|
| `trivy image` (HIGH/CRITICAL, fixed-only — **the CI gate**) | **6** | **3** | 3 |
| `pip-audit` (locked prod deps) | **16** | **10** | 6 |
| `trivy fs` (lockfile view, incl. unfixed) | **7** | **4** | 3 |

All three remaining `trivy image` findings are `starlette`; the 10 pip-audit advisories are
`starlette` (9) + `ecdsa` (1).

**Why the counts differ:** pip-audit reports one advisory per *fix version*, so a single defect
fixed across several releases counts several times — hence 6 advisories cleared for 3 CVEs.

**It is on a live request path, and was tested as such.** Note the endpoint is *not* login —
`POST /api/v1/auth/login` takes a Pydantic `LoginRequest` (a JSON body), not
`OAuth2PasswordRequestForm`, so it never touches multipart. The real consumer is
**`POST /api/v1/onboarding/logo`** (`file: UploadFile = File(...)`), a genuine
`multipart/form-data` file upload — which makes the bump *more* relevant than a form-login
would have, since two of the three CVEs are path-traversal-on-file-write (CVE-2026-24486) and
DoS via multipart headers (CVE-2026-42561). Exercised after the bump by
`tests/api/onboarding/test_logo.py` (4 passed, including oversize and non-image rejection) and
by the live `test_full_onboarding_journey` e2e, which posts a real multipart upload. All 11
auth e2e journeys also pass.

### What remains: starlette (3 HIGH) — verified locally

`trivy image --severity HIGH,CRITICAL --ignore-unfixed` still exits **1**. Zero OS-package
findings, which validates the pinned base digest.

| Package | Version | CVE | Fixed in |
|---|---|---|---|
| starlette | 0.46.2 | CVE-2025-62727 | 0.49.1 |
| starlette | 0.46.2 | CVE-2026-48818 | 1.1.0 |
| starlette | 0.46.2 | CVE-2026-54283 | 1.3.1 |

A resolution probe confirmed starlette stays pinned at 0.46.2 **even with fastapi 0.141.1**, so
these are not fixed by moving fastapi forward. starlette 1.x is a major version and not a
drop-in. This needs a real compatibility investigation, not a version bump.

### And `ecdsa` — no fix exists

`trivy fs` scans `poetry.lock` rather than the image and is **report-only** (pip-audit already
blocks on the same set). It surfaces one finding the image gate cannot:

| Package | CVE | Fix |
|---|---|---|
| `ecdsa 0.19.2` | **CVE-2024-23342** (Minerva timing attack) — pip-audit calls it `PYSEC-2026-1325` | **None available.** Arrives transitively via `python-jose`. |

`trivy image` hides it behind `--ignore-unfixed`. That flag is correct for a *blocking* gate — a
CVE with no released fix cannot be actioned by upgrading, and blocking every deploy on it just
teaches people to bypass the gate — but it means the unfixable set is invisible unless something
else reports it. That is exactly what `trivy fs` is for here.

The real remediation is migrating **python-jose → PyJWT**. There is no version to bump to.

Trivy's **secret scanner** runs in the same job over the working tree: **0 secrets found.**

### Remaining options — pick deliberately

| Option | What it buys | Cost |
|---|---|---|
| **(a) Investigate starlette** | Clears the last 3 HIGH and the CI gate goes green | Unknown — real compatibility work against fastapi; starlette 1.x is a major |
| **(b) Migrate python-jose → PyJWT** | Clears the unfixable `ecdsa` advisory | A real auth-code change; needs full re-verification of token issue/verify |
| **(c) Time-boxed documented entries** in `.trivyignore` / `pip-audit-ignores.txt`, **with review dates** | Unblocks CI now | Ships a known-vulnerable image; needs a written justification per entry |

**Do not blanket-suppress.** Both ignore files carry a rule requiring a reason and a review date
per entry, precisely so silencing a finding is a deliberate diff someone has to approve. The
gate failing is the correct signal — it is what makes it real rather than decorative.

---

## 11. Log access

Every service uses `json-file` with `max-size: 10m`, `max-file: 3` — **all four services**.
That caps container logs at roughly 120MB. Without it, logs grow unbounded and eventually
fill the disk, which takes down Postgres and the host, not just the noisy container.

```bash
make prod-logs                                    # tail everything
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f --tail=100 api
docker compose -f docker-compose.prod.yml --env-file .env.production logs db | grep duration   # slow queries (>1s)
```

**Production sets `LOG_FILE_PATH=` (empty).** `app/core/logger.py` makes the loguru file sink
conditional on that setting (`app/core/config.py` defaults it to `logs/app.log` for local
use). Previously it hardcoded a write to `logs/app.log`, which crashed the hardened
read-only container with `PermissionError` **and** wrote logs invisible to `docker logs`,
bypassing json-file rotation entirely. Leave it empty in production.

`/app/logs` still exists in the image as a safety net owned by `appuser`, so that if the env
var is ever lost the app starts rather than crash-looping.

---

## 12. Incident runbook

### The container will not start

**First check the exit reason:**

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production ps -a
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail=100 api
```

| Symptom | Cause | Fix |
|---|---|---|
| Exits immediately with a Pydantic `ValidationError` | A required env var is missing — `SECRET_KEY`, `DATABASE_URL`, `FIRST_SUPERUSER_EMAIL`, `FIRST_SUPERUSER_PASSWORD`. Pydantic Settings validates at **import time**, so the container exits with a clear error rather than starting broken. This is intended fail-fast. | Fix `.env.production`, `up -d` again. |
| `PermissionError` writing a log file | `LOG_FILE_PATH` is set to a path. The rootfs is read-only. | Set `LOG_FILE_PATH=` empty. |
| `EROFS` / read-only filesystem on upload | Something is writing outside `/tmp` or `app_storage`. | `LOCAL_STORAGE_DIR` must stay under `./var/storage`. |
| `api` never starts, no logs | `migrate` did not exit 0 — `depends_on` is correctly blocking. | See the next runbook. |
| Marked `unhealthy` | The image `HEALTHCHECK` hits `/health`. `start_period` is 40s in compose. | Check the app actually booted before assuming the check is wrong. |

### A migration failed mid-deploy

The CD ERR trap has already restored the **previous API image** and restarted it — but
**the database may be partially migrated**. The app rollback and the schema rollback are
separate concerns.

1. Establish where the schema actually is:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     run --rm migrate alembic current
   ```
2. Read the `migrate` logs to see which revision failed and whether it was transactional.
3. **Back up before touching the schema** (§9), then follow the migration rollback path in
   `ROLLBACK.md`.

Do **not** re-run the deploy hoping it resolves itself. Alembic is not idempotent under a
partially-applied revision.

### Database connection exhaustion

Symptom: `FATAL: sorry, too many clients already`.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
  psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
```

Re-check the §5 arithmetic. Almost always one of:

- `WEB_CONCURRENCY` was raised without raising `POSTGRES_MAX_CONNECTIONS`.
- `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` were raised without the same check.
- Idle-in-transaction sessions are leaking — find them in `pg_stat_activity` and fix the
  code path, not the limit.

Immediate mitigation: lower `WEB_CONCURRENCY` in `.env.production` and
`up -d --no-build api`. Then fix the arithmetic properly.

### Disk full

Most likely culprits, in order:

```bash
df -h
docker system df                     # images, volumes, build cache
du -sh ${DEPLOY_PATH}/backups        # local backup tier
```

1. **Old images.** CD prunes with `until=72h`, but a stalled pipeline leaves them. Safe to
   run `docker image prune -af --filter "until=72h"` by hand — it keeps recent images so a
   manual rollback still works.
2. **Backups.** Enforce the 7-day local tier.
3. **Container logs** should be bounded at ~120MB by the rotation config. If they are not,
   something has lost the `logging:` block — check `make prod-config`.
4. **Postgres WAL.** If it has grown, look for a stuck replication slot or a long-running
   transaction blocking checkpoints.

**Never** free space with `docker system prune --volumes` or `docker compose down -v` — both
delete the Postgres data volume.

### Emergency: is the running image what I think it is?

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health/ready | jq .revision
docker inspect --format '{{.Config.Image}}' \
  "$(docker compose -f docker-compose.prod.yml --env-file .env.production ps -q api)"
```

---

## 13. The compose project name — do not change it

```yaml
name: cofoundaz-api-prod
```

This is **not cosmetic**, and it was learned the hard way. The project name prefixes every
volume and network. When the prod stack shared a name with the dev stack (which defaults to
the directory name, `cofoundaz-api`), running
`docker compose -f docker-compose.prod.yml down -v` **deleted the dev stack's Postgres
volume**. After the rename, dev volumes were verified to survive a prod `down -v`.

Keep the two names different forever.

Relatedly, `make prod-down` deliberately has **no `-v`**. Named volumes hold the Postgres data
directory; `down -v` on a production host is unrecoverable data loss. Remove volumes by hand,
on purpose, after taking a backup.

---

## 14. Scaling

This stack is sized for one VPS. In rough order of what to reach for:

**1. Vertical first (no architecture change).** Every knob is an env var. Resize the VPS,
re-derive §5, update `.env.production`, `up -d`. This is the cheapest option by a wide margin
and covers a lot of growth.

**2. Raise `WEB_CONCURRENCY`.** Bounded by CPU *and* by the connection arithmetic. Raise
`POSTGRES_MAX_CONNECTIONS` in the same change, or you trade a CPU bottleneck for
`too many clients already`.

**3. Add a connection pooler.** Once `WEB_CONCURRENCY × 10` starts crowding
`max_connections`, pgbouncer in transaction-pooling mode in front of Postgres decouples the
two numbers. This is the first real architecture change and needs its own design pass.

**4. Move Postgres off the box.** A managed Postgres removes the largest memory consumer and
gives you PITR and automated backups — which also retires most of §9. Point `DATABASE_URL`
at it and drop the `db` service.

**5. Multiple API hosts behind nginx.** Requires first: moving rate limiting out of slowapi's
per-process in-memory store (today it is per API *process*, so the effective global limit is
roughly `RATE_LIMIT_PER_MINUTE × WEB_CONCURRENCY` — treat it as defence in depth behind
nginx's own `limit_req`, not an exact quota), and moving `LOCAL_STORAGE_DIR` off a local
volume to object storage.

> **NOT VERIFIED —** no load testing was performed, so there is no measured throughput number
> to say *when* to move between these steps. Verification: a load test on the real host with
> `docker stats` and `pg_stat_activity` sampled throughout, which would give real thresholds.

---

## Related documents

- **[GITHUB_ACTIONS_SETUP.md](./GITHUB_ACTIONS_SETUP.md)** — every secret and variable, how to
  generate it, and its scope.
- **[ROLLBACK.md](./ROLLBACK.md)** — automatic rollback, manual rollback, and the migration
  rollback path.
