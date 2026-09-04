# Rollback Runbook — cofoundaz-api

> **Type:** incident runbook · **Read time:** 3 minutes · **Last verified:** 2026-09-03

Three paths, in the order you will need them.

| Situation | Path | Section |
|---|---|---|
| A deploy just failed in Actions | It already rolled itself back. Confirm, then diagnose. | §1 |
| A deploy went green but the release is bad | Manual rollback via `workflow_dispatch` | §2 |
| The schema is wrong, or a migration failed mid-deploy | `alembic downgrade` — **backup first** | §3 |

**Rolling back the application and rolling back the schema are separate operations.**
Restoring the previous image does **not** undo a migration that already ran. Section 3 exists
because of that.

> **NOT VERIFIED —** none of these procedures has been executed against a real VPS, and no
> staging box existed at the time of this verification pass. They are read directly off
> `.github/workflows/cd-staging.yml`, `.github/workflows/cd-production.yml`,
> `.github/workflows/live-e2e.yml`, `scripts/ghcr_digest.sh`,
> `.github/actions/deploy-stack/action.yml` and `docker-compose.prod.yml`. The one part that
> *was* verified is what makes §3 trustworthy: all 7 alembic revisions implement real downgrade
> bodies (`op.drop_table` / `op.drop_index`, not `pass`), and the CI `migrations` job proves the
> `downgrade base` → `upgrade head` round-trip on every run.

> **The pipeline was restructured on 2026-09-03 — see `docs/deployment/BRANCHING.md` for the
> full branching model. Three things changed and all three affect this runbook.**
>
> 1. **The env file on the server is `.env`**, not `.env.production`. `.env.<env>` names exist
>    only on a developer machine. Every command below uses `--env-file .env`.
> 2. **`cd.yml` no longer exists.** Deploys are now two separate workflows —
>    **CD (staging)** (`.github/workflows/cd-staging.yml`, fires on a push to `develop`) and
>    **CD (production)** (`.github/workflows/cd-production.yml`, fires on a push to `main`) —
>    plus a reusable **Live E2E** workflow (`.github/workflows/live-e2e.yml`) that both the
>    staging promotion gate and an on-demand `workflow_dispatch` run. The automatic rollback
>    described in §1 still exists identically in both environments, because staging and
>    production still share one composite action, `.github/actions/deploy-stack`.
> 3. **Production still cannot be reached by a bad staging run — but not because of a `needs:`
>    edge, because there isn't one any more.** Staging and production are now separate workflow
>    runs on separate trigger events, so `cd-production.yml` has no job dependency on
>    `cd-staging.yml`'s result to point at. Instead, `cd-production.yml`'s `resolve-artifact` job
>    refuses to deploy any commit that does not carry a `staging-verified-<full sha>` tag in
>    GHCR — a tag only `cd-staging.yml`'s `mark-staging-verified` job can mint, and only after
>    the live E2E gate passes against a *deployed* staging stack. The practical guarantee is the
>    same one the old `needs:` edge gave (a broken or unrun staging pass blocks production), but
>    the mechanism moved from a job dependency to a proof carried in the registry alongside the
>    artifact — which is what makes it survive the two runs no longer sharing a workflow.
>
> `${DEPLOY_PATH}` below is that environment's **GitHub Environment secret**. No server path is
> written down in this repository — read the secret, not this document.

---

## 1. Automatic rollback — what the CD trap already did

The deploy script installs `trap rollback ERR` before it touches anything that matters. On
**any** non-zero command after that point, it:

1. Restores the **previous** `API_IMAGE` value in `${DEPLOY_PATH}/.env`, in place.
2. `docker pull`s that image in case it was pruned.
3. Runs `compose up -d --no-deps --no-build api` to bring the old image back.
4. Polls `/api/v1/health/ready` for up to ~60s and reports whether the rollback is healthy.
5. Dumps the last **80 lines** of `api` logs into the Actions log.
6. Exits 1, so the run is red.

