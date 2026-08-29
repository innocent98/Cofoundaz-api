# GitHub Actions Setup — cofoundaz-api

> **Type:** deployment reference · **Covers:** every secret and variable the CI, CD and CodeQL
> workflows read, how to generate it, and where it goes · **Last verified:** 2026-08-28

Every value below was read out of `.github/workflows/ci.yml`, `.github/workflows/cd.yml` and
`.github/workflows/codeql.yml`. If a workflow references something not listed here, that is a
bug in this document — fix it.

> **NOT VERIFIED — the current shapes.** `actionlint` exits 0 on every workflow plus the
> composite actions (re-run 2026-08-28) with `shellcheck 0.11.0` integration, and `shellcheck`
> is clean on `scripts/env.sh`. But **`cd.yml` has never executed on GitHub in its current
> three-job `staging-deploy` → `staging-e2e` → `production-deploy` shape**, and nothing has ever
> run against a real VPS — no SSH deploy, no scp, no GHCR pull from a server. **No staging box
> existed at the time of this verification pass.** Static validation is not execution.
> Verification is the next push to `develop` (CI + CodeQL), the next pull request
> (`dependency-review`), and the next push to `main` (the full CD chain).

---

## 1. What needs configuring, at a glance

**Everything the deploy needs is now a PER-ENVIRONMENT value.** There are two GitHub
Environments — `staging` and `production` — and every row in the first block below must be set
**separately under each of them**. Setting a value once at repository level does not work: CD's
`staging-deploy` and `production-deploy` jobs read `${{ secrets.X }}` while targeting their own
environment, so each resolves that environment's copy.

| Where | Item | Type |
|---|---|---|
| Environments `staging` **and** `production` | `VPS_HOST` | secret |
| Environments `staging` **and** `production` | `VPS_USERNAME` | secret |
| Environments `staging` **and** `production` | `VPS_SSH_KEY` | secret |
| Environments `staging` **and** `production` | `VPS_PORT` | secret (optional — defaults to 22) |
| Environments `staging` **and** `production` | `DEPLOY_PATH` | secret |
| Environments `staging` **and** `production` | `ENV_ENCRYPTION_KEY` | secret |
| Environments `staging` **and** `production` | `GHCR_PULL_USERNAME` | secret |
| Environments `staging` **and** `production` | `GHCR_PULL_TOKEN` | secret |
| Environments `staging` **and** `production` | `APP_URL` | **variable, not secret** |
| Repository | `SONAR_TOKEN` | secret — **OPTIONAL** (§8). Absent = the `sonarcloud` job skips cleanly. |
| Automatic | `GITHUB_TOKEN` | provided by GitHub — CD's `build-and-push` uses it for the GHCR push |

That is **8 secrets × 2 environments**, plus one variable per environment.

**`ENV_ENCRYPTION_KEY` may legitimately hold the SAME value in both environments** — people
assume it has to differ, and it does not. One key encrypts both `.env.staging.enc` and
`.env.production.enc`; what is being defended against is "someone cloned the repo", not
"staging operators must not read production". If you *do* want that separation, use two keys and
set each environment's secret accordingly — nothing else in the pipeline cares.

**The `staging` environment's `APP_URL` does double duty.** It supplies `environment.url` on the
deployment *and* `E2E_BASE_URL` for the `staging-e2e` gate, which fails fast when it is empty.
It must therefore be the **publicly reachable staging base URL**, or the gate cannot run.

> **PREREQUISITE THAT IS NOT A GITHUB SETTING: nginx must already be serving both hostnames
> over TLS before the first CD run.**
>
> This is easy to miss because nothing in this file or in `cd.yml` asks for it. The
> `staging-e2e` job runs `pytest e2e/` from a GitHub-hosted runner against
> `https://staging-api.cofoundaz.com`. The compose stack publishes on `127.0.0.1` only —
> deliberately — so **without nginx there is nothing on 443 for the runner to reach**, and
> `production-deploy` is gated on `staging-e2e` succeeding. The pipeline therefore cannot
> reach production at all until the edge exists.
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

The names are not arbitrary — `cd.yml` declares `environment: name: staging` on both
`staging-deploy` and `staging-e2e`, and `environment: name: production` on `production-deploy`.
That is what gives you:

- **Required reviewers** — a human approval gate before a deploy job touches the VPS. Configure
  this on **`production`**. It is the cheapest safety net in the whole pipeline. Leaving
  `staging` un-gated is the point: staging is what production is gated *on*.
- **Deployment history** — a record of what went out, where, and when.
- **Scoped secrets** — each environment's VPS credentials are readable only by jobs targeting
  that environment.
- **Per-environment `DEPLOY_PATH`** — which is why no server path appears anywhere in this
  repository. See `DEPLOYMENT_GUIDE.md` §2.

Optionally restrict both environments to the `main` branch and `v*.*.*` tags, matching CD's
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

## 4. GHCR pull token

**Each** VPS pulls the image from `ghcr.io` at deploy time. **The GHCR package is private by
default**, so every host needs credentials of its own — the runner's `GITHUB_TOKEN` cannot be
used there, it does not leave the runner. `GHCR_PULL_USERNAME` and `GHCR_PULL_TOKEN` are
therefore set on **both** environments; one PAT reused across both is fine.

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

