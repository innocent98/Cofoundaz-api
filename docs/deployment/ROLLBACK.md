# Rollback Runbook — cofoundaz-api

> **Type:** incident runbook · **Read time:** 3 minutes · **Last verified:** 2026-08-28

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
> `.github/workflows/cd.yml`, `.github/actions/deploy-stack/action.yml` and
> `docker-compose.prod.yml`. The one part that *was* verified is what makes §3 trustworthy: all
> 7 alembic revisions implement real downgrade bodies (`op.drop_table` / `op.drop_index`, not
> `pass`), and the CI `migrations` job proves the `downgrade base` → `upgrade head` round-trip on
> every run.

> **Two things changed with the two-environment pipeline, and both affect this runbook.**
>
> 1. **The env file on the server is `.env`**, not `.env.production`. `.env.<env>` names exist
>    only on a developer machine. Every command below uses `--env-file .env`.
> 2. **The automatic rollback now exists identically in BOTH environments**, because staging and
>    production share one composite action, `.github/actions/deploy-stack`. A failed *staging*
>    deploy rolls staging back the same way and, because `production-deploy` requires
>    `needs.staging-deploy.result == 'success'`, production is never reached.
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

**Actions → CD → Run workflow → set `image_tag`.**

Supply an **existing GHCR tag**, e.g. `sha-a1b2c3d`. This skips `build-and-push` entirely
(`if: github.event.inputs.image_tag == ''`) — no rebuild, no CI wait.

> **The manual rollback now goes through staging first.** It redeploys the chosen tag along the
> full chain: `staging-deploy` → `staging-e2e` → `production-deploy`. That is slower than the old
> straight-to-production path, and it is the point — **a rollback is a production change, so it
> is gated too.** A tag that cannot pass the live staging gate does not reach production.
>
> Two consequences to plan for during an incident:
>
> - **Staging is rolled back as well**, because it is the first hop. If you need staging left
>   alone, this is not the path.
> - **The gate must be able to run.** It needs the `staging` environment's `APP_URL` to be
>   reachable from the GitHub runner. If staging itself is down, use the last-resort path below.

The input is validated on the runner before it reaches the VPS: it must match
`^[a-z0-9./_-]+(:[A-Za-z0-9._-]+|@sha256:[a-f0-9]{64})$`. Operator-supplied text that ends up
in a `docker pull` gets constrained to the shape of a real image reference first.

### Finding the tag to roll back to

| Source | How |
|---|---|
| Previous CD run | The **Deployment summary** step writes image, commit, and actor to the run summary. |
| GHCR | The repo's Packages page lists every pushed tag. |
| The VPS | `docker images ghcr.io/innocent98/cofoundaz-api` — the local cache, pruned at 72h. |

Tags available: `sha-<short>`, semver (`v1.2.3`, `v1.2`) on tag pushes, and `latest` on `main`.
**Never roll back to `latest`** — it is a moving pointer, so you cannot tell what you deployed
and the next rollback has no fixed reference.

This path still requires the `production` environment approval if you configured required
reviewers. That is correct: a rollback is still a production change.

### Last resort — roll back on the VPS directly

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

The compose project name is `cofoundaz-api-prod`, deliberately different from the dev stack's
`cofoundaz-api`. That separation is what stops a prod `down -v` from destroying the dev
Postgres volume — a failure mode that was hit for real before the rename, and verified fixed
after it. Do not "tidy up" the two names into one.

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