**`--no-deps` is load-bearing.** Without it, compose honours `api`'s `depends_on` and re-runs
the `migrate` one-shot on the **old** image against a database already at the **new** head.
Alembic fails with "Can't locate revision" and takes the rollback down with it. Rolling code back
without rolling the schema back is intended — it assumes migrations stay backward-compatible for
one release, which is what the CI downgrade round-trip gate protects.

The previous image is read from the **running container** (`compose ps -q api` →
`docker inspect --format '{{.Config.Image}}'`), not from `.env`. The `.env` was just overwritten
by scp from the committed ciphertext, so it cannot know what is currently serving. It resolves
the container via compose rather than a hardcoded name, so renaming a service or the project
cannot silently break rollback.

**There is no `.bak` file.** The current composite action rewrites `API_IMAGE` in `.env` in
place; earlier versions of this runbook described a `.env.production.bak`, and that file is no
longer created. If you find one on a host, it is a leftover from the old script.

The trap covers the migration step, the `compose up`, and the readiness poll — a readiness
timeout deliberately triggers it via an explicit `false`.

### What it cannot do

| Case | Consequence |
|---|---|
| **First deploy** — no previous image exists | It logs "no previous image recorded — cannot auto-roll back", dumps the api logs, and exits 1. Fix forward. |
| **A migration already applied** | The old image now runs against a **newer schema**. See §3. |
| **The image was pruned** | CD prunes with `until=72h`, keeping recent images, so this is unlikely — but if the local image is gone, the rollback pull fails. Use §2. |

### Confirm the state yourself — do not trust the log alone

```bash
ssh deploy@<vps-host>
cd "${DEPLOY_PATH}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"

$COMPOSE ps
curl -fsS "http://127.0.0.1:$(grep -E '^API_PORT=' .env | cut -d= -f2)/api/v1/health/ready"
docker inspect --format '{{.Config.Image}}' "$($COMPOSE ps -q api)"
grep '^API_IMAGE=' .env
```

The `revision` field in the readiness body is the git SHA baked into the running image. If
`revision`, the inspected image, and `API_IMAGE` disagree, **the file is the one to distrust**
— the container is what is serving.

Do the same on **staging** before assuming production is the only affected stack: the two run
the same digest, so a bad release is a bad release on both.

---

## 2. Manual rollback — redeploy a known-good tag

Use when a deploy went green but the release is bad, or when the automatic rollback could not
complete.

**Actions → CD (production) → Run workflow → set `image_tag`.**

Supply an **existing GHCR tag**, e.g. `sha-a1b2c3d`. `cd-production.yml` has no build step at
all — there is no `build-and-push` to skip, because it does not exist in this workflow.
`resolve-artifact` only ever resolves an already-built, already-pushed image through the
registry and hands its digest to `production-deploy`.

> **The manual rollback no longer goes through staging, and that is a deliberate change in the
> safety story — not an oversight.** The old design (the single `cd.yml`) re-proved the tag live
> by redeploying it through the full chain — `staging-deploy` → `staging-e2e` →
> `production-deploy` — before it reached production. The new design instead trusts a proof
> minted **when the image was first built and promoted**: `cd-staging.yml` tags the tested
> digest `verified-sha256-<64-hex-digest>` the moment the live E2E suite passes against deployed
> staging, and `cd-production.yml`'s `resolve-artifact` job refuses to deploy any `image_tag`
> whose digest does not carry that tag — unless `bypass_staging_proof` is ticked (below).
>
> **What this gains:**
> - **Staging is left untouched.** A production rollback no longer redeploys anything to staging
>   as a side effect of getting there.
> - **It works when staging is down or unreachable.** The old path needed the staging gate to be
>   able to run at all; the new path needs only the registry to answer.
> - **It is faster during an incident** — a registry lookup and a deploy, with no rebuild, no
>   staging redeploy, and no live E2E run sitting in the critical path.
>
> **What this loses:**
> - **The image is not re-exercised against a live environment at rollback time.** The gate ran
>   once, when the image was first promoted, and a rollback trusts that result rather than
>   re-establishing it. If config or schema has drifted since the proof was minted and you are
>   not confident the image still behaves correctly, run `live-e2e.yml` by hand against
>   production first (`workflow_dispatch`, `environment: production`) instead of trusting a
>   stale proof blind — see the warning that run prints about writing real data to production.

