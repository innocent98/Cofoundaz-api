# GitHub Actions Setup — cofoundaz-api

> **Type:** deployment reference · **Covers:** every secret and variable the CI, CD and CodeQL
> workflows read, how to generate it, and where it goes · **Last verified:** 2026-08-27

Every value below was read out of `.github/workflows/ci.yml`, `.github/workflows/cd.yml` and
`.github/workflows/codeql.yml`. If a workflow references something not listed here, that is a
bug in this document — fix it.

> **NOT VERIFIED —** none of the three workflows has ever executed on GitHub. All pass
> `actionlint 1.7.12` with `shellcheck 0.11.0` integration (exit 0 on `ci.yml`, `cd.yml`,
> `codeql.yml` and the composite action). Static validation is not execution. Verification is
> the first push to `develop` (CI + CodeQL), the first pull request (`dependency-review`), and
> the first push to `main` (CD). The most likely first-run failure is **not** a missing secret
> — it is the security gate; see "Known security debt" in `DEPLOYMENT_GUIDE.md`.

---

## 1. What needs configuring, at a glance

| Where | Item | Type |
|---|---|---|
| Environment `production` | `VPS_HOST` | secret |
| Environment `production` | `VPS_USERNAME` | secret |
| Environment `production` | `VPS_SSH_KEY` | secret |
| Environment `production` | `VPS_PORT` | secret (optional — defaults to 22) |
| Environment `production` | `DEPLOY_PATH` | secret |
| Environment `production` | `GHCR_PULL_USERNAME` | secret |
| Environment `production` | `GHCR_PULL_TOKEN` | secret |
| Environment `production` | `APP_URL` | **variable, not secret** |
| Repository | `SONAR_TOKEN` | secret — **OPTIONAL** (§8). Absent = the `sonarcloud` job skips cleanly. |
| Automatic | `GITHUB_TOKEN` | provided by GitHub |

**CI needs no secrets to pass.** Every credential in `ci.yml` is a hardcoded throwaway
(`ci-secret-key-not-used-outside-ci`, `test_user` / `test_password`) written inline in the
workflow. `SONAR_TOKEN` is the one optional addition, and its absence is a clean skip rather
than a failure — see §8. If you add a test that needs a real third-party key, add it as a
repository-level secret named `CI_<SERVICE>_<THING>` — never reuse a production credential in CI.

**CodeQL, `dependency-review` and Dependabot need no secrets at all** — see §9.

---

## 2. Create the `production` environment first

Settings → Environments → **New environment** → name it exactly `production`.

The name is not arbitrary — `cd.yml` declares `environment: name: production`, and that is
what gives you:

- **Required reviewers** — a human approval gate before the deploy job touches the VPS.
  Configure this. It is the cheapest safety net in the whole pipeline.
- **Deployment history** — a record of what went out and when.
- **Scoped secrets** — the VPS credentials are readable only by jobs targeting this
  environment, not by every workflow in the repo.

Optionally restrict the environment to the `main` branch and `v*.*.*` tags, matching CD's
triggers.

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
issues `docker` commands, `cd`s into `${DEPLOY_PATH}`, and edits `.env.production`; none of
that needs root, and running as root means one leaked key is full host compromise.

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

## 4. GHCR pull token

The VPS pulls the image from `ghcr.io` at deploy time. **The GHCR package is private by
default**, so the host needs credentials of its own — the runner's `GITHUB_TOKEN` cannot be
used there, it does not leave the runner.

> **NOT VERIFIED —** no GHCR push or pull was performed. Verification: on the VPS, run
> `printf '%s' "<token>" | docker login ghcr.io -u <user> --password-stdin` followed by
> `docker pull ghcr.io/innocent98/cofoundaz-api:latest`.

**Generate a classic Personal Access Token** (GitHub → Settings → Developer settings →
Personal access tokens → Tokens (classic)):

| Setting | Value |
|---|---|
| Scope | **`read:packages` only** |
| Expiry | Set one. 90 days is a reasonable cadence; put the renewal in a calendar. |
| Name | `cofoundaz-api GHCR pull (VPS)` — so you know what breaks when you revoke it. |

`read:packages` and nothing more. A token with `write:packages` or `repo` on the VPS means
anyone who gets shell there can push a poisoned image or read your source.

- `GHCR_PULL_USERNAME` = the GitHub username that owns the PAT.
- `GHCR_PULL_TOKEN` = the PAT itself.

**How the token is handled in the deploy** (`cd.yml`): it is passed to `appleboy/ssh-action`
via `envs:` as an environment variable, **not** interpolated with `${{ }}` into the script
body. `${{ }}` interpolation happens before the remote shell runs, which would bake the token
into the literal command text sent over the wire and into any command trace. On the VPS it is
consumed with `--password-stdin`, never as an argv argument.

---

## 5. Every secret, in detail

All of these live under **Settings → Environments → `production` → Environment secrets**.

### `VPS_HOST`

The hostname or IP of the production VPS. Prefer a DNS name over a literal IP — replacing the
box then means a DNS change, not a secret edit.

### `VPS_USERNAME`

The non-root deploy user from §3 (e.g. `deploy`).

