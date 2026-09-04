# Branching & Promotion — cofoundaz-api

> **Type:** process reference · **Read time:** 6 minutes · **Last verified:** never — see below

Where features branch from, where they merge to, how a release reaches production, and what
happens when someone takes a shortcut.

> **NOT VERIFIED —** the model described here has never been executed. `develop` did not
> exist when this was written, and no commit has yet travelled the `develop → main` path.
> Every workflow file was linted (`actionlint`, `shellcheck`) and its job graph parsed, and
> `scripts/ghcr_digest.sh` was exercised against a live public GHCR repository — but the
> registry lookups against **this** project's private package, the staging deploy, the live
> E2E gate and the production deploy have all only been reasoned about. **The first push to
> `develop` and the first fast-forward promotion to `main` are the real tests.** Correct this
> document afterwards.

---

## 1. The model in one picture

```
  feature/*  fix/*  chore/*
      |
      |  PR  ->  full CI (lint, test, migrations, e2e, security, quality, build)
      v
   develop ------------------> cd-staging.yml
      |                          build image, tag sha-<short7>
      |                          deploy staging
      |                          live E2E against DEPLOYED staging
      |                          if green: tag the image staging-verified-<sha>
      |
      |  fast-forward ONLY (no squash, no merge commit)
      v
    main -------------------> cd-production.yml
                               find the image for this SHA
                               refuse unless it is staging-verified
                               deploy that exact digest. NEVER build.
```

`develop` is the trunk. `main` is a pointer to what production is running, and nothing is
developed on it.

---

## 2. Where each event runs what — and what it costs

Billing is per job, rounded **up** to the minute, against a private repo's 2,000 min/month
allowance. The CI figures come from the cost model in `.github/workflows/ci.yml`; the CD
figures are **estimates and have never been measured** — correct them after the first runs.

| Event | Workflows that fire | Jobs | ~billed min |
|---|---|---|---|
| PR into `develop`, **draft** | none | 0 | **0** |
| PR into `develop`, ready | `ci.yml` full suite | lint, test, migrations, e2e, security, quality, build (+`dependency-review`, which skips on a private repo) | ~11 |
| push `develop` | `ci.yml` safety net + `cd-staging.yml` | lint, test, security · build-and-push, staging-deploy, staging-e2e, mark-staging-verified | ~5 + ~10 (est.) |
| PR `develop` → `main` | **none** | none | **0** |
| push `main` | `cd-production.yml` | resolve-artifact, production-deploy | ~3 (est.) |
| manual `Live E2E` | `live-e2e.yml` | live-e2e | ~3 (est.) |

Two things in that table are deliberate and are the first things a reader questions.

**A PR from `develop` to `main` runs nothing at all.** Not CI, not CodeQL. Because promotion
is a fast-forward, the SHA on `main` is byte-identical to the SHA that already passed the full
suite on the `develop` PR *and* the safety net on the `develop` push. A third run would test
the same tree again and bill for it. The thing that actually protects `main` is not a re-run,
it is `cd-production.yml` refusing to deploy any commit without a staging-verified image.

**A direct push to `develop` still runs lint, test and security.** A direct push has had no
review, so this is the only thing between a typo and a staging deploy. It is the same
reasoning that used to keep those three jobs on pushes to `main`.

**Draft PRs cost nothing.** Every CI job carries a `draft == false` guard. Open work as a
draft, push as often as you like for free, and mark it ready when you want it validated.

---

## 3. The everyday loop

1. Branch from `develop`: `git switch develop && git pull && git switch -c feat/thing`.
2. Open the PR **into `develop`**, as a draft while you iterate.
3. Mark it ready. Full CI runs. Fix until green.
4. Merge into `develop`. `cd-staging.yml` builds one image, deploys it to staging, and runs
   the live E2E suite against the deployed staging API.
5. If that is green, the image is tagged `staging-verified-<sha>` and the commit is
   **promotable**. The run summary says so explicitly.
6. Promote: fast-forward `main` to `develop` (see §4). `cd-production.yml` finds that image,
   checks the proof, and deploys the identical digest. It does not rebuild.
7. Approve the `production` environment gate when prompted.

---

## 4. Promotion must be a fast-forward — and why

```bash
git switch main
git pull
git merge --ff-only develop     # fails loudly if a fast-forward is not possible
git push origin main
```

**Adebayo must enable branch protection on `main` requiring a linear history:**
Settings → Branches → `main` → *Require linear history*. Do this before the first promotion.

The reason is mechanical rather than stylistic. `cd-production.yml` finds the image for the
commit being deployed by looking up the GHCR tag `sha-<first 7 chars of the SHA>`. That tag
was created by `cd-staging.yml` when it built the commit on `develop`.

A **squash merge** or a **merge commit** produces a *new* commit with a *different* SHA. No
image was ever built for that SHA. So:

- production finds nothing to deploy, and fails;
- and it is right to fail, because the tree on `main` is no longer the tree staging tested —
  even when the diff is identical, the commit is not the one that was proven.

Only a fast-forward keeps the SHA, and therefore the proof, intact.

> The failure message in `cd-production.yml` names this cause explicitly, so a squash-merge
> mistake reads as "develop → main was not a fast-forward" in the log rather than as a
> mysterious missing image.

**PRs into `main` are still fine, and are the recommended way to promote** — just merge them
with the *rebase and merge* / fast-forward option rather than *squash and merge*. With linear
history required, GitHub disables the options that would break this.

---

## 5. What actually gates production

Splitting staging and production into separate workflow runs destroyed the `needs:` edge that
used to make production unreachable without a green gate. Replacing it with a convention —
"we only merge after staging is green" — would have been a documented habit, not a gate.

