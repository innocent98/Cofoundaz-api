# Deployment Guide — cofoundaz-api

> **Type:** deployment reference · **Stack:** FastAPI + Postgres 17 + Redis 7 on a single
> Docker-Compose VPS behind nginx · **Last verified:** 2026-08-28

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
| Environments | **Two** stacks — `staging` and `production` — running the *same* image digest. They differ only in the `.env` each receives and in the per-environment GitHub secrets (`DEPLOY_PATH`, `VPS_*`, `ENV_ENCRYPTION_KEY`, `GHCR_PULL_*`). No server path appears anywhere in this repository. See §2. |

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

## 2. Environments, the deploy path, and VPS prerequisites

### Two environments, one image

There are **two** VPS stacks — **staging** and **production**. Both run
`docker-compose.prod.yml`, both pull the **same image digest**, and they differ in exactly two
things: the `.env` each receives, and the per-environment GitHub secrets that tell CD where to
put it.

**The repository does not know either server path.** `DEPLOY_PATH` is a **secret on the GitHub
Environment**, so the `staging` environment resolves one path and `production` another.
`cd.yml` and `.github/actions/deploy-stack/action.yml` read `${{ secrets.DEPLOY_PATH }}` and
nothing else.

That is deliberate, and it is a correction. Earlier versions of this guide named
`/opt/cofoundaz-api` as *the* deploy directory. **That path never existed on any host** — which
is precisely the failure mode the per-environment secret removes. A path written into the repo
drifts silently the moment a server is built differently, and nothing fails until a deploy does.
If you need to know where a stack lives, read the secret, not this document.

| Environment | `DEPLOY_PATH` — the *value that environment's secret currently holds* |
|---|---|
| `staging` | `/opt/cofoundaz-staging` |
| `production` | `/opt/cofoundaz` |

Those are values of a secret, **not** paths this repo hardcodes anywhere. Change the secret and
the deploy follows.

### The env file on the server is `.env` — not `.env.production`

This is the single most confusable part of the setup, so read the whole table.

| File | Where it lives | In git? | What it is |
|---|---|---|---|
| `.env` | `${DEPLOY_PATH}/.env` on the VPS | **No** — gitignored | **The deployed environment.** `docker-compose.prod.yml` reads it. CD rewrites it on every deploy. |
| `.env.staging` / `.env.production` | a developer machine only | **No** — gitignored | Plaintext working copies. Two names so one developer can hold both environments at once without collision. |
| `.env.staging.enc` / `.env.production.enc` | repo root | **Yes — committed** | AES-256-CBC ciphertext of the two files above. |
| `.env.key` | a developer machine only | **No** — gitignored | The AES key. Committing it beside the `.enc` files would defeat the entire exercise. |
| `.env.example`, `.env.production.example` | repo root | **Yes** | Templates. Placeholders only. |

CD's flow, per environment (`.github/actions/deploy-stack/action.yml`):
`.env.<env>.enc` → decrypted **on the runner** with that environment's `ENV_ENCRYPTION_KEY`
→ written out as **`.env`** → `chmod 600` → scp'd to `${DEPLOY_PATH}` → **deleted from the
runner** in an `if: always()` step.

The decrypt step refuses to continue if the ciphertext is missing, if the key is unset, if
decryption fails, or if the plaintext contains fewer than 5 variables — a truncated environment
is never deployed. It **warns** (rather than fails) when `CHANGE_ME` survives into the plaintext.

**Verified** on 2026-08-28: `git add --dry-run` proves `.env`, `.env.staging`, `.env.production`
and `.env.key` are **blocked** by `.gitignore`, while `.env.staging.enc`, `.env.production.enc`,
`.env.example` and `.env.production.example` are **addable**. That ignore rule is what keeps a
production credential file out of the repository — do not weaken it.

The full encrypt / decrypt / verify / rotate workflow lives in
**[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)**, not here.

### `STACK_ENV_FILE` — why local prod-stack testing uses a different filename

`docker-compose.prod.yml` declares `env_file: - ${STACK_ENV_FILE:-.env}`.

- **On the server**, `STACK_ENV_FILE` is never set, so it resolves to **`.env`** — the file CD
  just shipped. This is the only value used in a real deploy. **Never set it on the server.**