### `VPS_SSH_KEY`

The **private** key from §3, complete with header, footer, and trailing newline.

### `VPS_PORT` *(optional)*

The SSH port. `cd.yml` uses `${{ secrets.VPS_PORT || 22 }}`, so omit it entirely if sshd is on
22. Set it if you moved sshd.

### `DEPLOY_PATH`

Absolute path to the deploy directory on the VPS — e.g. `/opt/cofoundaz-api`. The deploy
script `cd`s here and expects to find:

```
${DEPLOY_PATH}/docker-compose.prod.yml
${DEPLOY_PATH}/.env.production          # chmod 600, owned by the deploy user
```

The script **writes** to `.env.production` (it pins `API_IMAGE` to the deployed digest, and
takes a `.env.production.bak` alongside it), so the deploy user needs write permission on both
the file and its directory.

### `GHCR_PULL_USERNAME` / `GHCR_PULL_TOKEN`

See §4.

---

## 6. `APP_URL` — a variable, not a secret

**Settings → Environments → `production` → Environment variables** (the *Variables* tab,
beside Secrets).

```yaml
environment:
  name: production
  url: ${{ vars.APP_URL }}
```

Two reasons it is a variable:

1. The `secrets` context **is not available in `environment.url`**. `actionlint` caught this
   during authoring. Using `secrets.APP_URL` there is a workflow error, not a style choice.
2. A public base URL is not a secret.

Value: the public origin, e.g. `https://api.yourdomain.com`. It renders as the clickable link
on the deployment in the Actions UI.

---

## 7. `GITHUB_TOKEN` — nothing to configure

Provided automatically. `cd.yml`'s `build-and-push` job requests `packages: write` to push to
GHCR; CI requests `security-events: write` only in the jobs that upload SARIF. Everything else
runs on the default `contents: read`.

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

## 9. CodeQL, dependency-review and Dependabot — nothing to configure

These three need **no secrets**. They run on the automatic `GITHUB_TOKEN` plus the
`security-events: write` / `pull-requests: write` permissions already declared in the workflows.

| Feature | Where | Configuration needed |
|---|---|---|
| **CodeQL** | `.github/workflows/codeql.yml` — its own workflow, because it needs a weekly `schedule:` (`cron: "17 4 * * 1"`) that would otherwise drag the whole 10-job CI pipeline along with it | None. Report-only; findings appear in the Security tab. |
| **`dependency-review`** | a PR-only job in `ci.yml`; `fail-on-severity: high`, denies `GPL-3.0` / `AGPL-3.0` / `LGPL-3.0` | None. Uses `pull-requests: write` to comment on failure. |
| **Dependabot** | `.github/dependabot.yml` — `pip`, `github-actions` and `docker`, weekly | Enable Dependabot in Settings → Code security if it is not already on for the repo. |

Two things to check after enabling, because neither has been observed:

> **NOT VERIFIED —** CodeQL has never run (it cannot run locally; it needs GitHub Actions), and
> `dependency-review-action` has never run (it needs a real pull request to diff a manifest
> against a base commit). Verification is the first push to `develop` and the first PR.

> **NOT VERIFIED — check this one deliberately.** The Docker base-image digest lives in an
> `ARG PYTHON_IMAGE=…` default rather than a bare `FROM` literal. Dependabot's Docker parser
> handles ARG-based `FROM` in most cases but is **not guaranteed to**. If no digest PRs appear
> within **two weeks** of enabling Dependabot, refresh the digest by hand and treat the Docker
> ecosystem as unverified until a PR is actually observed.

**Why Dependabot is not optional housekeeping.** This repo pins Actions to full commit SHAs and
the base image to a digest. Pinning **without** a mechanism to move the pin is strictly worse
than not pinning: within months you are running a base image with months of unpatched CVEs,
confidently and reproducibly. Dependabot opens the PR, CI proves the move is safe, a human
merges it.

---

## 10. Setup checklist

Work top to bottom. Nothing here depends on a green CI run.

- [ ] Create the `production` environment.
- [ ] Add **required reviewers** to it.
- [ ] Generate the SSH keypair (§3); confirm you can SSH in with it **before** adding secrets.
- [ ] Confirm the deploy user is in the `docker` group and `docker compose version` works.
- [ ] Create the GHCR PAT with `read:packages` only, with an expiry (§4).
- [ ] `docker login ghcr.io` on the VPS by hand and confirm a `docker pull` works.
- [ ] Add all 7 secrets to the `production` environment.
- [ ] Add `APP_URL` as an environment **variable**.
- [ ] Place `docker-compose.prod.yml` and a completed `.env.production` (mode 0600) at
      `${DEPLOY_PATH}`.
- [ ] Enable Dependabot for the repo (Settings → Code security), then check within two weeks
      that Docker **digest** PRs actually appear — see §9.
- [ ] *(Optional)* Enable SonarCloud and add `SONAR_TOKEN` — §8. Skipping this is fine; the job
      skips itself.
- [ ] Push to `develop` and watch CI. Expect the security gate to be red — see §11.
- [ ] Confirm the Security tab shows five distinct result sets: `trivy-fs`, `trivy-config`,
      `trivy-image`, `semgrep`, and CodeQL's `/language:python`.
