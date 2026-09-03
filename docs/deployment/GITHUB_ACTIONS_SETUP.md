# GitHub Actions Setup — cofoundaz-api

> **Type:** deployment reference · **Covers:** every secret and variable the CI, CD and CodeQL
> workflows read, how to generate it, and where it goes · **Last verified:** 2026-09-03

Every value below was read out of the five workflow files in `.github/workflows/` —
`ci.yml`, `codeql.yml`, `cd-staging.yml`, `cd-production.yml` and `live-e2e.yml`. If a workflow
references something not listed here, that is a bug in this document — fix it.

**Read [BRANCHING.md](./BRANCHING.md) first if you have not.** Which workflow fires on which
branch, and why promotion to `main` must be a fast-forward, is assumed knowledge below.

> **NOT VERIFIED — the current shapes.** `actionlint` exits 0 on all five workflows plus the
> composite actions (re-run 2026-09-03) with `shellcheck 0.11.0` integration, `shellcheck` is
> clean on `scripts/env.sh` and `scripts/ghcr_digest.sh`, and `ghcr_digest.sh` was exercised
> against a live **public** GHCR repository through all four of its exit paths. But **the
> two-workflow CD split has never executed on GitHub**, the registry lookups have never run
> against this project's own private package, and nothing has ever run against a real VPS — no
> SSH deploy, no scp, no GHCR pull from a server. Static validation is not execution.
> Verification is the first pull request into `develop` (full CI), the first push to `develop`
> (`cd-staging.yml`, including the new `mark-staging-verified` job), and the first fast-forward
> promotion to `main` (`cd-production.yml`).

---

## 1. What needs configuring, at a glance

**Everything the deploy needs is now a PER-ENVIRONMENT value.** There are two GitHub
Environments — `staging` and `production` — and every row in the first block below must be set
**separately under each of them**. Setting a value once at repository level does not work:
`cd-staging.yml`'s `staging-deploy`, `cd-production.yml`'s `production-deploy` and
`live-e2e.yml`'s `live-e2e` jobs all read `${{ secrets.X }}` while targeting their own
environment, so each resolves that environment's copy. That the deploy jobs now live in two
different files changes nothing here — resolution is by the job's `environment:` key, not by
the file it sits in.

| Where | Item | Type |
|---|---|---|
| Environments `staging` **and** `production` | `VPS_HOST` | secret |
| Environments `staging` **and** `production` | `VPS_USERNAME` | secret |
| Environments `staging` **and** `production` | `VPS_SSH_KEY` | secret |
| Environments `staging` **and** `production` | `VPS_PORT` | secret (optional — defaults to 22) |
| Environments `staging` **and** `production` | `DEPLOY_PATH` | secret |
| Environments `staging` **and** `production` | `ENV_ENCRYPTION_KEY` | secret |
| Environments `staging` **and** `production` | `APP_URL` | **variable, not secret** |
| Repository | `SONAR_TOKEN` | secret — **OPTIONAL** (§8). Absent = the `sonarcloud` job skips cleanly. |
| Automatic | `GITHUB_TOKEN` | provided by GitHub — `cd-staging.yml` uses it to **push** to GHCR, `cd-production.yml` to **read** it (§7) |

That is **8 secrets × 2 environments**, plus one variable per environment.

**`ENV_ENCRYPTION_KEY` may legitimately hold the SAME value in both environments** — people
assume it has to differ, and it does not. One key encrypts both `.env.staging.enc` and
`.env.production.enc`; what is being defended against is "someone cloned the repo", not
"staging operators must not read production". If you *do* want that separation, use two keys and
set each environment's secret accordingly — nothing else in the pipeline cares.

**The `staging` environment's `APP_URL` does double duty.** It supplies `environment.url` on the
deployment *and* `E2E_BASE_URL` for the live E2E gate, which fails fast when it is empty. It
must therefore be the **publicly reachable staging base URL**, or the gate cannot run — and
because production is gated on that gate having passed, an empty `APP_URL` on `staging` makes
production unreachable.

**`production`'s `APP_URL` is now load-bearing too.** `live-e2e.yml` can be dispatched against
either environment, and it reads the target environment's `APP_URL` the same way.

> **PREREQUISITE THAT IS NOT A GITHUB SETTING: nginx must already be serving both hostnames
> over TLS before the first CD run.**
>
> This is easy to miss because nothing in this file or in any workflow asks for it.
> `live-e2e.yml` runs `pytest e2e/` from a GitHub-hosted runner against
> `https://staging-api.cofoundaz.com`. The compose stack publishes on `127.0.0.1` only —
> deliberately — so **without nginx there is nothing on 443 for the runner to reach**. The gate
> then fails, `mark-staging-verified` never runs, no `staging-verified-*` tag is minted, and
> `cd-production.yml` refuses to deploy the commit. The pipeline therefore cannot reach
> production at all until the edge exists.
>
> The failure is also misleading: it presents as connection errors from the test suite,
> which reads like an application or deploy problem rather than a missing prerequisite.
>
> Set it up first: **[NGINX_TLS.md](./NGINX_TLS.md)**. Two other things there matter to this
> pipeline specifically — staging must keep serving `/api/v1/openapi.json`, because
> `e2e/test_smoke.py::test_openapi_served` is one of the 13 gating tests; and staging's edge
> rate limit is deliberately looser than production's so the gate's own request burst cannot
> 429 itself.