The `image_tag` input itself is validated on the runner before it reaches the VPS:
`^[A-Za-z0-9._-]+$` — letters, digits, dot, underscore and dash only, so it cannot contain a `/`
or `@` and cannot redirect the pull to a different registry or repository. The
`registry/repo@digest` reference assembled from it is separately validated against
`^[a-z0-9./_-]+@sha256:[a-f0-9]{64}$` immediately before the deploy step ever sees it.

### `bypass_staging_proof` — break glass, not routine

`cd-production.yml`'s `workflow_dispatch` also exposes a `bypass_staging_proof` checkbox.
Ticking it deploys the named tag to production even though no `verified-sha256-<digest>` proof
exists for it. It exists for exactly one legitimate case: **an image built before the proof
mechanism shipped** — which, on day one, is whatever production is currently running. It is
**never** available on the automatic push-to-`main` path; that path has no bypass at all.

When used, the run emits a `::warning` and the deployment summary records "staging proof
BYPASSED" against whoever ticked it. If the same situation keeps forcing a bypass, that is a
signal to get a fresh staging-verified image built and promoted the normal way — not a reason to
keep reaching for the checkbox.

### Finding the tag to roll back to

| Source | How |
|---|---|
| Previous **CD (production)** run | The **Deployment summary** step writes image, commit, and gating proof to the run summary. |
| GHCR | The repo's Packages page lists every pushed tag, including `staging-verified-<sha>` and `verified-sha256-<digest>`. |
| The VPS | `docker images ghcr.io/innocent98/cofoundaz-api` — the local cache, pruned at 72h. |

GHCR now carries two extra tag families alongside `sha-<short7>`: `staging-verified-<full sha>`
(keyed to the commit) and `verified-sha256-<digest>` (keyed to the bytes). **Seeing a
`verified-sha256-<digest>` tag on the same manifest as the candidate `sha-<short7>` tag is the
fastest way to confirm a rollback target will pass the gate** without needing
`bypass_staging_proof` — if it is not there, either that image was never promoted through
`cd-staging.yml`, or the bypass is what you need.

Tags available: `sha-<short7>` from every build, plus the two proof tags above. **Semver tags
(`v1.2.3`, `v1.2`) are never produced any more** — the `v*.*.*` tag trigger was removed, since a
version tag no longer builds anything; tagging a release is now a labelling act on a commit
already running in production. **`latest` is now inert** — builds only ever run on a push to
`develop`, and `latest` was only ever tagged on the default branch, which is `main`. Neither
environment would deploy `latest` in any case on the automatic paths, since both deploy by
digest, but do not expect a fresh `latest` tag to appear.

**Never roll back to `latest`.** `resolve-artifact` will happily accept it as an `image_tag` and
resolve it to whatever digest it currently points at — so the rollback would "work" while
leaving you unable to say what you deployed, and the next rollback with no fixed reference to
aim at. Name a `sha-<short7>` tag. This has not changed; `latest` being inert makes it a *staler*
moving pointer, not a safer one.

This path still requires the `production` environment approval if you configured required
reviewers. That is correct: a rollback is still a production change.

### Last resort — roll back on the VPS directly

This is now also the only rollback path that works if **GHCR itself** is unreachable — every
Actions-driven path above resolves the image through the registry before it can deploy anything.

If Actions itself is unavailable:

```bash
ssh deploy@<vps-host>
cd "${DEPLOY_PATH}"          # that environment's DEPLOY_PATH secret value
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"

cp .env .env.manual-rollback.bak     # take your own backup; the pipeline no longer makes one
sed -i "s|^API_IMAGE=.*|API_IMAGE=ghcr.io/innocent98/cofoundaz-api:sha-<good>|" .env
$COMPOSE up -d --no-deps --no-build api
curl -fsS "http://127.0.0.1:$(grep -E '^API_PORT=' .env | cut -d= -f2)/api/v1/health/ready"
```

Note `--no-build` (compose may otherwise try to build from a source tree that is not there) and
`--no-deps` (otherwise the `migrate` one-shot re-runs on the old image against the new schema).

> **This edit does not survive.** CD scp's `.env` from the committed ciphertext on **every**
> deploy, so any manual change on the server is overwritten by the next one. Treat it as an
> emergency stopgap and record it where the next deploy's operator will see it. The durable fix
> is always: edit `.env.<env>` **locally** → `make env-encrypt-<env>` → commit the `.enc` →
> redeploy.

---

## 3. Database migration rollback

> **STOP. Take a backup first.** A downgrade that drops a column or a table **destroys the
> data in it**, and `alembic downgrade` will not warn you. There is no undo.

### 3.1 Back up — non-negotiable

Postgres uses `scram-sha-256` for host **and** local auth, so `psql` / `pg_dump` **prompt for
a password** (verified). Pass `PGPASSWORD` or the command hangs on a prompt nobody answers.

```bash
cd "${DEPLOY_PATH}"
set -a; . ./.env; set +a
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"

mkdir -p backups
$COMPOSE exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc \
  > "backups/pre-downgrade-$(date -u +%Y%m%dT%H%M%SZ).dump"

ls -lh backups/   # confirm it is non-empty before continuing
```

> **NOT VERIFIED —** this backup command was never executed. Verification: run it, then
> restore into a scratch database and compare row counts. Do that drill **before** you need
> it, not during an incident.

### 3.2 Find out where you actually are

```bash
$COMPOSE run --rm migrate alembic current      # the applied revision
$COMPOSE run --rm migrate alembic history      # the chain
```

Current head as of this writing: `0007_roadmap_applied_templates`, with 7 revisions
(`0001_initial_schema` … `0007_roadmap_applied_templates`) and 25 tables in `public`.

### 3.3 Read the migration before you run it

Open `alembic/versions/<revision>.py` and read its `downgrade()` body. Classify it:

| Downgrade contains | Destructive? | Then |
|---|---|---|
| `op.drop_index` only | No | Safe. Rebuilding an index costs time, not data. |
| `op.drop_column` | **Yes** | That column's data is gone. Backup is mandatory. |
| `op.drop_table` | **Yes** | The whole table is gone. |
| `op.alter_column` narrowing a type | **Yes** | Values that do not fit are lost or the migration errors mid-way. |

All 7 revisions in this repo implement real downgrade bodies — verified. The CI `migrations`
job runs `downgrade base` then `upgrade head` on every push, which is what makes that claim
hold over time rather than being a one-off observation.

### 3.4 Stop the API first

The app must not be writing against a schema that is moving underneath it.

```bash
$COMPOSE stop api
```

### 3.5 Downgrade exactly one step at a time

```bash
$COMPOSE run --rm migrate alembic downgrade -1
$COMPOSE run --rm migrate alembic current      # confirm after every step
```

Prefer `-1` over naming a distant revision. One step at a time means you can stop the moment
something looks wrong; a multi-step jump can drop three tables before you read the first line
of output.

**Never run `alembic downgrade base` on production.** That is every table, gone. It exists as
a CI correctness check, not an operation.

### 3.6 Deploy the matching application version, then restart

The schema and the code must agree. After downgrading, deploy the image that expects that
schema (§2), then:

```bash
$COMPOSE up -d --no-build api
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
```