- [ ] Only once CI on `develop` is understood, merge to `main` to trigger CD.

---

## 11. Expect the first CI run to be red — and why that is correct

The `security` job and the `build` job's Trivy gate **will fail** on the current dependency
set. This is not a setup problem, and no secret will fix it.

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

Useful to know what you get for free on that first push.

CI is **10 jobs**. Eight run fully in parallel with no `needs:`; `sonarcloud` needs `test`, and
`build` needs `[lint, quality, test, migrations, e2e, security, trivy-repo]`.

| Job | Gate | Locally verified |
|---|---|---|
| `lint` | black, isort, ruff (**incl. C901** complexity, `max-complexity = 12`), mypy | All green — mypy: *"Success: no issues found in 91 source files"*. Measured worst: `validate_answer` at **11** |
| `test` | `pytest --cov-fail-under=95` on postgres:17 + redis:7; uploads `coverage.xml` | **338 passed, 98.29% coverage** |
| `migrations` | exactly one alembic head, upgrade from empty, `alembic check` drift, downgrade-base round-trip | 1 head (`0007_roadmap_applied_templates`) |
| `e2e` | `scripts/e2e_run.sh` | **25 passed** |
| `security` | gitleaks (full history), Semgrep (SARIF → Security tab), **bandit** (`-r app/`), pip-audit | Semgrep exit 0 (no findings); gitleaks exit 0 after baselining one historical `SECRET_KEY` in `.gitleaksignore`; **bandit exit 0** — its 3 original findings were all false positives and now carry inline `# nosec` annotations with reasons at the site |
| `quality` | **pylint** `--fail-under=9.5`, **radon** report, **hadolint** | pylint **9.94/10, 15 messages** (7.48 and 527 messages before the scoped `[tool.pylint]` config); radon **average A (2.30)** over 301 blocks, every module's MI rated **A**; hadolint **exit 0** |
| `trivy-repo` | `trivy fs` (lockfile + secrets, **report-only**) and `trivy config` (IaC, **blocks**) | `trivy fs`: **7 HIGH, 0 CRITICAL**, **0 secrets**; `trivy config`: **0 misconfigurations** |
| `dependency-review` | PR-only; `fail-on-severity: high`, denies GPL-3.0/AGPL-3.0/LGPL-3.0 | **NOT VERIFIED —** needs a real pull request |
| `sonarcloud` | `needs: [test]`; runs only when `SONAR_TOKEN` exists | **NOT VERIFIED —** see §8 |
| `build` | image build, smoke test, **CycloneDX SBOM** (artifact `sbom-cyclonedx-<sha>`, 90-day retention), Trivy HIGH/CRITICAL | Trivy 0.74.0 run locally. SBOM generated locally: CycloneDX 1.7, **174 components**, ~301 KB — **NOT VERIFIED** as a workflow artifact |

Separately, `.github/workflows/codeql.yml` runs on the same push/PR branches **plus** a weekly
cron, report-only. See §9.

**Two numbers that look like they disagree but do not.** ruff's mccabe and radon count
complexity differently: for the same function (`validate_answer`) ruff measures **11** while
radon rates it **D (23)**. Neither is wrong. ruff's C901 is the single **gate**; radon only
reports, precisely so two tools can never block on the same concept with different arithmetic.

Two notes on CI hygiene worth preserving:

- **`concurrency` cancels superseded PR runs but never `main` runs.** A cancelled main CI run
  would leave that commit permanently undeployable.
- **`POETRY_VERSION` must stay on 2.x.** `poetry.lock` is lock-version 2.1, which Poetry 1.x
  cannot read. Both CI and the Dockerfile pin `2.2.1`. Downgrading requires regenerating the
  lockfile.

---

## 13. Rotation schedule

| Credential | Cadence | Notes |
|---|---|---|
| `GHCR_PULL_TOKEN` | At its expiry — 90 days is reasonable | Update the secret **and** re-run `docker login` on the VPS. |
| `SONAR_TOKEN` *(if enabled)* | At its expiry | Only affects the optional `sonarcloud` job; if it lapses the job fails rather than skipping, since the secret still exists but is invalid. Delete the secret to go back to a clean skip. |
| `VPS_SSH_KEY` | On personnel change or suspected compromise | New key → `ssh-copy-id` → update secret → deploy to confirm → **then** remove the old public key. |
| `SECRET_KEY` (in `.env.production`, not GitHub) | On suspected leak | Logs every user out immediately. |
| `POSTGRES_PASSWORD` (in `.env.production`) | Deliberate rotations only | `ALTER USER` inside Postgres **and** update the file together — the env var is read only at initdb. |
| `MFA_ENCRYPTION_KEY` | **Never** | Not rotatable in place. Rotating permanently locks out every MFA-enrolled user. |

**Never delete an environment's secrets to "start clean."** Add the new value, confirm a
deploy, then remove the old one.

---

## Related documents

- **[DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)** — the full operator's manual.
- **[ROLLBACK.md](./ROLLBACK.md)** — what to do when a deploy goes wrong.
