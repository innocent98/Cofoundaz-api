# SOP — CI/CD branching restructure: develop deploys staging, main deploys production

> **Type:** infra / deploy pipeline · **Date:** 2026-09-03 · **Area:** `.github/workflows/`, `scripts/ghcr_digest.sh`, deployment docs

## What shipped

Three things, in that order because the third could not exist without the second.

1. **The single CD run was split in two.** `cd.yml` deployed staging, ran the live E2E, and
   deployed production in one run triggered by a push to `main`. It is deleted. A push to
   `develop` now runs `cd-staging.yml`; a push to `main` runs `cd-production.yml`. CI and CodeQL
   were retargeted to `develop` only, so a PR from `develop` to `main` runs nothing at all.

2. **The live E2E became a reusable workflow.** `live-e2e.yml` takes an `environment` input, is
   called by `cd-staging.yml` as the promotion gate, and is also `workflow_dispatch`-able
   against staging or production on demand.

3. **The staging gate moved into the registry.** When the live E2E passes,
   `mark-staging-verified` adds two tags to the same GHCR manifest —
   `staging-verified-<full sha>` and `verified-sha256-<hex>`. `cd-production.yml` has no build
   step at all: it resolves those tags, refuses if either is missing, and deploys the digest.

## Why

**A promotion should be a deliberate act.** Under the old shape, merging a PR shipped to
production. The only thing between a merge and a production deploy was the environment approval
prompt, which arrives after staging has already been redeployed and E2E'd — so a routine merge
and a release were the same gesture.

**Splitting broke the gate, and that was the hard part.** Production was unreachable without a
green staging E2E because `production-deploy` declared
`needs: [staging-deploy, staging-e2e]` with explicit `result == 'success'` checks. Once staging
and production are separate *runs*, that edge cannot exist. The obvious replacement — "we only
fast-forward `main` after staging is green" — is a habit, not a gate, and habits do not hold at
02:00.

**Production rebuilding was the other thing worth removing.** The old pipeline built once and
reused the digest, which was right, but only because both deploys lived in one run. A naive
split would have had production rebuild from `main`, which quietly changes what "tested" means:
the same source, compiled again, is not the same artifact.

## How

### The proof travels with the artifact

The mechanism had to satisfy one property: **production structurally cannot deploy what staging
did not verify**, across two independent runs, without trusting run metadata that a person can
re-run or edit.

Putting the claim in the registry next to the bytes it is a claim about does that. Two tags,
because production asks the question two different ways:

| Tag | Minted by | Answers |
|---|---|---|
| `sha-<short7>` | `cd-staging.yml` `build-and-push` | which image is this commit? |
| `staging-verified-<full 40-char sha>` | `mark-staging-verified`, only after a green live E2E | was this **commit** verified? |
| `verified-sha256-<64 hex>` | same job, same moment | were these **bytes** verified? |

The commit-keyed tag serves the automatic path, which knows a SHA and nothing else. The
digest-keyed tag serves rollback, where an operator supplies an opaque tag like `sha-a1b2c3d`
from which the full SHA cannot be recovered — so the question gets asked of the bytes instead.
Without the second tag, a manual rollback could not be gated at all, and the gate would have
needed a hole in it.

`mark-staging-verified` runs only on a `push` event and only when build, deploy and E2E all
returned `result == 'success'`. The `push`-only condition is load-bearing: on a
`workflow_dispatch` rollback of an older tag, `github.sha` is still `develop`'s HEAD, so minting
`staging-verified-<HEAD>` would attach this commit's name to a different commit's bytes — the
one lie that would make the whole scheme worthless.

**Rejected: GitHub deployment statuses, check-run lookups, and attestations.** All three ask
GitHub about the *run*, not the registry about the *artifact*, and all three are re-runnable or
re-creatable by anyone with write access. Attestations were closest in spirit but would have
meant a custom predicate and a verification step to write anyway; the tag lookup gets the same
property in ten lines of curl.

### Asserting the retag rather than assuming it

`docker buildx imagetools create` adds a tag to an existing manifest without pulling or
re-pushing layers, which also preserves the OCI index and therefore the SBOM and provenance
attestations. It is *expected* to copy the index verbatim — identical bytes hash to an identical
digest — but that is buildx's implementation detail, not a documented guarantee.

An unverified assumption there would silently downgrade the property from "production deploys
the same artifact" to "production deploys an artifact built from the same source". So the mint
step resolves both new tags through the registry and fails if either names a different digest
than the one staging tested. If it ever fires, the fix is `crane tag` — which PUTs the identical
manifest bytes under a new name — and not a relaxed assertion.