**CI needs no secrets to pass.** Every credential in `ci.yml` is a hardcoded throwaway
(`ci-secret-key-not-used-outside-ci`, `test_user` / `test_password`) written inline in the
workflow. `SONAR_TOKEN` is the one optional addition, and its absence is a clean skip rather
than a failure — see §8. If you add a test that needs a real third-party key, add it as a
repository-level secret named `CI_<SERVICE>_<THING>` — never reuse a production credential in CI.

**CodeQL, `dependency-review` and Dependabot need no secrets at all** — see §9.

---

## 2. Create BOTH environments first

Settings → Environments → **New environment**, twice, named exactly `staging` and `production`.

The names are not arbitrary — `cd-staging.yml` declares `environment: name: staging` on
`staging-deploy`, `live-e2e.yml` takes the name as an input and declares it on its own job, and
`cd-production.yml` declares `environment: name: production` on `production-deploy`. That is
what gives you:

- **Required reviewers** — a human approval gate before a deploy job touches the VPS. Configure
  this on **`production`**. It is the cheapest safety net in the whole pipeline. Leaving
  `staging` un-gated is the point: staging is what production is gated *on*.
- **Deployment history** — a record of what went out, where, and when.
- **Scoped secrets** — each environment's VPS credentials are readable only by jobs targeting
  that environment.
- **Per-environment `DEPLOY_PATH`** — which is why no server path appears anywhere in this
  repository. See `DEPLOYMENT_GUIDE.md` §2.

**Set each environment's deployment-branch rule to match its trigger:** `staging` → `develop`,
`production` → `main`.

> The previous advice here — restrict both to `main` and `v*.*.*` tags — is now **actively
> wrong** and would break the pipeline outright. Staging deploys from `develop`, so a `main`-only
> rule blocks every staging deploy; and the `v*.*.*` trigger no longer exists at all. If you
> configured that rule earlier, change it before the first push to `develop`.

---

## 3. SSH keypair for the deploy user

> **NOT VERIFIED —** no VPS was used. Verification is `ssh -i ~/.ssh/cofoundaz_deploy
> deploy@<vps-host>` succeeding from your laptop before you paste anything into GitHub.

**One key per project, not per developer.** A shared personal key means a departing developer
takes production access with them and rotation means chasing every machine that ever had it.