- **On a laptop**, `.env` is already taken by the **dev** stack (`docker-compose.yml`). So
  `make prod-*` runs
  `STACK_ENV_FILE=.env.production docker compose -f docker-compose.prod.yml --env-file .env.production …`,
  and therefore never reads or overwrites the dev `.env`.

Get a local `.env.production` with `make env-decrypt-production`
(`./scripts/env.sh decrypt production`).

**Verified** on 2026-08-28: `docker compose config` parses cleanly in **both** shapes — the
server shape (`--env-file .env`) and the local shape
(`STACK_ENV_FILE=.env.production --env-file .env.production`).

### Env encryption tooling, in one line

`scripts/env.sh` has six subcommands — `generate-key`, `encrypt <env>`, `decrypt <env>`,
`verify <env>`, `rotate <env>`, `diff` — wrapped by `make env-*` targets. Key discovery is
three-tier: `$ENV_ENCRYPTION_KEY` → `.env.key` → interactive prompt. Crypto is AES-256-CBC with
PBKDF2 at 100,000 iterations.

**Verified** on 2026-08-28, a full round-trip on fake data in a scratch directory:
encrypt → decrypt is byte-identical; `verify` passes; a **wrong key exits 1 and leaves no
partial file**; `rotate` invalidates the old key; `diff` masks values; and the `CHANGE_ME`
warning fires. `shellcheck` is clean on `scripts/env.sh`.

### VPS provisioning and prerequisites

> **NOT VERIFIED —** this entire subsection. No VPS was provisioned, and **no staging box
> existed at the time of this verification pass**. Verification is: run these steps on a fresh
> host and confirm `docker compose version` ≥ v2 and `make prod-config` renders without
> warnings.

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
| Deploy directory | `${DEPLOY_PATH}` — supplied by the **per-environment** GitHub secret, one value for `staging` and another for `production`. Owned by the deploy user. CD ships `docker-compose.prod.yml` and `.env` into it on every deploy. |

### Files in the deploy directory

```
${DEPLOY_PATH}/
├── docker-compose.prod.yml     # scp'd by CD on every deploy, from the commit being deployed
└── .env                        # scp'd by CD, chmod 600, NEVER in git
```

**CD writes both files on every deploy.** They do not need to be placed by hand for CD to work
— only the directory itself, owned by the deploy user, has to exist.

`.gitignore` denies `.env` and `.env.*` and re-allows only `.env.example`,
`.env.production.example`, `.env.staging.enc` and `.env.production.enc` — verified above with
`git add --dry-run`.

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

Do this **for staging first, then production.** Everything below is per environment; the only
things that differ are the `DEPLOY_PATH` and the `.env.<env>` you fill in.

> **NOT VERIFIED —** steps 1–6 were never executed against a VPS, and no staging box existed at
> the time of this verification pass. The compose behaviour in step 5 (ordering, migrations,
> readiness) *was* verified locally; the SSH/host parts were not.

**1. Prepare the deploy directory.** Use that environment's `DEPLOY_PATH` value — do not copy a
path out of this document.

```bash
ssh deploy@vps
DEPLOY_PATH=<the value of that environment's DEPLOY_PATH secret>
sudo mkdir -p "${DEPLOY_PATH}" && sudo chown deploy:deploy "${DEPLOY_PATH}"
```

**2. Nothing to copy by hand.** CD scp's `docker-compose.prod.yml` and `.env` into
`${DEPLOY_PATH}` on every deploy. The steps below describe the manual first bring-up; from then
on the pipeline owns both files.

**3. Fill in the environment locally, then encrypt it.** Work on `.env.staging` or
`.env.production` **on your machine** — never on the server.

```bash
cp .env.production.example .env.production      # or .env.staging
chmod 600 .env.production
# fill in every CHANGE_ME, then:
make env-encrypt-production                     # -> .env.production.enc  (commit the .enc)
```

Commit **only** the `.enc`. Full workflow: **[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)**.

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

**5. Bring the stack up.** On the server the env file is `.env` — get it there either by
running a CD deploy, or, for a manual first bring-up, by decrypting locally and scp'ing the
result to `${DEPLOY_PATH}/.env`.

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d
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

