# SOP — Image-CMD CI gate, dependency fixes, weekend test bug

> **Type:** CI / dependencies / test correctness · **Date:** 2026-08-29 · **Area:** `ci.yml` `build` job, `scripts/`, `app/platform/email.py`, `app/db/models/enums.py`, mission tests

## What shipped

Branch `ci/exercise-image-cmd-and-dep-fixes`:

| Commit | What |
|---|---|
| `80fe0ac` | `test(mission)`: pin a weekday so generation tests stop failing at weekends |
| `aca8297` | `ci(build)`: run the image's own gunicorn CMD and gate on graceful shutdown |
| `ea5707a` | `chore(deps)`: `emails` 0.6 → 1.1.2 + fix the `mail_from` contract (PR #22) |
| `b31223d` | `chore(deps-dev)`: dev-tooling group + migrate 19 enums to `StrEnum` (PR #19) |
| `f535aee` | `chore(tooling)`: realign pre-commit revs with `poetry.lock` after that bump |
| _(head)_ | `docs`: this SOP + checklist reconciliation |

A verification exercise for PR #24 (`gunicorn` 23.0.0 → 26.2.0) is recorded below. **That
bump is deliberately NOT on this branch.** It needs a `CMD` change to be safe.

## Why

### 1. Nothing had ever started the image the way production starts it

The Dockerfile's `CMD` is gunicorn with uvicorn workers, `--graceful-timeout 30`,
`--max-requests`, and a worker count from `WEB_CONCURRENCY`. Three separate code paths all
avoided it:

- `ci.yml`'s `build` smoke test overrides it — `python -c "import app.main"`, `alembic --help`
- `scripts/e2e_run.sh` boots **bare uvicorn on the host**: one process, no arbiter, no
  prefork, no signal forwarding through tini
- `docker-compose.yml` (dev) overrides it with reloading uvicorn

So the entire production process model shipped unexercised. A gunicorn major bump, a removed
flag, or a worker class deprecated out from under us would first have been discovered on the
VPS.

This is not hypothetical — see the PR #24 finding below. To be precise about provenance,
because it matters: **that defect was found by hand**, by bringing the production compose
stack up on 26.2.0, *not* by the gate. The gate as first written ran `docker run` with a
writable root filesystem, so the control-socket write succeeded and there was nothing to
see. That gap has since been closed — the gate now runs with `--read-only`, the same tmpfs
and `no-new-privileges` as `docker-compose.prod.yml`, and asserts a clean boot — and it now
**does** fail on 26.2.0 automatically (verified; see Verification).

### 2. `emails` 1.x made the `mail_from` address non-optional — and it was already broken

PR #22 fails mypy on `mail_from=(EMAILS_FROM_NAME, EMAILS_FROM_EMAIL)`, both `str | None`,
against `str | tuple[str | None, str] | None`. The type is telling the truth. Measured
against emails 1.1.2:

| `mail_from` | resulting `From` header |
|---|---|
| `(None, None)` | **absent entirely** |
| `(None, "a@b.com")` | `a@b.com` |
| `("Cofoundaz", "a@b.com")` | `Cofoundaz <a@b.com>` |

RFC 5322 requires `From`, so the first row builds a message every real MTA rejects — and
`SMTPEmailSender` discards the send result, so it fails **silently**. Under 0.6 that path was
untyped and "type-checked fine"; it was broken then too, just invisibly.

### 3. PR #19's "19 errors" is ruff, not mypy

The task was reported as 19 mypy errors. It is not. Pulled the actual failing job log (run
`33120136937`, job `98684687298`): the failure is in `ruff check app tests`, which runs
**before** mypy in the `lint` job, so mypy never executed on that PR at all. Under the bump,
mypy 2.3.1 reports `Success: no issues found in 97 source files`.

All 19 are one rule in one file — `UP042`, new in ruff 0.16, inside the `UP` ruleset the
project already selects, so it arrived with the bump rather than being opted into.

### 4. The unit suite was red two days in seven

Six tests in `tests/services/test_mission_generate.py` assert on the tasks
`get_or_generate_today()` draws, but never pinned the date. The service deliberately
materialises an **empty** mission on Sat/Sun when `weekend_missions` is off. So those six
passed Monday–Friday and failed every Saturday and Sunday, on an unmodified `main`.

Corroborated by this directory: `2026-08-28-staging-pipeline-env-encryption.md` records
"406 unit tests passed, 98.25% coverage" — measured on a **Friday**. On Saturday 2026-08-29
the same tree gives 400 passed / 6 failed / 98.05%.

The irony: the two tests that *do* pin a date hardcode `date(2026, 8, 29)` as their example
Saturday — the very day this surfaced.

## How

### The image-CMD gate (`scripts/image_cmd_check.sh`)

Runs the image with **no command override** against throwaway Postgres and Redis on a
user-defined network, and asserts:

1. the image still declares the gunicorn `CMD` — so the check cannot silently degrade into
   testing a different process model
2. gunicorn loads `uvicorn.workers.UvicornWorker`
3. `WEB_CONCURRENCY` determines the worker count. Set to **3, deliberately not the baked-in
   default of 4**, so a `CMD` that hardcoded `--workers` could not pass. The baked default is
   asserted separately via `docker image inspect`, no second boot needed.
4. the container reaches Docker's `healthy` state on the image's **own** HEALTHCHECK timings,
   un-accelerated — if `--start-period=30s` is too slow for a deploy window, that is a real
   defect and this is where it should surface
5. `/health` is 200
6. `/api/v1/health/ready` is 200 with `database` **and** `redis` both `ok`, **parsed as JSON**
   rather than grepped — readiness answers 200 with a per-dependency breakdown, so a
   substring match would sail past a dead database
7. no worker respawns while serving
8. **the boot is clean** — no gunicorn `[ERROR]`/`[WARNING]` at all. Scoped to gunicorn's
   own log format, so an application-level loguru error cannot trip it, and checked *before*
   shutdown because gunicorn 23.x logs a normal drain at ERROR
9. SIGTERM drains to **exit code 0**, well inside the grace period. Not 137.

The container runs under **production's runtime posture**, not a convenient one: `--read-only`,
`--tmpfs /tmp:size=64m,mode=1777`, `--security-opt no-new-privileges:true`, mirroring
`docker-compose.prod.yml`. This is load-bearing rather than cosmetic — the only real defect
found this week was visible *only* under a read-only rootfs.

**A user-defined network rather than GitHub Actions `services:`**, deliberately: service
containers publish on the *runner's* localhost, which a container cannot reach without
host-gateway plumbing that behaves differently on a laptop. A network created here behaves
identically everywhere, so `make image-check` locally is the same test CI runs.

**It was proven to bite before being trusted.** Two deliberately-broken images were built and
run through it:

| Broken image | Result |
|---|---|
| `CMD` hardcodes `--workers 2`, ignores `WEB_CONCURRENCY` | fails: "gunicorn booted 2 workers, expected 3" |
| PID 1 swallows SIGTERM | fails: "SIGKILLed (exit 137) after 8s" |
| real image built at gunicorn 26.2.0 | fails: "gunicorn logged ERROR/WARNING during a healthy boot" |

The second image **passed every other assertion** — healthy, `/health` 200, readiness 200,
correct worker count — which is precisely why the shutdown assertion had to exist. The third
is PR #24's real defect, and it likewise passes healthy, both endpoints, worker count and
graceful shutdown; only the read-only rootfs plus the clean-boot assertion catch it.

### `emails` — raise, don't cast

An unset `EMAILS_FROM_EMAIL` is treated as the fatal misconfiguration it is, with an error
naming the setting and the alternatives, rather than casting or `# type: ignore`-ing the
`None` away. `EMAILS_FROM_NAME` needs no handling — `None` is supported and yields a valid
bare-address `From`.

`tests/platform/test_email.py` had **no SMTP coverage at all**. Four tests added, driving the
**real** emails library and asserting on the MIME message it builds, stubbing only the network
hop. Mocking `emails.Message` would prove nothing about a version bump, which is the exact
thing that broke.

### `UP042` — fixed, not suppressed, but only after proving it was safe

Ruff marks the `UP042` autofix **unsafe** for a real reason: `(str, Enum)` and `StrEnum`
stringify differently.

```
before:  str(UserStatus.active) == "UserStatus.active",  f"{...}" likewise
after:   str(UserStatus.active) == "active",             f"{...}" likewise
```

On 19 enums backing production database columns that is not a formatting nit, so it was
verified rather than assumed:

- `name == value` for **every member of all 19 enums**, and SQLAlchemy's
  `Enum(..., native_enum=False)` persists by **name** — so stored values cannot move.
  `alembic check`: *"No new upgrade operations detected."*
- **no `str()`, f-string or `%`-format of an enum member anywhere in `app/`.** The only
  `str()` calls are on UUIDs and pydantic error locations.
- pydantic serialises by `.value`, unchanged.
- of the 38 tracked files in `e2e/_captures/`, the run regenerated **25**; all 25 are
  identical to the committed versions once UUIDs and timestamps are normalised — zero enum
  serialisation drift on the wire. The other 13 were not rewritten by this run, so they are
  stated as what they are: not evidence either way.

### The weekend fixture

A `weekday` fixture shifts the service's notion of today **forward** to Monday on Sat/Sun and
is a **no-op on a weekday**, so Monday-to-Friday behaviour is byte-identical to before.

Forward, never backward: a backward shift lands on the same date as the `today - 1 day` prior
mission that `test_carries_forward_snoozed_tasks_first` creates, and the service selects
priors with a strict `mission_date < today`, so that mission would stop being found.

`e2e/test_mission.py` already compensated for the same guard against the live server clock;
this brings the unit suite in line with it.

## What's involved

| File | Change |
|---|---|
| `scripts/image_cmd_check.sh` | **new** — the gate, ~360 lines incl. reasoning and diagnostics |
| `.github/workflows/ci.yml` | one BLOCKING step in the `build` job. `on:`, job names and `needs:` chains untouched |
| `Makefile` | `make image-check`; `ci-local` grows a 7th stage |
| `app/platform/email.py` | explicit raise on unset `EMAILS_FROM_EMAIL` |
| `tests/platform/test_email.py` | +4 SMTP tests; module goes to 100% coverage |
| `app/db/models/enums.py` | 19 × `(str, enum.Enum)` → `(enum.StrEnum)` |
| `tests/services/test_mission_generate.py` | `weekday` fixture + applied to 6 tests |
| `pyproject.toml` / `poetry.lock` | `emails` + 8 dev-tooling constraints, floors raised to the **verified** versions (dependabot's ranges left `emails` 0.6 resolvable — the exact version the fix exists for) |
| `.pre-commit-config.yaml` | hook revs realigned to the lockfile after the dev bump |

**No CI gate, coverage threshold, lint rule, severity level or scanner was weakened.** The
`build` job gained a step; nothing else in `ci.yml` moved. `cd.yml` and `codeql.yml` untouched.

## PR #24 — gunicorn 23.0.0 → 26.2.0: **DO NOT MERGE AS-IS**

Verified by building the image at 26.2.0 and running the **production** compose stack
(`read_only: true`, `USER 1000:1000`) end to end, with 23.0.0 as the baseline.

Everything the bump needed to preserve, it preserves:

| | gunicorn 23.0.0 | gunicorn 26.2.0 |
|---|---|---|
| workers spawned at `WEB_CONCURRENCY=2` | 2 | 2 |
| `/api/v1/health/ready` | 200, both deps ok | 200, both deps ok |
| graceful stop exit code | **0** in <1s | **0** in <1s |
| `--graceful-timeout` / worker class / `--max-requests` | valid | valid |
| ERROR/WARNING lines at boot | **0** | **1** |

**The blocker — a new ERROR on every production boot:**

```
[ERROR] Control server error: [Errno 30] Read-only file system: '/home/appuser/.gunicorn'
```

gunicorn 25.1.0 added a control socket, **on by default**, which tries to create
`$HOME/.gunicorn/`. Our production container sets `read_only: true` and runs as UID 1000, so
that write can never succeed. Non-fatal — the container is healthy and serves traffic — but it
is a permanently-failing subsystem announcing itself at ERROR on every deploy.

This is now caught automatically: the gate runs with `--read-only` and asserts a clean boot,
and a 26.2.0 build fails it. Found by hand first, then turned into a gate.

**The fix**, verified to produce a clean boot (0 ERROR/WARNING, readiness 200, graceful stop
exit 0) on the real production stack — add one flag to the Dockerfile `CMD`:

```diff
       "--max-requests-jitter", "100", \
+      # gunicorn >= 25.1.0 starts a control socket by default at
+      # $HOME/.gunicorn/gunicorn.ctl. The production container is read_only,
+      # so that write always fails and logs at ERROR on every boot. We do not
+      # use the control socket. NOTE: this flag does not exist before 26.x -
+      # it must land in the SAME commit as the gunicorn bump, never before.
+      "--no-control-socket", \
       "--access-logfile", "-", \
```

**Sequencing is load-bearing:** `--no-control-socket` does **not exist** in gunicorn 23.0.0.
Verified — a 23.0.0 image carrying the flag dies at startup with
`gunicorn: error: unrecognized arguments: --no-control-socket`. Flag and bump must land in
one commit. The new image-CMD gate catches that ordering mistake: the container never reaches
healthy, and the gate says so — *"A gunicorn flag rejected at startup looks exactly like this."*

Two other findings, neither blocking:

- **No security argument for the bump.** Zero advisories affect gunicorn 23.0.0. The
  request-smuggling hardening in 25.3.0/26.x lands in gunicorn's HTTP parser, which under
  `UvicornWorker` **is never invoked** — uvicorn does all parsing. Upgrade for maintenance
  currency, not security.
- **`uvicorn.workers` has been deprecated since uvicorn 0.30.0** but never removed, and is
  still what uvicorn's own deployment docs recommend. Independent of this bump. When migrating,
  note `uvicorn-worker` 0.4.0 requires `uvicorn>=0.36.0`, which our `^0.32.0` pin forbids;
  `uvicorn-worker==0.3.0` is the drop-in.
- **Never pin to 24.1.0.** It broke `forwarded_allow_ips` for uvicorn's `ProxyHeadersMiddleware`
  (fixed in 24.1.1). Going straight to 26.2.0 skips it.

## Verification

Local, 2026-08-29, all against the pinned project toolchain via `poetry run`:

| Gate | Result |
|---|---|
| `black` 26.5.1 / `isort` 6.1.0 / `ruff` 0.16.5 / `mypy` 2.3.1 | clean |
| `pylint` 4.0.7 | **9.94/10** (unchanged from 3.3.9), gate `--fail-under=9.5` |
| `bandit` / `hadolint` / `actionlint` / `poetry check --lock` | clean |
| `shellcheck scripts/image_cmd_check.sh` | clean |
| Unit suite | **410 passed, 98.39% coverage** (floor 95) |
| Migrations | fresh-DB upgrade, `alembic check` no drift, downgrade→upgrade round-trip |
| e2e | **27 passed** against a real server |
| `make scan` | bandit, Semgrep, gitleaks, `trivy config`, Checkov, `trivy image` — all green |
| `pip-audit` (production set, py3.11 markers) | **no known vulnerabilities, 1 documented ignore** |
| `scripts/image_cmd_check.sh` (gunicorn 23.0.0, read-only rootfs) | **PASSED**, ~15s incl. dependency startup |
| same gate against a gunicorn 26.2.0 build | **FAILS** on the control-socket ERROR, as intended |
| `pre-commit` black / isort / ruff hooks | resolve and run; ruff hook binary reports **0.16.5**, matching the lockfile |

Suite history across the branch: 400 passed + 6 failed → 406 → 410. Coverage 98.05% → 98.25%
→ 98.39%.

**Known pre-existing, not introduced here:** `shellcheck scripts/*.sh` exits 1 on
`SC2329 (info)` for `cleanup()` in `scripts/e2e_run.sh` — a false positive, the function is
invoked via `trap ... EXIT`. Left alone as out of scope.

## Operate / roll back

Nothing to deploy. The only runtime-affecting change is the `StrEnum` migration, which is
provably value-identical on the wire and in the database.

- Roll back the whole branch: `git revert` the four commits, or drop the branch.
- Roll back only the weekend fix: `git revert 80fe0ac` — it is deliberately a separate,
  self-contained commit.
- The new CI step can be disabled by deleting the step; it has no other coupling.

## Follow-ups

- [ ] **PR #24**: apply the `--no-control-socket` diff above **in the same commit** as the
      gunicorn bump, then merge. Do not merge as-is.
- [ ] **PR #22 / #19** can be closed — both are superseded by this branch.
- [ ] Migrate off the deprecated `uvicorn.workers` module to `uvicorn-worker==0.3.0`
      (blocked on nothing; independent of #24).
- [ ] Consider widening the image-CMD gate to also run `alembic upgrade head` through the
      image, which would exercise the `migrate` service's command as well as the API's.
- [ ] `scripts/e2e_run.sh` `SC2329` — either add a `# shellcheck disable` with a reason or
      leave it; it currently makes a plain `shellcheck scripts/*.sh` exit non-zero.
- [ ] **The mypy pre-commit hook cannot install.** `additional_dependencies: [types-all]`
      pulls `types-pkg-resources`, which no longer exists on PyPI, so
      `pre-commit run mypy` dies with *"No matching distribution found"*. Reproduces on an
      unmodified `main`, so it is pre-existing — but it means anyone who runs
      `make dev-install` and then commits hits a confusing failure. Fixing it means
      choosing the stub set this project actually needs (`types-python-dateutil`,
      `types-passlib`, …) instead of the long-dead `types-all` meta-package.
- [ ] **black and isort reformat `alembic/` under pre-commit.** Every other linter here
      excludes alembic as generated code, but black's `exclude` is not applied to files
      passed *explicitly* — which is exactly what pre-commit does — so it needs
      `force-exclude`, and isort needs `extend_skip_glob`. Running `main`'s own config
      reformats 8 migration files. Also pre-existing; no migration is touched by this
      branch.