**1. Generate, on your machine — not on the VPS:**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/cofoundaz_deploy -C "github-actions-deploy@cofoundaz-api" -N ""
```

`-N ""` gives it no passphrase. A passphrase-protected key cannot be used unattended by
Actions. That is precisely why this key must be **project-scoped and nothing else** — its
only capability is reaching one deploy user on one host.

**2. Install the public half on the VPS, for the non-root deploy user:**

```bash
ssh-copy-id -i ~/.ssh/cofoundaz_deploy.pub deploy@<vps-host>
ssh -i ~/.ssh/cofoundaz_deploy deploy@<vps-host> 'docker compose version'   # must succeed
```

The deploy user must be in the `docker` group. **Do not use `root`** — the deploy script
issues `docker` commands, `cd`s into `${DEPLOY_PATH}`, and edits `${DEPLOY_PATH}/.env`; none of
that needs root, and running as root means one leaked key is full host compromise.

**Do this once per host.** Staging and production are separate boxes with separate
`VPS_HOST`/`VPS_SSH_KEY` secrets. A single keypair installed on both hosts is acceptable and
simplest; two keypairs (one per environment) narrows the blast radius of a leak from both stacks
to one. Either way, the private half goes into **each** environment's `VPS_SSH_KEY` secret.

**3. Harden sshd** — `PasswordAuthentication no`, `PermitRootLogin no`.

**4. The private half becomes `VPS_SSH_KEY`.** Copy the **entire** file including the
`-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END …-----` lines and the trailing newline:

```bash
pbcopy < ~/.ssh/cofoundaz_deploy      # macOS
```

A truncated key is the single most common cause of a first-deploy SSH failure.

**Rotate this key** when a developer with VPS access leaves, or on any suspicion of
compromise: generate a new pair, `ssh-copy-id` it, update the secret, run a deploy to confirm,
then remove the old public key from `~/.ssh/authorized_keys` on the VPS. In that order.

---

## 4. GHCR pull credentials — nothing to configure

**There is nothing to create here.** `GHCR_PULL_USERNAME` and `GHCR_PULL_TOKEN` are no longer
used; do not set them.

Each VPS pulls the image from `ghcr.io` at deploy time, and the package is private by default,
so the host does need credentials. Those are now the workflow run's own `GITHUB_TOKEN`,
forwarded to the VPS for `docker login` — with `packages: read` granted on the two deploy jobs.

**An earlier version of this document said `GITHUB_TOKEN` "cannot be used there, it does not
leave the runner". That is wrong**, and it is why a hand-made PAT was specified instead. The
token is an ordinary credential string; nothing stops it being passed over SSH and used for
`docker login` on another host while the run is active. It is strictly better than a PAT:

| | PAT | run-scoped `GITHUB_TOKEN` |
|---|---|---|
| Lifetime | Until you rotate it | Expires when the job ends |
| Scope | Whatever was ticked at creation | Exactly the workflow's `permissions:` block |
| Rotation | Manual, on a reminder | Automatic, every run |
| Stored anywhere | Yes — two secrets, two environments | No |

If you already created those two secrets, they are now unused and can be deleted.

> **NOT VERIFIED —** no GHCR pull from a VPS has completed yet. The 2026-09-01 deploy reached
> this step and failed with `username is empty`, which is the bug this change fixes.

**How the credential is handled in the deploy** (`.github/actions/deploy-stack`, used
identically by `cd-staging.yml` and `cd-production.yml`): it is passed to
`appleboy/ssh-action` via `envs:` as an environment variable, **not** interpolated with
`${{ }}` into the script body. `${{ }}` interpolation happens before the remote shell runs,
which would bake the token into the literal command text sent over the wire and into any
command trace. On the VPS it is consumed with `--password-stdin`, never as an argv argument.
That handling is unchanged — only the source of the credential is.

---

## 5. Every secret, in detail

Set every one of these **twice** — once under **Settings → Environments → `staging` →
Environment secrets**, and once under **`production`**.

### `VPS_HOST`

The hostname or IP of that environment's VPS. Prefer a DNS name over a literal IP — replacing
the box then means a DNS change, not a secret edit.

### `VPS_USERNAME`

The non-root deploy user from §3 (e.g. `deploy`).

### `VPS_SSH_KEY`

The **private** key from §3, complete with header, footer, and trailing newline.

### `VPS_PORT` *(optional)*

The SSH port. Every workflow that SSHes uses `${{ secrets.VPS_PORT || 22 }}`, so omit it
entirely if sshd is on 22. Set it if you moved sshd.

### `DEPLOY_PATH`

Absolute path to that environment's stack directory on the VPS.

**This value lives only in the secret.** No deploy path appears anywhere in this repository —
`cd-staging.yml`, `cd-production.yml`, `live-e2e.yml` and
`.github/actions/deploy-stack/action.yml` read `${{ secrets.DEPLOY_PATH }}` and nothing else. An earlier version of this document named `/opt/cofoundaz-api` as the canonical
path; **that path never existed on any host**, and a repo-side path going stale without anything
failing is exactly what the per-environment secret prevents.

| Environment | The value that environment's secret currently holds |
|---|---|
| `staging` | `/opt/cofoundaz-staging` |
| `production` | `/opt/cofoundaz` |

Those are secret *values*, not repository constants. Change a secret and the deploy follows.

The deploy `cd`s into this directory and scp's two files into it on **every** deploy:

```
${DEPLOY_PATH}/docker-compose.prod.yml   # from the commit being deployed
${DEPLOY_PATH}/.env                      # chmod 600 — decrypted on the runner from .env.<env>.enc
```

**The deployed filename is `.env`, not `.env.production`.** The `.env.<env>` names exist only on
a developer machine, where both environments must coexist without colliding. The script also
**writes** to `${DEPLOY_PATH}/.env` — it pins `API_IMAGE` to the deployed digest, and the ERR
trap rewrites it during a rollback — so the deploy user needs write permission on both the file
and its directory. (There is **no** `.bak` file; rollback restores `API_IMAGE` in place from the
image read off the running container.)

Only the directory itself, owned by the deploy user, has to pre-exist.

### `ENV_ENCRYPTION_KEY`

The AES key for `.env.<environment>.enc`. `deploy-stack` decrypts `.env.staging.enc` or
`.env.production.enc` **on the runner**, writes it out as `.env`, scp's that, and deletes it from
the runner in an `if: always()` step.

**It may legitimately be the same value in both environments.** One key encrypts both files; the
threat model is a cloned repository, not staging-versus-production separation. Use two keys only
if you want that separation.

The decrypt step **hard-fails** if the `.enc` file is missing, if the key is unset or wrong, or
if the decrypted plaintext holds fewer than 5 variables. It warns when `CHANGE_ME` survives into
the plaintext. Generate and manage the key with `scripts/env.sh` / `make env-*` — the full
workflow is in **[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)**.

### `GHCR_PULL_USERNAME` / `GHCR_PULL_TOKEN` — REMOVED

Not used, and not required. The VPS-side `docker login` uses the run's own `GITHUB_TOKEN`.
See §4.

---

## 6. `APP_URL` — a variable, not a secret, and it gates production

**Settings → Environments → `staging` → Environment variables**, and again under
`production` (the *Variables* tab, beside Secrets).

```yaml
environment:
  name: production
  url: ${{ vars.APP_URL }}
```

Two reasons it is a variable:

1. The `secrets` context **is not available in `environment.url`**. `actionlint` caught this
   during authoring. Using `secrets.APP_URL` there is a workflow error, not a style choice.
2. A public base URL is not a secret.

Value: that environment's public origin, e.g. `https://api.yourdomain.com` for production and
`https://staging-api.yourdomain.com` for staging. It renders as the clickable link on the
deployment in the Actions UI.