Every one of these is an env var in the environment file — `.env` on the server, `.env.<env>` on your machine. No compose edit required. Change it locally, re-encrypt, commit,
redeploy: a hand edit on the server is overwritten by the next deploy.

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

> **NOT VERIFIED — the current shapes.** `ci.yml` and `cd.yml` have executed on GitHub in
> *earlier* shapes (that is where the GHAS constraint and the lowercase-image-name bug were
> found), but **`cd.yml` has never run as the three-job staging-gated pipeline described below**,
> and `codeql.yml` has never run at all. `actionlint` exits 0 on every workflow plus the
> composite actions (re-run 2026-08-28), with `shellcheck 0.11.0` integration — confirmed live
> by feeding actionlint a deliberately-bad script and seeing SC2086 reported. Static validation
> is not execution. The next push is the verification run.

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
| `test` | `postgres:17-alpine` + `redis:7-alpine` services, `pytest --cov-fail-under=95`, uploads `coverage.xml` as an artifact | **406 passed, 98.25% coverage** (re-verified 2026-08-28 after `main` merged Roadmap Slice 3 and Today's Mission). |
| `migrations` | exactly-one-head check, upgrade from empty, `alembic check` (model/schema drift), then `downgrade base` + `upgrade head` | Exactly 1 head, 0 drift (re-verified 2026-08-28). |
| `e2e` | writes a CI `.env`, runs `scripts/e2e_run.sh` against a locally booted server | **The full 27-test suite.** This is where the 14 mailbox-dependent journeys are covered; CD's live staging gate runs only the other 13 — see the CD section below. |
| `security` | gitleaks (full history), Semgrep (SARIF → Security tab), **bandit** (`-r app/`), pip-audit on `poetry export --only main` | gitleaks exit 0 after baselining one historical `SECRET_KEY`; Semgrep 1.157.0 exit 0, no findings; **bandit exit 0**; pip-audit red on **1** remaining advisory (`ecdsa`, no fix available) — see §10. |
| `quality` | **pylint** `--fail-under=9.5`, **radon** `cc`/`mi` report, **hadolint** on the `Dockerfile` | pylint **9.94/10, 15 messages remaining**; radon **average complexity A (2.30)** across 301 blocks with every module's maintainability index rated **A**; hadolint **exit 0** at `failure-threshold: info`. |
| `trivy-repo` | `trivy fs` (locked dependency graph + secret scan, report-only) and `trivy config` (IaC misconfiguration, blocking) | `trivy fs`: **1 HIGH, 0 CRITICAL** (the unfixable `ecdsa` advisory), and **0 secrets found** (re-verified 2026-08-28, after the starlette upgrade cleared the rest). `trivy config`: **0 misconfigurations** on the Dockerfile. |
| `dependency-review` | **PR-only.** `fail-on-severity: high`; denies `GPL-3.0`, `AGPL-3.0`, `LGPL-3.0` | **NOT VERIFIED —** the action requires a real pull request to diff a manifest against a base commit and cannot be run locally. Verification is the first PR into `develop`. |
| `sonarcloud` | `needs: [test]`. Downloads the coverage artifact and scans — **only when `SONAR_TOKEN` exists**, skipping cleanly otherwise | **NOT VERIFIED —** no SonarCloud account was created; `sonar-project.properties` still carries `CHANGE_ME` placeholders. The skip-cleanly path is by construction (job-level `env` + a step `if` gate), not observed. Enablement steps: `GITHUB_ACTIONS_SETUP.md` → "Enabling SonarCloud". |
| `build` | `needs: [lint, quality, test, migrations, e2e, security, trivy-repo]`. Builds the image, smoke-tests it, generates a **CycloneDX SBOM**, Trivy-scans HIGH/CRITICAL `--ignore-unfixed`, uploads SARIF, **then** a separate failing gate step | Trivy 0.74.0: **0 findings** as of 2026-08-28 — the starlette upgrade cleared all of them; see §10. SBOM generated locally from the built image: **CycloneDX 1.7, 174 components** (173 library + 1 operating-system), ~301 KB. **NOT VERIFIED —** the SBOM has never been uploaded as a workflow artifact. |

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

> **NOT VERIFIED — the current shape.** CI and CD have both executed on GitHub before (that is
> where the GHAS constraint below and the lowercase-image-name bug were found), but **CD has
> never run in its current three-job shape**, and no part of it has ever touched a real VPS: no
> SSH deploy, no scp, no GHCR pull from a server. **No staging box existed at the time of this
> verification pass.** `actionlint` exits 0 on all workflows and `shellcheck` is clean on
> `scripts/env.sh` (2026-08-28). Static validation is not execution.

Triggers: push to `main`, tags `v*.*.*`, and `workflow_dispatch` with an optional `image_tag`
input (the manual rollback path — see `ROLLBACK.md`). One `concurrency` group covers the whole
pipeline: `cd-pipeline`, `cancel-in-progress: false`.

**CD is a three-stage gated pipeline. Production is unreachable except through a green live
staging E2E run.**

```
build-and-push  ──►  staging-deploy  ──►  staging-e2e  ──►  production-deploy
(skipped when        environment:         environment:      environment:
 image_tag is         staging              staging           production
 supplied)                                                   (manual approval)
```

| Job | The condition that gates it |
|---|---|
| `build-and-push` | `if: github.event.inputs.image_tag == ''` — skipped entirely on the rollback path. Pushes to GHCR, then resolves the **immutable digest**. |
| `staging-deploy` | `if: always() && needs.build-and-push.result != 'failure' && … != 'cancelled'`. `always()` is what keeps the rollback path alive when the build is skipped; the explicit checks stop a **failed** build from ever reaching a VPS. |
| `staging-e2e` | `if: needs.staging-deploy.result == 'success'` |
| `production-deploy` | `if: needs.staging-deploy.result == 'success' && needs.staging-e2e.result == 'success'` — **both conditions explicit**, so a skipped or cancelled gate can never be mistaken for a pass. |

**Production deploys the same digest staging proved.** `production-deploy` takes its
`image_ref` from `needs.staging-deploy.outputs.image_ref`. It does **not** rebuild. Staging and
production therefore run byte-identical artifacts, and the only difference between them is which
`.env` each received.

**Both deploys call one composite action**, `.github/actions/deploy-stack`. Two copies of ~100
lines of deploy shell would drift, and the copy that drifts is the one you find out about during
an incident.

**`production` keeps its manual approval gate** via the `production` GitHub Environment —
configure required reviewers under Settings → Environments → production.

#### What `deploy-stack` does, per environment

```
1. decrypt .env.<env>.enc ON THE RUNNER  →  write it out as `.env`  →  chmod 600
2. scp `.env` + docker-compose.prod.yml  →  ${DEPLOY_PATH}
3. record the previous image from the RUNNING container (compose ps -q api)
4. docker login ghcr.io  →  docker pull <digest>
5. pin API_IMAGE in `.env`
6. migrations one-shot, --exit-code-from migrate
7. compose up -d --no-build --remove-orphans
8. poll /api/v1/health/ready, 40 × 3s (~120s)
── ERR trap on any failure: restore the previous API_IMAGE in `.env`,
   `compose up -d --no-deps --no-build api`, re-verify health,
   dump the last 80 api log lines, exit 1
9. docker image prune -af --filter "until=72h"
── if: always() — delete the decrypted `.env` from the runner
```

Design points that matter, all preserved from the single-environment version:

- **Deploy by digest, not tag.** A tag is a mutable pointer; a digest is content-addressed, so
  what the VPS pulls is byte-identical to what was built and scanned.
- **The previous image is read from the running container**, not from `.env`. The `.env` was
  just overwritten by scp, so it cannot know what is currently serving. It resolves via
  `compose ps -q api` rather than a hardcoded container name, so renaming a service cannot
  silently break rollback.
- **`--no-deps` on the rollback is essential.** Without it, compose honours `api`'s `depends_on`
  and re-runs the `migrate` one-shot on the **old** image against a database already at the
  **new** head. Alembic fails with "Can't locate revision" and takes the rollback down with it.
- **The image name is lowercased** wherever a registry reference is assembled.
  `github.repository` preserves the repository's own casing (`innocent98/Cofoundaz-api`), but a
  Docker reference path must be lowercase. That mismatch failed the first real CD run; it is now
  fixed on both the digest path and the manual-tag path.
- **`concurrency: cancel-in-progress: false`** — cancelling a half-finished deploy leaves a VPS
  in an unknown state, which is worse than queueing.
- `docker image prune -af --filter "until=72h"` reclaims disk but deliberately keeps recent
  images, so a manual rollback to yesterday's build still has a local image.

#### The live staging E2E gate — what it proves, and what it does not

This is the part not to oversell, so it is written plainly.

The `e2e/` suite has **27 tests**. Against a **deployed** staging, only **13 can run.** The
other **14 cannot**: they read one-time tokens out of file-captured emails (`EMAIL_BACKEND=file`
plus `EMAIL_FILE_DIR`), and that directory is on the **VPS**, not on the GitHub runner. They
depend on the `mailbox` fixture — most of them transitively, via `make_verified_user`, which
consumes `mailbox` internally.

`e2e/conftest.py` supports **`E2E_REMOTE=1`**, which:

- automatically **deselects** every test whose fixture closure includes `mailbox` or
  `make_verified_user`;
- prints each deselected nodeid with the reason (`deselected (needs local mail dir): …`);
- raises `pytest.UsageError` if that would leave **zero** tests — a gate that verifies nothing
  must **fail**, not pass;
- and, as a backstop, the `mailbox` fixture itself raises in remote mode.

The CD job additionally **fails on pytest exit code 5** ("no tests collected"). That is a
deliberate divergence from the house template, which treats exit 5 as success so projects
*without* an e2e suite are not blocked. This project has a suite, so exit 5 means the selection
broke and nothing was verified.

**Verified** on 2026-08-28 against a live HTTP server: **`13 passed, 14 deselected`.**

**The 13 that gate production**

| Test |
|---|
| `e2e/test_journey.py::test_duplicate_email_409` |
| `e2e/test_journey.py::test_weak_password_422` |
| `e2e/test_journey.py::test_forgot_is_generic_for_unknown_email` |
| `e2e/test_smoke.py::test_health_ok` |
| `e2e/test_smoke.py::test_api_health_ok` |
| `e2e/test_smoke.py::test_openapi_served` |
| `e2e/test_smoke.py::test_me_requires_auth` |
| `e2e/test_smoke.py::test_unknown_route_404` |
| `e2e/test_smoke.py::test_validation_error_is_enveloped` |
| `e2e/test_smoke.py::test_seams_return_501` |
| `e2e/test_smoke.py::test_onboarding_state_requires_auth` |
| `e2e/test_smoke.py::test_invitation_preview_unknown_token_404` |
| `e2e/test_smoke.py::test_assessments_requires_auth` |

**The 14 that do NOT gate production** — deselected remotely; they **do** run in local
`make e2e` and in the CI `e2e` job on every push

| Test |
|---|
| `e2e/test_assessment.py::test_assessment_journey` |
| `e2e/test_health_score.py::test_health_score_journey` |
| `e2e/test_journey.py::test_signup_verify_login_me` |
| `e2e/test_journey.py::test_mfa_enable_then_challenge_login` |
| `e2e/test_journey.py::test_refresh_rotation_and_reuse_revokes_family` |
| `e2e/test_journey.py::test_cookie_only_refresh` |
| `e2e/test_journey.py::test_forgot_reset_then_login_with_new_password` |
| `e2e/test_journey.py::test_password_reset_revokes_existing_sessions` |
| `e2e/test_journey.py::test_login_lockout_after_5_fails` |
| `e2e/test_journey.py::test_resend_is_throttled_within_60s` |
| `e2e/test_mission.py::test_mission_journey` |
| `e2e/test_onboarding.py::test_full_onboarding_journey` |
| `e2e/test_roadmap.py::test_roadmap_journey` |
| `e2e/test_roadmap_replan.py::test_roadmap_replan_journey` |

**Be blunt about what this means.** The remote gate proves the deployment is **alive**,
**correctly wired**, **serving its OpenAPI**, and **enforcing auth, validation and 404
behaviour** against the artifact that is about to reach production. It does **not** exercise
signup-verify, MFA, password reset, refresh rotation, onboarding, roadmap, mission or assessment
journeys against that artifact.

Those journeys are **not unverified** — the CI `e2e` job runs the full suite against a locally
booted server on every push. They are simply not verified *against the deployed artifact*.

**The concrete path to widening it**, either of:

- ship the staging mail directory to the runner — e.g. `rsync` `EMAIL_FILE_DIR` over SSH between
  the signup call and the token read; or
- run the suite **on the VPS**, where the mail directory is local.

> **NOT VERIFIED —** neither widening approach has been implemented or attempted. Both are
> proposals. Do not read them as existing capability.

The job writes a step summary counting what ran and what was deselected, and uploads
`staging-e2e.log` / `staging-e2e.xml` as an artifact with 14-day retention.

#### The rate-limiter reset — a restart, not a Redis flush

Before the gate runs, `staging-e2e` SSHes in and runs
`docker compose -f docker-compose.prod.yml --env-file .env restart api`, then re-waits on
readiness.

The house template flushes rate-limit keys out of Redis. **That would be a no-op here.**
Verified: `app.state.limiter._storage` is `MemoryStorage` — the app builds its slowapi `Limiter`
with **no `storage_uri`**, so limiter state lives in each gunicorn worker's memory, not in Redis.
Restarting the `api` service is what actually clears it.

Without the reset, a redeploy that reuses warm workers can start the E2E run partway through a
120/minute budget and fail on 429s that have nothing to do with the change under test.

#### `E2E_BASE_URL` comes from the `APP_URL` variable

`staging-e2e` sets `E2E_BASE_URL: ${{ vars.APP_URL }}` and fails fast when it is empty. The
**`staging` environment's `APP_URL` must therefore be the publicly reachable staging base URL**,
or the gate cannot run at all. It is the same variable that supplies `environment.url` on the
deployment.

### House-default deviation: GHCR pull, not scp-tarball

This project deploys by **GHCR pull**, which differs from the house template's scp-tarball
default. The deviation is deliberate and stated in `cd.yml` itself.

| | GHCR pull (this project) | scp `docker save` tarball (template) |
|---|---|---|
| VPS needs GHCR auth | **Yes** — `GHCR_PULL_TOKEN` on each host | No |
| Network dependency at deploy time | **ghcr.io must be reachable** | GitHub runner → VPS only |
| Rollback source | GHCR + local image cache (72h prune window) | previous tarball on disk |
| Layer reuse on pull | Yes — only changed layers transfer | Full image every time |
| SBOM / SLSA provenance attestations | Travel **with** the manifest in GHCR | **Nowhere to live** — a tarball has no manifest to attach them to |

Two reasons drive the choice. A **digest is content-addressed**, so the registry cannot serve
different bytes under it — the same guarantee the tarball gets from being physically copied,
without copying it. And BuildKit's **SBOM and SLSA provenance attestations** are pushed alongside
the GHCR manifest; a `docker save` tarball has no place to carry them, so switching to tarballs
would silently drop the supply-chain evidence.

The cost is real and worth stating: a **GHCR credential on every VPS**, and a **network
dependency on ghcr.io at deploy time**. If ghcr.io is down you cannot deploy — but you *can*
still roll back to any image still in the local cache.

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

Run migrations by hand.

**Locally**, against the prod stack on your machine:

```bash
make prod-migrate
# = STACK_ENV_FILE=.env.production docker compose -f docker-compose.prod.yml \
#     --env-file .env.production up --no-build --exit-code-from migrate migrate
```

**On the VPS**, where the env file is `.env`:

```bash
cd "${DEPLOY_PATH}"
docker compose -f docker-compose.prod.yml --env-file .env \
  up --no-build --exit-code-from migrate migrate
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
set -a; . ./.env; set +a

docker compose -f docker-compose.prod.yml --env-file .env \
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
docker compose -f docker-compose.prod.yml --env-file .env stop api
# 3. restore
docker compose -f docker-compose.prod.yml --env-file .env \
  exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
  pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists \
  < backups/<chosen>.dump
# 4. bring the API back and confirm readiness
docker compose -f docker-compose.prod.yml --env-file .env up -d api
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

### fastapi + starlette upgrade — SHIPPED, and the trap in it

`fastapi 0.115.14 → 0.136.3` and `starlette 0.46.2 → 1.6.0`, clearing all 9
starlette advisories.

| Scanner | Before | After |
|---|---|---|
| `pip-audit` | 10 | **1** |
| `trivy image` (CI gate) | 3 | **0** |
| `trivy fs` | 4 | **1** |

The 1 remaining is `ecdsa` (no fix available) — see below.

> **Do not raise the `fastapi` ceiling above `<0.137.0` without reading this.**
>
> fastapi 0.137.0 wraps `include_router()` routes in an internal `_IncludedRouter`
> instead of flattening them into `app.routes`. slowapi's `SlowAPIMiddleware`
> finds the current endpoint by scanning `app.routes` for entries exposing
> `.endpoint`, which `_IncludedRouter` does not — so **every `/api/v1/*` route
> silently stops being rate limited.** Auth included.
>
> Measured at 135 requests against a 120/min limit: on fastapi 0.141.1 the
> `/api/v1` route never returned a single 429, while `/health` (declared with
> `@app.get`, not through a router) kept limiting normally — which is why all 405
> unit tests and 27 e2e tests passed with rate limiting disabled.
>
> `tests/api/test_rate_limit.py::test_default_limits_reach_routes_registered_via_include_router`
> now guards the pin: it fails on 0.137+ with a diagnostic naming the constraint.
>
> Going above 0.136.3 requires real work — patching slowapi's route resolution,
> waiting for a slowapi release that understands `_IncludedRouter`, or replacing
> slowapi with middleware that reads `scope["route"]` instead of scanning routes.

Verified under the production stack, not just pytest: 700 concurrent requests
produced 480 × 200 and 220 × 429 — exactly `WEB_CONCURRENCY` (4) × 120.

**Known pre-existing bug (not from this upgrade):** the 429 body is slowapi's
default `{"error":"Rate limit exceeded: ..."}` with no `Retry-After`, not the
app's `RATE_LIMITED` envelope. Confirmed identical on the pre-upgrade versions.
Any FE guide promising `error.code == "RATE_LIMITED"` is inaccurate today.

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
make prod-logs                                    # LOCAL prod stack (uses .env.production)

# On the VPS, where the env file is `.env`:
cd "${DEPLOY_PATH}"
docker compose -f docker-compose.prod.yml --env-file .env logs -f --tail=100 api
docker compose -f docker-compose.prod.yml --env-file .env logs db | grep duration   # slow queries (>1s)
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
docker compose -f docker-compose.prod.yml --env-file .env ps -a
docker compose -f docker-compose.prod.yml --env-file .env logs --tail=100 api
```

| Symptom | Cause | Fix |
|---|---|---|
| Exits immediately with a Pydantic `ValidationError` | A required env var is missing — `SECRET_KEY`, `DATABASE_URL`, `FIRST_SUPERUSER_EMAIL`, `FIRST_SUPERUSER_PASSWORD`. Pydantic Settings validates at **import time**, so the container exits with a clear error rather than starting broken. This is intended fail-fast. | Fix `.env.<env>` locally, re-encrypt, commit, redeploy. For an emergency, edit `${DEPLOY_PATH}/.env` and `up -d` — but that edit is overwritten on the next deploy. |
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
   docker compose -f docker-compose.prod.yml --env-file .env \
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
docker compose -f docker-compose.prod.yml --env-file .env \
  exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
  psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
```

Re-check the §5 arithmetic. Almost always one of:

- `WEB_CONCURRENCY` was raised without raising `POSTGRES_MAX_CONNECTIONS`.
- `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` were raised without the same check.
- Idle-in-transaction sessions are leaking — find them in `pg_stat_activity` and fix the
  code path, not the limit.

Immediate mitigation: lower `WEB_CONCURRENCY` in `${DEPLOY_PATH}/.env` and
`up -d --no-build api`. Then make the same change in `.env.<env>` locally, re-encrypt and commit
— otherwise the next deploy reverts it.

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
  "$(docker compose -f docker-compose.prod.yml --env-file .env ps -q api)"
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
re-derive §5, update `.env.<env>` locally, re-encrypt, commit, redeploy. This is the cheapest
option by a wide margin
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
  generate it, and its scope. All of them are now **per-environment**.
- **[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)** — the `scripts/env.sh` workflow: generate a key,
  encrypt, decrypt, verify, rotate, diff.
- **[ROLLBACK.md](./ROLLBACK.md)** — automatic rollback, manual rollback, and the migration
  rollback path.