### Resolving a tag to a digest, and why not `imagetools inspect`

`scripts/ghcr_digest.sh` requests a pull token from `ghcr.io/token` and issues a `HEAD` against
the manifest endpoint, reading `Docker-Content-Digest`.

- **Exit codes are the contract:** `0` resolved, `2` does not exist, `1` everything else.
- `docker buildx imagetools inspect` collapses 404 and 401 into one non-zero exit with prose on
  stderr, so the pipeline would have to pattern-match English error text to tell "this commit
  was never built" from "the token is wrong". Those two demand completely different responses,
  and conflating them is how a credentials outage gets misdiagnosed as an unpromotable commit.
- The `Accept` header must include `application/vnd.oci.image.index.v1+json`, or an image pushed
  with attestations is invisible and the lookup 404s on an image that plainly exists.

### The build-time coupling guard

`cd-production.yml` reconstructs the build tag as `sha-` plus the first 7 characters of the SHA.
That is `docker/metadata-action`'s `format=short` today, but it is their formatting choice, and
an action bump could change it. `build-and-push` therefore asserts the tag it just pushed
matches what production will look for. Without that, an action upgrade would surface months
later as a bogus "no image was ever built for this commit" — blaming the branching model for a
dependency bump.

### Two error messages, deliberately different

The two failure modes on the automatic path mean different things, so they say different things:

- **No `sha-<short7>`** → "develop → main was not a fast-forward". A squash or merge commit
  creates a new SHA nothing ever built. The message names the branch-protection setting.
- **No `staging-verified-<sha>`** → the image exists but staging never exercised it. The hotfix
  case.

Both fail before any step touches a VPS, and both say so.

## The one deliberate hole, named

`cd-production.yml` accepts a `bypass_staging_proof` input **on `workflow_dispatch` only**. The
automatic push-to-`main` path has no bypass whatsoever.

It exists because a rollback target that predates this mechanism carries no proof tag — including,
on day one, whatever production is currently running. A production gate with no break-glass is
not safer; it is how people end up SSHing into the box and doing it by hand, unlogged.

Using it still requires repository write access **and** the `production` environment approval,
emits a `::warning`, and records "staging proof BYPASSED" in the deployment summary. It is
Adebayo's call whether even that is too much; removing the input is a two-line change.

## What's involved

| File | Change |
|---|---|
| `.github/workflows/cd.yml` | **deleted** — replaced by the two files below |
| `.github/workflows/cd-staging.yml` | new. `push: [develop]` + dispatch. `build-and-push` → `staging-deploy` → `staging-e2e` → `mark-staging-verified`. Semver tag rules removed with the `v*.*.*` trigger; `latest` kept but now inert |
| `.github/workflows/cd-production.yml` | new. `push: [main]` + dispatch. `resolve-artifact` → `production-deploy`. **No build step.** `packages: read` on the resolve job |
| `.github/workflows/live-e2e.yml` | new. `workflow_call` + `workflow_dispatch`, `environment` input. Holds the old `staging-e2e` job body; rate-limiter reset restricted to staging |
| `.github/workflows/ci.yml` | triggers `[main, develop]` → `[develop]`; COST MODEL rewritten with a per-event trigger table |
| `.github/workflows/codeql.yml` | same retarget; weekly cron untouched |
| `scripts/ghcr_digest.sh` | new. Registry v2 tag → digest resolution with distinguishable exit codes |
| `docs/deployment/BRANCHING.md` | new. The promotion model, the fast-forward requirement, hotfixes, bootstrapping |
| `docs/deployment/GITHUB_ACTIONS_SETUP.md` | workflow/job/permission facts; branch-protection and deployment-branch-rule steps; §9c rewritten around ephemeral keys |
| `docs/deployment/DEPLOYMENT_GUIDE.md` | CI and CD sections rewritten for the split; cosign argument re-argued |
| `docs/deployment/ROLLBACK.md` | §2 rewritten — a production rollback no longer traverses staging |
| `docs/deployment/NGINX_TLS.md` | gate now lives in `live-e2e.yml`; production's rate-limit zone caveat |
| `docs/checklist/PROJECT_CHECKLIST.md` | old gated-pipeline item superseded; new section, with the unverified steps left unchecked |
| `README.md` | dev-loop diagram and PR guidance now name the branch targets |

`.github/actions/deploy-stack/action.yml` is **unchanged and deliberately so** — both workflows
still call it, so the deploy logic cannot drift between environments. `docker-compose.prod.yml`,
the `Dockerfile` and all application code are untouched.

## Verification

