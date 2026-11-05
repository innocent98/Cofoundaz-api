# CI pipeline speed (API)

**What shipped:** a faster `ci.yml`: path filter + always-green `CI gate`, unit suite split into 4 shards with the coverage gate on the combined number, PR e2e trimmed to `critical` journeys plus touched areas (full e2e on push to develop and nightly). Branch `ci/pipeline-speed`, PR: see the branch. FE twin: `ci/pipeline-speed-fe`.

**Why:** most PRs are docs (handoffs, re-walk evidence) yet ran the whole pipeline: ~4,500 unit tests in one 10 to 16 minute job and every e2e journey in one ~19 minute job (a PR's wall time was the e2e job, then `build`). Adebayo asked on 2026-10-29 for "just the criticals" in e2e.

**Before** (last 3 green develop pushes, lint/unit/security only): unit 649 s, 950 s, 551 s. Last full PR run with every job (#213 head `feature/google-sign-in`): e2e 1140 s, unit 598 s, quality 102 s, build 109 s, migrations 54 s, lint 67 s, security 68 s. **After**: numbers in the PR body (measured on the PR itself).

## How

| Lever | What it does | Notes |
|---|---|---|
| `changes` job | Lists the PR's files (`gh api pulls/N/files`). Docs-only = every file under `docs/`, `e2e/_captures/`, or `*.md`. | Push/nightly always count as code. |
| Docs-only PR | Skips lint, unit, quality, migrations, e2e, build, dependency-review, CodeQL. **Security (gitleaks) still runs**: handoffs are where a pasted secret lands. | |
| `CI gate` | `if: always()`, green when every needed job is success/skipped, red on failure/cancel. | **No branch protection exists today** (`gh api .../branches/develop/protection` is 404, no rulesets). If it is turned on, require `CI gate`, not the individual jobs (they are legitimately skipped on docs PRs). |
| Unit shards | `pytest-split` `--splits 4`, `least_duration`, `.test_durations` committed. Job names `Unit tests (shard N/4)`. Each shard has its own Postgres + Redis. | A test missing from `.test_durations` gets the average, so new tests never break the split. |
| Coverage | Each shard writes `.coverage.shardN` (`relative_files = true`); job `Unit tests` (same name as before) runs `coverage combine` and `coverage report --fail-under=95` on the combined data. It fails loudly if not exactly 4 files arrive. | The 95% threshold is unchanged. |
| Critical e2e | `@pytest.mark.critical` (registered in `pyproject.toml`) on 10 journeys: signup+verify+login, assessment, team invite+accept, document upload+share+e-sign, invoice, finance payments (Paystack stub), Billing subscribe+webhook, Calendar connect, Google sign-in, admin staff sign-in. | Marker lives on the test, so "which are critical" is greppable: `grep -rn "mark.critical" e2e`. |
| Area e2e | `scripts/ci_e2e_select.py` maps changed paths to `e2e/test_*.py` by name (`app/services/finance/*` selects `test_finance*.py`). Shared plumbing (auth, core, db, middleware, worker, alembic, pyproject, lock, e2e harness, Dockerfile, ci.yml) selects ALL. | `scripts/e2e_run.sh` takes `E2E_SELECT=critical` and `E2E_AREA_FILES`; default `all`. |
| Full e2e | Push to `develop` and a nightly `schedule` (02:17 UTC) run everything. | `cd-staging.yml` / `live-e2e.yml` untouched: the staging proof gate is not weakened. |
| Cache + concurrency | The composite action `setup-python-poetry` already caches `.venv` keyed on `poetry.lock`; kept. `concurrency` already cancels superseded PR runs; the group now includes the event name so the nightly cannot cancel or be cancelled by a push. | |

Rejected: `dorny/paths-filter` (third-party action; `gh api` does it with the pinned tooling we already have), running shards with xdist inside one job (the tests share one Postgres and drop tables), skipping `security` on docs-only (loses the secret scan).

## Also fixed in passing
`Dependency review (PR only)` failed on every PR since the repo went public ("Dependency review is not supported": the Dependency graph is off in repo settings). It now runs only when the variable `ENABLE_CODE_SCANNING=true`. **Adebayo:** turn on Settings > Code security > Dependency graph, then set that variable, and the gate returns.

## Refreshing `.test_durations`
Every shard uploads `test-durations-N`. After a big test change, download the four artifacts of a green run, merge, commit:

```bash
gh run download <run-id> -p 'test-durations-*' -D /tmp/td
python3 -c "import json,glob;d={};[d.update(json.load(open(f))) for f in glob.glob('/tmp/td/*/.test_durations')];json.dump(d,open('.test_durations','w'),indent=2,sort_keys=True)"
```

## Verification
Local: workflows parse as YAML; `pytest --co -m critical e2e` collects 10; `--splits 4 --group 1/4` collects 1261/1259 of 5,042 tests; selector prints `ALL` for auth/core, finance files for `app/services/finance/*`, nothing for docs. CI evidence (docs-only commit skipping heavy jobs, code PR timings) is in the PR body.

## Operate / roll back
Revert the PR; nothing else depends on it. Local equivalent of a PR e2e run: `E2E_SELECT=critical ./scripts/e2e_run.sh`.

## Follow-ups
- Turn on the Dependency graph (above).
- Seed `.test_durations` came from the first CI run; refresh it when shard times drift apart.
- If branch protection is added: require `CI gate`.