Confirm `revision` in the readiness body is the version you intended.

### 3.7 If a migration failed *mid-deploy*

The ERR trap has already restored the previous **image** — but the schema may be partially
migrated, and those are separate problems.

1. `$COMPOSE run --rm migrate alembic current` — establish where the schema actually is.
2. `$COMPOSE logs migrate` — find which revision failed and whether it was transactional.
   Postgres DDL is transactional, so a revision that raised inside its transaction rolled
   itself back; one that failed between operations may not have.
3. **Back up (§3.1) before touching anything.**
4. Decide: fix forward (a new revision that repairs the state) is usually safer than
   downgrading a half-applied revision.

**Do not re-run the deploy hoping it resolves itself.** Alembic is not idempotent under a
partially-applied revision.

---

## 4. Do not do these

| Command | Why |
|---|---|
| `docker compose -f docker-compose.prod.yml down -v` | `-v` deletes the named volumes, Postgres data included. Unrecoverable. `make prod-down` deliberately omits `-v`. |
| `docker system prune --volumes` | Same outcome, less obviously. |
| `alembic downgrade base` on production | Every table, gone. |
| Rolling back to `latest` | A moving pointer. You cannot tell what you deployed. |
| Re-running a failed deploy without reading the logs | You do not yet know what state the schema is in. |
| Editing `${DEPLOY_PATH}/.env` and expecting it to stick | CD scp's `.env` from the committed ciphertext on every deploy. The edit is gone on the next one. Edit `.env.<env>` locally, re-encrypt, commit, redeploy. |
| Editing `.env.<env>` locally and forgetting to re-encrypt | The `.enc` is what deploys. An un-encrypted change ships nothing. `make env-verify` checks both files still decrypt. |
| Rolling production back without looking at staging | Both stacks run the same digest, so both are affected. |

The compose project name comes from `COMPOSE_PROJECT_NAME` in each stack's `.env` —
`cofoundaz-api-prod` for production (also the compose-file default), `cofoundaz-api-staging`
for staging — and both are deliberately different from the dev stack's `cofoundaz-api`. That
separation is what stops a `down -v` in one stack from destroying another's Postgres volume:
a failure mode that was hit for real between prod and dev before the rename, verified fixed
after it, and re-verified across prod and staging on 2026-08-29. Do not "tidy up" any two of
these names into one.

Concretely, if a rollback has you running compose by hand: **check `COMPOSE_PROJECT_NAME` in
the `.env` you are pointing at before typing any command with `-v` in it.** The wrong value
does not error — it silently addresses the other environment's volumes.

---

## 5. After any rollback

- [ ] Readiness returns 200, and `revision` is the version you intended.
- [ ] `docker inspect` on the running container, `API_IMAGE` in `${DEPLOY_PATH}/.env`, and
      `revision` all agree.
- [ ] `alembic current` matches the schema the deployed code expects.
- [ ] Any stray `.env.*.bak` you created by hand is read, then removed.
- [ ] **Staging was checked too** — it runs the same digest.
- [ ] If you edited `${DEPLOY_PATH}/.env` by hand, the same change was made in `.env.<env>`
      locally, re-encrypted and committed — otherwise the next deploy reverts it.
- [ ] The pre-downgrade dump is copied **off the VPS**.
- [ ] The reason for the rollback is written down, and the fix-forward change is open.

---

## Related documents

- **[DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)** — the full operator's manual, including the
  incident runbook and backup/restore.
- **[GITHUB_ACTIONS_SETUP.md](./GITHUB_ACTIONS_SETUP.md)** — the per-environment secrets and
  variables, including `DEPLOY_PATH`.
- **[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)** — how to change an environment properly: edit
  locally, re-encrypt, commit, redeploy.
- **[BRANCHING.md](./BRANCHING.md)** — the full `develop` → `main` promotion model, the
  fast-forward requirement, and why the staging-verified proof lives in the registry instead of
  a workflow dependency.