Local, 2026-09-03. macOS. No VPS and no GitHub run.

**What was actually executed:**

- `actionlint` — exit 0 across all five workflows and both composite actions, with its
  `shellcheck 0.11.0` integration active on every `run:` block.
- `shellcheck scripts/ghcr_digest.sh` — exit 0. (`scripts/e2e_run.sh` reports one pre-existing
  SC2329 *info*; that file was not touched.)
- **A YAML parse of the job graph**, printing every job's `needs:`, `if:`, `permissions:` and
  `environment:` per workflow. Confirms each event reaches exactly the intended jobs, and — the
  one worth checking explicitly — that a pull request whose base is `main` matches **no trigger
  in any workflow**.
- **`scripts/ghcr_digest.sh` against the live registry**, using the public
  `ghcr.io/gitleaks/gitleaks` because this project's package is private and unreachable from a
  dev machine without a PAT. All four paths: existing tag → exit 0 with the digest; missing tag
  → exit 2; a digest reference round-trips to itself; an unreadable repository → exit 1. Stdout
  carries the digest and nothing else, so `DIGEST="$(...)"` is safe.
- `gitleaks v8.30.1` in directory mode over the whole tree — **no leaks found**.
- `black --check app tests` and `ruff check app tests` — both clean. `poetry check --lock` —
  exit 0.

**What was NOT verified, and why it could not be:**

- **The entire deploy path.** No VPS access. No SSH, no scp, no GHCR pull from a server, no
  compose up, no health probe, no rollback trap. Unchanged from the previous pass, and still the
  largest gap.
- **Every registry operation against this project's own private package** — the digest lookups
  and, critically, `docker buildx imagetools create`. The retag's digest-preservation is
  *asserted at runtime* rather than proven here, which is precisely why the assertion exists.
- **The GHCR token flow using `GITHUB_TOKEN`** for `packages: read` on `resolve-artifact`.
- `isort` and `mypy` could not be run — not installed in this worktree's virtualenv. No Python
  file was changed, so neither gate can regress from this diff.
- `gitleaks` in **history** mode, as CI runs it: the container cannot follow this worktree's
  `.git` gitdir pointer out of the mount. Directory mode covers the added content.

**The real tests are the first push to `develop` and the first fast-forward promotion to
`main`.** Correct this document afterwards.

## Operate / roll back

- **Before anything else:** create `develop`, and require a **linear history** on `main`. Point
  the `staging` environment's deployment-branch rule at `develop` and `production`'s at `main`.
  The previous advice — restrict both to `main` and `v*.*.*` — now blocks every staging deploy.
- **Bootstrap in the order in `BRANCHING.md` §7.** Merging this change into `main` before taking
  a commit through `develop` fires `cd-production.yml` for a commit with no image. It fails at
  `resolve-artifact`, deploys nothing, and leaves production untouched — correct, but confusing
  if unexpected.
- **Rolling this change back** is `git revert` of the range plus restoring `cd.yml`. Nothing on
  any VPS changes shape: `deploy-stack`, the compose file and the image are untouched, so a
  revert returns to the old trigger topology without a migration of any kind.
- **Day-one rollback gap:** the image production runs today predates the proof tags, so rolling
  back to it needs `bypass_staging_proof`. Resolves itself after the first promotion.
- **Destructive thing to avoid:** re-pointing a `staging-verified-*` or `verified-sha256-*` tag
  by hand. `cd-production.yml` cross-checks the two digests and refuses when they disagree,
  because at that point nobody can say which bytes were tested.

## Follow-ups

- **NOT VERIFIED: the whole deploy path**, as above. First promotion is the test.
- **Proof-tag accumulation.** Every promoted commit adds two GHCR tags, forever. Harmless for a
  long time, but there is no retention policy. Worth a scheduled cleanup that keeps, say, the
  last 50 — and it must never delete a tag for an image that is currently deployed.
- **`live-e2e.yml` against production creates real users** and does not clean them up, and the
  rate-limiter reset is deliberately unavailable there, so a busy window can 429 the run. The
  workflow warns loudly and the `production` environment approval is the friction, but the
  honest answer is that the suite has no teardown. Either give it one or restrict the dispatch
  target to staging — **Adebayo's call**.
- **`bypass_staging_proof` is a deliberate hole**, described above. Removable in two lines if
  Adebayo would rather have none.
- **CD job durations are unmeasured.** Every CD figure in the cost tables in `ci.yml` and
  `BRANCHING.md` is an estimate. Replace them with real numbers after the first runs.
- **Nothing scans the two compose files as IaC** — unchanged from the previous pass, still no
  tool that does it.