The SSH port. `cd.yml` uses `${{ secrets.VPS_PORT || 22 }}`, so omit it entirely if sshd is on
22. Set it if you moved sshd.

### `DEPLOY_PATH`

Absolute path to that environment's stack directory on the VPS.

**This value lives only in the secret.** No deploy path appears anywhere in this repository —
`cd.yml` and `.github/actions/deploy-stack/action.yml` read `${{ secrets.DEPLOY_PATH }}` and
nothing else. An earlier version of this document named `/opt/cofoundaz-api` as the canonical
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

### `GHCR_PULL_USERNAME` / `GHCR_PULL_TOKEN`

See §4. Set on both environments.

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

> **The `staging` value is load-bearing.** `staging-e2e` sets
> `E2E_BASE_URL: ${{ vars.APP_URL }}` and fails the job immediately if it is empty. It must be
> the **publicly reachable staging base URL** — the GitHub runner has to be able to reach it, so
> a loopback or private address will not do. Get this wrong and production is unreachable,
> because `production-deploy` requires `needs.staging-e2e.result == 'success'`.

---

## 7. `GITHUB_TOKEN` — nothing to configure

Provided automatically, at the **repository** level — nothing to set per environment.
`cd.yml`'s `build-and-push` job requests `packages: write` to push to GHCR; CI requests
`security-events: write` only in the jobs that upload SARIF. Everything else runs on the default
`contents: read`. The VPS-side pull uses `GHCR_PULL_TOKEN` instead (§4); `GITHUB_TOKEN` never
leaves the runner.

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

## 10. Setup checklist

Work top to bottom. Nothing here depends on a green CI run.

- [ ] Create **both** environments: `staging` and `production`.
- [ ] Add **required reviewers** to `production`. Leave `staging` un-gated.
- [ ] Generate the SSH keypair(s) (§3); confirm you can SSH into **each** host with it **before**
      adding secrets.
- [ ] Confirm the deploy user on each host is in the `docker` group and `docker compose version`
      works.
- [ ] **Set up nginx and obtain certificates for both hostnames** — [NGINX_TLS.md](./NGINX_TLS.md).
      Confirm `curl -I https://staging-api.cofoundaz.com/api/v1/health/ready` returns 200 **from
      off-host** before the first CD run. The E2E gate reaches staging over the public internet;
      until this works, `production-deploy` is unreachable.
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
- [ ] Push to `develop` and watch CI. Expect the security gate to be red — see §11.
- [ ] Confirm the Security tab shows five distinct result sets: `trivy-fs`, `trivy-config`,
      `trivy-image`, `semgrep`, and CodeQL's `/language:python`.
- [ ] Only once CI on `develop` is understood, merge to `main` to trigger CD. Expect the chain
      `build-and-push` → `staging-deploy` → `staging-e2e` → `production-deploy`, with
      `production-deploy` waiting on your approval. The gate should report **13 passed, 14
      deselected** — see `DEPLOYMENT_GUIDE.md` for exactly which tests those are and what the
      gate therefore does *not* prove.

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
| `e2e` | `scripts/e2e_run.sh` against a locally booted server | **The full 27-test suite.** CD's live staging gate runs only 13 of these; the other 14 need a local mail directory. See `DEPLOYMENT_GUIDE.md`. |
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
| `VPS_SSH_KEY` | On personnel change or suspected compromise | New key → `ssh-copy-id` → update the secret **on both environments** → deploy to confirm → **then** remove the old public key. |
| `ENV_ENCRYPTION_KEY` | Quarterly, or on suspected leak | `./scripts/env.sh rotate <env>` re-encrypts under a fresh key. Update the secret on **both** environments if they share one key, commit the new `.enc` files, then redeploy. Verified locally: rotation invalidates the old key. |
| `SECRET_KEY` (inside the encrypted env, not GitHub) | On suspected leak | Logs every user out immediately. Edit `.env.<env>` locally → re-encrypt → commit → redeploy. |
| `POSTGRES_PASSWORD` (inside the encrypted env) | Deliberate rotations only | `ALTER USER` inside Postgres **and** update the file together — the env var is read only at initdb. |
| `MFA_ENCRYPTION_KEY` | **Never** | Not rotatable in place. Rotating permanently locks out every MFA-enrolled user. |

**Never delete an environment's secrets to "start clean."** Add the new value, confirm a
deploy, then remove the old one.

**Values inside the env file are rotated locally, never on the server.** CD scp's `.env` from
the committed ciphertext on every deploy, so a hand edit on the VPS is overwritten by the next
one. The correct sequence is always: edit `.env.<env>` locally → re-encrypt → commit → redeploy.

---

## Related documents

- **[NGINX_TLS.md](./NGINX_TLS.md)** — nginx, TLS and certificates. A prerequisite for the
  staging E2E gate, and therefore for reaching production at all.

- **[DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)** — the full operator's manual, including the
  three-stage CD pipeline and exactly what the staging E2E gate covers.
- **[ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md)** — generating the key, encrypting, decrypting,
  verifying, rotating, diffing.
- **[ROLLBACK.md](./ROLLBACK.md)** — what to do when a deploy goes wrong.