> **The `staging` value is load-bearing.** `live-e2e.yml` sets
> `E2E_BASE_URL: ${{ vars.APP_URL }}` and fails the job immediately if it is empty. It must be
> the **publicly reachable staging base URL** — the GitHub runner has to be able to reach it, so
> a loopback or private address will not do. Get this wrong and production is unreachable: the
> gate cannot run, so `mark-staging-verified` never mints a `staging-verified-<sha>` tag, and
> `cd-production.yml` refuses to deploy a commit that has no such tag.
>
> **`production`'s value is now load-bearing too**, though less critically:
> `live-e2e.yml` reads it when dispatched against production, and it is `environment.url` on
> the production deployment.

---

## 7. `GITHUB_TOKEN` — nothing to configure

Provided automatically, at the **repository** level — nothing to set per environment.

| Workflow | Job | Beyond `contents: read` | Why |
|---|---|---|---|
| `cd-staging.yml` | `build-and-push` | `packages: write` | pushes the image to GHCR |
| `cd-staging.yml` | `mark-staging-verified` | `packages: write` | adds the `staging-verified-*` / `verified-sha256-*` tags to the manifest it just proved |
| `cd-production.yml` | `resolve-artifact` | **`packages: read`** | **new** — see below |
| `ci.yml` | `security`, `build` | `security-events: write` | SARIF upload only |
| `ci.yml` | `dependency-review` | `pull-requests: write` | writes the PR summary comment |

**`packages: read` on `resolve-artifact` is a genuinely new requirement, not a rename.** The
old single-run `cd.yml` never had to ask the registry anything: it knew the digest because it
had just built it in the same run. `cd-production.yml` has no build step, so it *looks the
digest up* — `scripts/ghcr_digest.sh` requests a pull token from `ghcr.io/token` using
`github.actor` and `GITHUB_TOKEN`, then resolves the tag. Without that permission the lookup
returns 401 and the deploy refuses (correctly, and with a message that says it is a
credentials problem rather than a missing image — the script's exit code 1 and exit code 2
mean different things precisely so those two cases never get confused).

The long-lived `GHCR_PULL_TOKEN` is deliberately **not** used for that lookup. It is the VPS's
credential; a workflow that can do its job with the ephemeral per-run token should.

`GITHUB_TOKEN` never leaves the runner.

You do not create this token, but you may need to **allow it to write packages**: Settings →
Actions → General → Workflow permissions. If the first GHCR push fails with a 403, that
setting is where to look.

> **NOT VERIFIED —** no GHCR push has been attempted.

---

## 8. Enabling SonarCloud *(optional)*

> **NOT VERIFIED —** SonarCloud has never run against this repository. No account could be
> created here, so `sonar-project.properties` still carries `CHANGE_ME` placeholders and the
> `sonarcloud` job has only ever been reasoned about, never observed — including its
> skip-cleanly path, which is correct **by construction** (a job-level `env: SONAR_TOKEN` plus
> a step `if:` gate) rather than by observation.

**Nothing breaks if you never do this.** The `sonarcloud` job checks for the secret first and,
when it is absent, writes a note to the run summary and skips every remaining step. Without the
guard, every run of this repo would go red on a missing secret for a service nobody had set up.

The job is **report-only** in intent: Sonar's verdict is a trend across commits, not a pass/fail
on one. It `needs: [test]` and downloads that job's `coverage.xml` artifact, so Sonar reports on
the *same* coverage run that gates the build instead of recomputing it and disagreeing.

**Steps, in order:**