So the proof was moved into the registry, next to the artifact it is a claim about.

| Tag | Minted by | Answers |
|---|---|---|
| `sha-<short7>` | `cd-staging.yml` `build-and-push` | "which image is this commit?" |
| `staging-verified-<full 40-char sha>` | `cd-staging.yml` `mark-staging-verified`, only after a green live E2E | "was this **commit** verified?" |
| `verified-sha256-<64 hex>` | same job, same moment | "were these **bytes** verified?" |

Nothing else in the repository can create the last two. `mark-staging-verified` runs only on a
push to `develop`, and only when the build, the staging deploy **and** the live E2E all
returned `success` — a skipped or cancelled job is not treated as a pass.

On a push to `main`, `cd-production.yml` resolves both `sha-<short7>` and
`staging-verified-<sha>` through the registry API, refuses if either is missing, refuses if
they name different digests, and then deploys the **digest**. There is no bypass on that path.

Two tags rather than one because production asks the question two different ways. The
automatic path knows a commit SHA and nothing else. A rollback operator knows only an opaque
tag like `sha-a1b2c3d`, from which the full SHA cannot be recovered — so the digest-keyed tag
lets the same question be asked of the bytes. See `ROLLBACK.md`.

**What this does not prove.** Only 13 of the 27 e2e tests can run against a deployed
environment; the rest need to read captured emails from a directory on the VPS. The gate
proves what those 13 cover and no more — `DEPLOYMENT_GUIDE.md` lists exactly which.

---

## 6. Hotfixes — what happens if you push straight to `main`

**It will not deploy, and production will be left exactly as it was.**

`cd-production.yml` runs, `resolve-artifact` looks for `sha-<short7>` of your hotfix commit,
finds nothing (nothing ever built it), and fails before any step touches a VPS. The run is
red; the production stack is untouched. If you pushed a commit that *had* been built but never
E2E'd, it fails one step later, on the missing `staging-verified-<sha>` tag, with a different
message saying exactly that.

This is the intended behaviour. A commit that never went through staging must be undeployable,
not merely discouraged.

**Do this instead.** The hotfix path is the normal path, run quickly:

```bash
git switch develop && git pull
git switch -c fix/urgent-thing
# ... fix ...
git push -u origin fix/urgent-thing
# open a PR into develop, mark it ready, let CI run (~11 min)
# merge -> staging deploys and is E2E'd (~10 min)
git switch main && git pull && git merge --ff-only develop && git push
# approve the production gate
```

Roughly 25 minutes, fully gated. The temptation is to skip it because the change is one line;
the reason not to is that the first time production sees that line would be in production.

**If production is actively broken, do not hotfix forward — roll back.** A rollback is much
faster than a fix-and-promote cycle and does not require a build at all:
`Actions → CD (production) → Run workflow → image_tag: sha-<previous>`. See `ROLLBACK.md`.

**If you genuinely must ship something that never passed staging** — the last resort — use
`Actions → CD (production) → Run workflow` with an `image_tag` and `bypass_staging_proof`
ticked. That still needs repository write access and the `production` environment approval,
it emits a warning, and it records "staging proof BYPASSED" in the deployment summary. It is
the break-glass handle, not a fast lane. `ROLLBACK.md` covers when it is legitimate.

---

## 7. Bootstrapping this model

In this order. Steps 1 and 2 are Adebayo's and are not automated.

1. **Create `develop` from `main`** and push it.
2. **Branch protection on `main`: require a linear history.** Also point the `production`
   GitHub Environment's deployment-branch rule at `main`, and `staging`'s at `develop` — the
   old advice to restrict both to `main` and `v*.*.*` tags is now wrong and would block the
   staging deploy outright.
3. Merge the pipeline-restructure PR **into `develop`**, not into `main`. Merging it into
   `main` first would trigger `cd-production.yml` for a commit that has no image, which fails
   correctly but confusingly.
4. Watch `cd-staging.yml`: the build, the staging deploy, the live E2E, and — the new part —
   `mark-staging-verified` reporting `staging-verified-<sha> -> sha256:...`.
5. Confirm in GHCR that the `staging-verified-*` and `verified-sha256-*` tags exist.
6. Fast-forward `main` and approve the production gate.

> **Day-one rollback gap.** Whatever image production is running *today* was built before this
> mechanism existed, so it carries no `verified-sha256-*` tag. Rolling back to it requires
> `bypass_staging_proof`. That is expected and it resolves itself after the first promotion —
> from then on every deployable image carries its own proof.

---

## 8. Things that changed, if you remember the old model

| Before | Now |
|---|---|
| Feature PRs targeted `main` | Feature PRs target `develop` |
| Push to `main` deployed staging **and** production in one run | Push to `develop` deploys staging; push to `main` deploys production |
| CI ran on `main` and `develop` | CI runs on `develop` only |
| Production got its digest from the build in the same run | Production looks the digest up in the registry and checks its proof |
| A `v*.*.*` tag built and deployed | Tags trigger nothing; tagging labels an already-deployed commit |
| Manual rollback re-ran staging + E2E first | Manual rollback goes straight to production, gated on the proof the image already carries |
| Production could, in principle, be reached by a rebuild | Production has no build step at all |

---

## Related documents

- [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) — what the pipeline does, stage by stage
- [GITHUB_ACTIONS_SETUP.md](./GITHUB_ACTIONS_SETUP.md) — environments, secrets, branch protection
- [ROLLBACK.md](./ROLLBACK.md) — incident paths, including the break-glass bypass
- [NGINX_TLS.md](./NGINX_TLS.md) — the edge the live E2E gate reaches staging through