1. Sign in to **SonarCloud** (https://sonarcloud.io) with the GitHub account that owns the repo.
2. **Import the GitHub repository** as a SonarCloud project.
3. Copy the **`projectKey`** and **`organization`** that SonarCloud assigns — they cannot be
   guessed, which is exactly why they are placeholders in the file — into
   `sonar-project.properties`, replacing `CHANGE_ME_sonar_project_key` and
   `CHANGE_ME_sonar_organization`.
4. Generate a **token** in SonarCloud (My Account → Security).
5. Add it as a **repository** secret named **`SONAR_TOKEN`** (Settings → Secrets and variables →
   Actions → Repository secrets). Not an environment secret — CI does not target an environment.
6. The job activates itself on the next run. No workflow edit is needed.

What the committed configuration already decides for you:

| Setting | Value | Why |
|---|---|---|
| `sonar.sources` / `sonar.tests` | `app` / `tests,e2e` | |
| `sonar.exclusions` | `alembic/**`, `htmlcov/**`, `var/**`, `.worktrees/**` | `alembic` is generated migration code, excluded from every other linter here (ruff, black, isort, mypy, pylint). Excluding it keeps Sonar's verdict consistent with the rest of the toolchain instead of contradicting it. |
| `sonar.cpd.exclusions` | `tests/**`, `e2e/**` | Duplicate-code detection on tests is noise — fixtures and arrange/act/assert blocks are legitimately repetitive, and that repetition is what makes tests readable. |
| `sonar.python.coverage.reportPaths` | `coverage.xml` | The artifact from the `test` job. |
| checkout `fetch-depth` | `0` | Sonar needs full history to attribute issues and compute "new code"; a shallow clone makes every line look new. |

---

## 9. Code scanning on a PRIVATE repo — the GHAS constraint

**Verified the hard way on the first real CI run.** This repository is **private**, and on a
private repository GitHub's code-scanning API and the Dependency Graph are part of **GitHub
Advanced Security (GHAS)**, a paid add-on. Without it:

```
Dependency review: "Dependency review is not supported on this repository.
  Please ensure that Dependency graph is enabled along with GitHub Advanced Security"
CodeQL / SARIF uploads: "Resource not accessible by integration"
```

### What this does and does not cost you

| | Without GHAS (today) | With GHAS, or public repo |
|---|---|---|
| Semgrep, bandit, gitleaks, pip-audit, Trivy, Checkov | **Still block the build** on findings, via exit code | Same, plus results in the Security tab |
| Security tab / SARIF | Not available | Available |
| CodeQL | **Skips cleanly** | Runs |
| dependency-review | **Skips cleanly** | Runs |

**You lose a view, not a gate.** Every scanner that blocked before still blocks. That is a
deliberate design constraint of this pipeline, and it is why the SARIF upload steps are
`continue-on-error` while the scanners themselves are not.

> **The failure mode this replaced — worth understanding before changing it.**
> On the first run the SARIF upload steps sat *between* the scanners. When an upload failed,
> GitHub marked the job failed and **skipped every later step**: `bandit` and `pip-audit` never
> ran in `security`, and `trivy config` and Checkov never ran in `iac-scan`. Four blocking
> gates silently did not execute. The job was red so nothing shipped — but had anyone
> "fixed" it with a bare `continue-on-error` on the upload, those jobs would have gone
> **green with no scanning at all**.
>
> The fix is structural, not cosmetic: **every scanner now runs before any upload**, and the
> uploads are last, best-effort, and guarded on `hashFiles()`. An upload can no longer
> suppress a gate. Do not reorder these steps.

### Turning code scanning on

Two routes, and the doc's job is to let you choose:

**Route A — make the repository public.** Code scanning, CodeQL and the Dependency Graph are
**free on public repositories**. Costs nothing; exposes the source. CodeQL and
dependency-review re-enable **automatically** — their job conditions test
`github.event.repository.private == false`.

**Route B — buy GitHub Advanced Security.** Keeps the repo private. GHAS is licensed per
*active committer* and is available on GitHub Enterprise (and as Secret Protection / Code
Security SKUs). **Pricing is not reproduced here because it changes** — check
<https://github.com/pricing> or your Enterprise account team. After enabling it, set a
repository **variable**:

```
Settings → Secrets and variables → Actions → Variables → New repository variable
  Name:  ENABLE_CODE_SCANNING
  Value: true
```

That single variable turns CodeQL and dependency-review back on. Nothing else changes.

**Route C — do nothing.** Entirely reasonable. The scanners keep blocking; you review findings
in the job logs rather than the Security tab. Nothing is unprotected.

### Why those two jobs skip rather than fail

A permanently-red job trains people to ignore red. CodeQL and dependency-review therefore carry
a job-level `if:` and report as **skipped** when neither condition is met — a visibly inactive
job, not a broken one.

---

## 9a. `ENABLE_CODE_SCANNING` — optional repository variable

| | |
|---|---|
| **Type** | Repository **variable** (not a secret — it holds no sensitive value) |
| **Value** | `true` to force CodeQL + dependency-review on |
| **Needed?** | No. Only after enabling GHAS on a private repo. |
| **Ignored when** | The repo is public — those jobs already run. |

---

## 9b. Dependabot — nothing to configure

`.github/dependabot.yml` is committed and needs no secrets. Dependabot's version updates work on
private repositories **without** GHAS (it is *Dependabot alerts*, which read the Dependency
Graph, that need GHAS on a private repo). Enable it under
**Settings → Code security → Dependabot version updates**.

## 9c. A key a TEST needs — generate it per run, do not store it

Two rules, in priority order.

**Rule 1 — never inline a key literal.** Not even a throwaway one.

```yaml
# WRONG - fails the gitleaks gate, permanently, for everyone
env:
  JOURNAL_ENCRYPTION_KEY: <a real 44-char Fernet key, pasted inline>
```

**Rule 2 — prefer generating it fresh, in the job, over storing it as a secret.**

```yaml
# BEST - nothing to leak, nothing to rotate, nothing to configure
- name: Run the tests
  run: |
    JOURNAL_ENCRYPTION_KEY="$(poetry run python -c \
      'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
      poetry run pytest tests/journal

# ACCEPTABLE, but only when Rule 2 genuinely cannot apply - see below
env:
  JOURNAL_ENCRYPTION_KEY: ${{ secrets.CI_JOURNAL_ENCRYPTION_KEY }}
```

### Why generate rather than store

A stored secret is a thing that exists: it has to be created, documented, rotated, granted to
the right scope, and remembered when someone forks the repo or sets up a second environment. A
generated one has none of that surface. The question is therefore not "is a secret safe here?"
but **"does anything actually require this value to survive between runs?"**

For an encryption key that a test suite uses to encrypt data *it just created within the same
run*, the answer is no. The suite encrypts and decrypts inside one process lifetime; a fresh
key each run is indistinguishable from a fixed one, except that there is nothing to leak.

**The project already does this**, and that precedent is the one to copy —
`scripts/e2e_run.sh`:

```bash
export MFA_ENCRYPTION_KEY="${MFA_ENCRYPTION_KEY:-$(poetry run python -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"
```

Note the `${VAR:-...}` shape: an externally supplied value still wins, so a developer can pin
one when debugging, and the run is self-contained when nobody does. Copy that shape.

**Nothing in this project's current test suites needs a key to survive between runs.** If you
are adding one, start from the assumption that yours does not either.

### The one case where a stored secret is right

**CI has to decrypt something that already exists** — a fixture encrypted at some earlier point,
a recorded provider response, an `.enc` file committed to the repo. A freshly generated key
cannot decrypt data it did not encrypt, so the key must be the *same* key, and that means
storing it.

`ENV_ENCRYPTION_KEY` is the honest example: it decrypts `.env.staging.enc` and
`.env.production.enc`, which were encrypted long before the run. That is a real requirement, so
it is a real secret.

If you conclude your case is this one, say so in the PR — in one sentence, name the pre-existing
data that has to be decrypted. If that sentence is hard to write, Rule 2 applies.

When a stored secret *is* right, use the `CI_<SERVICE>_<THING>` convention, at repository level,
and never reuse a production credential in CI.

### Why Rule 1 exists at all

This happened on 2026-08-31: a Fernet key was committed inline into this workflow's pytest env
block, with an accurate comment saying it was a throwaway that the journal tests needed. It was
genuinely inert — never used on real data, absent from every env template, referenced by no
code. It still cost real time, for two reasons:

1. **gitleaks cannot tell a throwaway from a live key.** Neither can a reviewer, quickly. Every
   committed high-entropy string has to be investigated as though it were real, and that
   investigation is the expensive part — not the fix.
2. **git history is permanent.** Once committed, the only ways out are a baseline entry or
   rewriting published history. The value was deleted from the tree the same week and the
   finding persisted regardless.

Generating per run avoids the whole category: there is no literal to paste, so there is nothing
for a future contributor to paste in the wrong place.

A local `.env` is fine for the same key: `.env` is gitignored, `.env.*` is denied by default, and
only `*.example` templates are re-allowed. See [ENV_ENCRYPTION.md](ENV_ENCRYPTION.md).

## 10. Setup checklist

Work top to bottom. Nothing here depends on a green CI run.

**Branches and protection — do these first.** The whole pipeline keys off them, and getting
them wrong produces failures that look like something else entirely. See
[BRANCHING.md](./BRANCHING.md).

- [ ] Create the **`develop`** branch from `main` and push it. It is the trunk; `main` becomes a
      pointer to what production runs.
- [ ] **Branch protection on `main`: require a linear history.** Settings → Branches → `main`.
      Without it, a squash or merge commit changes the SHA, no image exists for it, and
      `cd-production.yml` refuses to deploy — correctly, but only after the merge is already on
      `main`.
- [ ] *(Recommended)* Require the CI checks on `develop` too, so a red PR cannot merge.

- [ ] Create **both** environments: `staging` and `production`.
- [ ] Add **required reviewers** to `production`. Leave `staging` un-gated.
- [ ] Set each environment's **deployment branch rule**: `staging` → `develop`,
      `production` → `main`. If you previously restricted both to `main` and `v*.*.*` tags, fix
      it now — that rule blocks every staging deploy and the tag trigger no longer exists.
- [ ] Generate the SSH keypair(s) (§3); confirm you can SSH into **each** host with it **before**
      adding secrets.
- [ ] Confirm the deploy user on each host is in the `docker` group and `docker compose version`
      works.
- [ ] **Set up nginx and obtain certificates for both hostnames** — [NGINX_TLS.md](./NGINX_TLS.md).
      Confirm `curl -I https://staging-api.cofoundaz.com/api/v1/health/ready` returns 200 **from
      off-host** before the first CD run. The E2E gate reaches staging over the public internet;
      until this works, no `staging-verified-*` tag is ever minted and production is
      unreachable.
- [ ] Confirm each host's `.env` sets a distinct `COMPOSE_PROJECT_NAME` and `API_PORT`
      (production `cofoundaz-api-prod` / 8000, staging `cofoundaz-api-staging` / 8001). Sharing
      either is silent and destructive — see `DEPLOYMENT_GUIDE.md` §13.
- [ ] Create the deploy directory on each host, owned by the deploy user. Its absolute path is
      that environment's `DEPLOY_PATH` value — it is not written down in this repo.
- [ ] Create the GHCR PAT with `read:packages` only, with an expiry (§4).
- [ ] `docker login ghcr.io` on **each** VPS by hand and confirm a `docker pull` works.
- [ ] Generate the AES key (`make env-generate-key`), fill in `.env.staging` and
      `.env.production` locally, encrypt both, and commit **only** the two `.enc` files. See
      [ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md).
- [ ] Add all **8 secrets to `staging`** and all **8 to `production`** — including
      `ENV_ENCRYPTION_KEY` (the same value in both is fine).
- [ ] Add `APP_URL` as an environment **variable** on both. The `staging` value must be the
      publicly reachable staging base URL, or the E2E gate cannot run — which means nginx and the
      staging certificate must already exist.
- [ ] Enable Dependabot for the repo (Settings → Code security), then check within two weeks
      that Docker **digest** PRs actually appear — see §9.
- [ ] *(Optional)* Enable SonarCloud and add `SONAR_TOKEN` — §8. Skipping this is fine; the job
      skips itself.
- [ ] Open a **pull request into `develop`** and watch the full CI suite. Expect the security
      gate to be red — see §11. (A *draft* PR runs nothing at all, by design.)
- [ ] Confirm the Security tab shows the SARIF result sets it can: `trivy-image` and `semgrep`,
      plus CodeQL's `/language:python` if code scanning is enabled — see §9. On a private repo
      without GHAS these uploads are skipped and the gates still block by exit code.
- [ ] Merge into `develop` and watch `cd-staging.yml`:
      `build-and-push` → `staging-deploy` → `staging-e2e` → `mark-staging-verified`. The gate
      should report **13 passed, 14 deselected** — see `DEPLOYMENT_GUIDE.md` for exactly which
      tests those are and what the gate therefore does *not* prove.
- [ ] **Confirm the proof tags exist in GHCR** before promoting: the repository's Packages page
      should show `staging-verified-<full 40-char sha>` and `verified-sha256-<hex>` alongside
      `sha-<short7>`. `mark-staging-verified` also prints them in its job summary. If they are
      absent, production will refuse the commit — that is the gate working.
- [ ] Promote: **fast-forward** `main` to `develop`
      (`git switch main && git merge --ff-only develop && git push`). Watch
      `cd-production.yml`: `resolve-artifact` should report the digest and the proof it matched,
      then `production-deploy` waits on your approval. **No build step should appear** — if one
      does, something is wrong.
- [ ] *(Optional, once)* Run **Actions → Live E2E → Run workflow** against `staging` to confirm
      the on-demand path works before you need it in an incident.

---

## 11. Expect the first CI run to be red — and why that is correct

The `security` job and the `build` job's Trivy gate **will fail** on the current dependency
set. This is not a setup problem, and no secret will fix it.

**A second thing that looks broken and is not:** if you merge the pipeline restructure into
`main` *before* creating `develop` and taking a commit through it, `cd-production.yml` fires,
fails at `resolve-artifact` with "no image exists for this commit", and deploys nothing. That is
the gate refusing a commit that never went through staging — exactly its job. Follow the
bootstrap order in §10 (and [BRANCHING.md](./BRANCHING.md) §7) to avoid it: `develop` first,
promote by fast-forward second.

**Nothing else is expected red.** Every gate added in the scanning wave is green on the current
tree: pylint **9.94/10** against a 9.5 threshold, hadolint **exit 0** at `failure-threshold:
info`, bandit **exit 0**, and `trivy config` **0 misconfigurations**.

Verified locally: **6 HIGH / 0 CRITICAL** from Trivy (all Python packages, zero OS-package
findings), and **16 pip-audit advisories** across `starlette`, `python-multipart`, and
`ecdsa`. Nothing is suppressed — `.trivyignore` and
`.github/security/pip-audit-ignores.txt` contain only comments explaining the policy.

The full triage, including the verified dependency-resolution probe and the three options for
unblocking, is in **`DEPLOYMENT_GUIDE.md` → "Known security debt"**. Read it before adding a
single line to either ignore file. Both files require a written reason and a review date per
entry, precisely so that silencing a finding is a deliberate diff someone has to approve.

---

## 12. What CI validates without any configuration

Useful to know what you get for free on that first pull request.

**CI is 8 jobs, and which of them run depends on the event** — see the cost model at the top of
`ci.yml` and the table in [BRANCHING.md](./BRANCHING.md) §2:

| Event | Jobs that run |
|---|---|
| PR into `develop`, draft | none |
| PR into `develop`, ready | all 8 |
| push `develop` | `lint`, `test`, `security` only |
| PR `develop` → `main`, push `main` | none |

Seven run fully in parallel with no `needs:`; `build` needs
`[lint, quality, test, migrations, e2e, security]`.

> The `sonarcloud` and `trivy-repo` jobs referenced in older revisions of this document were
> **removed** from `ci.yml` in the cost-model pass — `sonarcloud` was inert without a token, and
> `trivy-repo` duplicated pip-audit and had stopped producing findings. The reasoning, and what
> coverage was lost, is recorded in the `REMOVED` block at the top of `ci.yml`. Their rows below
> are kept struck through rather than deleted, so the history is not silently rewritten.

| Job | Gate | Locally verified |
|---|---|---|
| `lint` | black, isort, ruff (**incl. C901** complexity, `max-complexity = 12`), mypy | All green — mypy: *"Success: no issues found in 91 source files"*. Measured worst: `validate_answer` at **11** |
| `test` | `pytest --cov-fail-under=95` on postgres:17 + redis:7; uploads `coverage.xml` | **338 passed, 98.29% coverage** |
| `migrations` | exactly one alembic head, upgrade from empty, `alembic check` drift, downgrade-base round-trip | 1 head (`0007_roadmap_applied_templates`) |
| `e2e` | `scripts/e2e_run.sh` against a locally booted server. **PR-only** — it does not run on a push to `develop` | **The full 27-test suite.** The live staging gate (`live-e2e.yml`) runs only 13 of these; the other 14 need a local mail directory. See `DEPLOYMENT_GUIDE.md`. |
| `security` | gitleaks (full history), Semgrep (SARIF → Security tab), **bandit** (`-r app/`), pip-audit | Semgrep exit 0 (no findings); gitleaks exit 0 after baselining one historical `SECRET_KEY` in `.gitleaksignore`; **bandit exit 0** — its 3 original findings were all false positives and now carry inline `# nosec` annotations with reasons at the site |
| `quality` | **pylint** `--fail-under=9.5`, **radon** report, **hadolint** | pylint **9.94/10, 15 messages** (7.48 and 527 messages before the scoped `[tool.pylint]` config); radon **average A (2.30)** over 301 blocks, every module's MI rated **A**; hadolint **exit 0** |
| ~~`trivy-repo`~~ | ~~`trivy fs` + `trivy config`~~ | **REMOVED** — see the note above |
| `dependency-review` | PR-only; `fail-on-severity: high`, denies GPL-3.0/AGPL-3.0/LGPL-3.0 | **NOT VERIFIED —** needs a real pull request |
| ~~`sonarcloud`~~ | ~~`needs: [test]`; runs only when `SONAR_TOKEN` exists~~ | **REMOVED** — see the note above |
| `build` | image build, smoke test, **CycloneDX SBOM** (artifact `sbom-cyclonedx-<sha>`, 90-day retention), Trivy HIGH/CRITICAL | Trivy 0.74.0 run locally. SBOM generated locally: CycloneDX 1.7, **174 components**, ~301 KB — **NOT VERIFIED** as a workflow artifact |

Separately, `.github/workflows/codeql.yml` runs on push and PR to **`develop`** — the same
branch set as `ci.yml`, retargeted for the same reason — **plus** a weekly cron, report-only.
See §9. The cron is what keeps the Security tab's baseline current; narrowing the branches does
not affect it.

**None of the CD workflows appear in this table**, because none of them run on a pull request.
`cd-staging.yml` fires on a push to `develop`, `cd-production.yml` on a push to `main`, and
`live-e2e.yml` only when called or dispatched.

**Two numbers that look like they disagree but do not.** ruff's mccabe and radon count
complexity differently: for the same function (`validate_answer`) ruff measures **11** while
radon rates it **D (23)**. Neither is wrong. ruff's C901 is the single **gate**; radon only
reports, precisely so two tools can never block on the same concept with different arithmetic.

Two notes on CI hygiene worth preserving:

- **`concurrency` cancels superseded PR runs but never pushes to `develop`.** That run is the
  only safety net a direct push gets, and `cd-staging.yml` is already building and deploying the
  same commit alongside it — cancelling would deploy to staging with the safety net silently
  unfinished rather than failed. The CD workflows have their own groups, `cd-staging` and
  `cd-production`, both with `cancel-in-progress: false`: cancelling a half-finished deploy
  leaves a VPS in an unknown state, which is worse than queueing.
- **`POETRY_VERSION` must stay on 2.x.** `poetry.lock` is lock-version 2.1, which Poetry 1.x
  cannot read. Both CI and the Dockerfile pin `2.2.1`. Downgrading requires regenerating the
  lockfile.

---

## 13. Rotation schedule

| Credential | Cadence | Notes |
|---|---|---|
| `SONAR_TOKEN` *(if enabled)* | At its expiry | Only affects the optional `sonarcloud` job; if it lapses the job fails rather than skipping, since the secret still exists but is invalid. Delete the secret to go back to a clean skip. |
| `VPS_SSH_KEY` | On personnel change or suspected compromise | New key → `ssh-copy-id` → update the secret **on both environments** → deploy to confirm → **then** remove the old public key. |
| `ENV_ENCRYPTION_KEY` | Quarterly, or on suspected leak | `./scripts/env.sh rotate <env>` re-encrypts under a fresh key. Update the secret on **both** environments if they share one key, commit the new `.enc` files, then redeploy. Verified locally: rotation invalidates the old key. |
| `SECRET_KEY` (inside the encrypted env, not GitHub) | On suspected leak | Logs every user out immediately. Edit `.env.<env>` locally → re-encrypt → commit → redeploy. |
| `POSTGRES_PASSWORD` (inside the encrypted env) | Deliberate rotations only | `ALTER USER` inside Postgres **and** update the file together — the env var is read only at initdb. |
| `MFA_ENCRYPTION_KEY` | **Never** | Not rotatable in place. Rotating permanently locks out every MFA-enrolled user. |

**Never delete an environment's secrets to "start clean."** Add the new value, confirm a
deploy, then remove the old one.

**Values inside the env file are rotated locally, never on the server.** Both CD workflows scp
`.env` from
the committed ciphertext on every deploy, so a hand edit on the VPS is overwritten by the next
one. The correct sequence is always: edit `.env.<env>` locally → re-encrypt → commit → redeploy.

---

## Related documents

- **[NGINX_TLS.md](./NGINX_TLS.md)** — nginx, TLS and certificates. A prerequisite for the
  staging E2E gate, and therefore for reaching production at all.

- **[BRANCHING.md](./BRANCHING.md)** — which branch triggers which workflow, why promotion must
  be a fast-forward, and what happens on a hotfix straight to `main`.
- **[DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)** — the full operator's manual, including both
  CD workflows and exactly what the live E2E gate covers.
- **[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)** — generating the key, encrypting, decrypting,
  verifying, rotating, diffing.
- **[ROLLBACK.md](./ROLLBACK.md)** — what to do when a deploy goes wrong.
