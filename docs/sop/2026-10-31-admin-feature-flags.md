# Admin slice D: feature flags

**What shipped:** a `feature_flags` table, a server-side `is_enabled(db, key, workspace_id)`, Super Admin list / create / update / impact routes (fresh TOTP, reason and audit row on each write), and `GET /flags` so the app reads its workspace's flags. Branch `feature/admin-feature-flags`, migration `0084_feature_flags` (chained after `0083`; if slice C also adds a migration, whichever merges second re-chains). Spec: D-ADM-13; gap rows S9 and P7b.

**Why:** staff need to roll features out gradually and switch them off fast, and the app needs one way to ask "is this on for me".

**How, and the calls made**

| Topic | Decision |
|---|---|
| Rule | Workspace gets a flag if `enabled` and (id in `workspace_allowlist` or `bucket < rollout_pct`). `enabled=false` beats everything. |
| Bucketing | `sha256("key:workspace_id")` first 8 bytes mod 100. Stable per pair, monotonic in the percentage. The key is in the hash (the spec said `hash(workspace_id)`) so the same workspaces are not first for every flag. |
| Targeting (P7b) | Bucketing plus explicit workspace allow-list only. No plan or stage rules. |
| `stage` | Label only (S9 badge); no effect on access. |
| Unknown flag | `is_enabled` returns false, so code can ship before its flag exists. |
| Who | Super Admin only (404 for everyone else). The app route needs a workspace membership. |
| Writes | Step-up code, reason (10+), one audit row in the same commit with changed fields only in `from`/`to`; row lock on update; unique key backs a create race to 409. No delete route. Key is immutable. |
| `GET /flags` | Returns only flags that are on. Found in review: returning every key let any member list unreleased feature names. |
| Blast radius | `affected_workspaces` on each flag, plus `/impact`. Computed in Python over live workspace ids (hash per flag per workspace). Fine at current scale; see follow-ups. |
| Allow-list on update | Only newly added ids must be live workspaces, so a later-deleted workspace does not block saves. |

**What's involved**
- `alembic/versions/0084_feature_flags.py`, `app/db/models/feature_flag.py`, `app/db/models/enums.py` (`FlagStage`).
- `app/services/flags.py` (`bucket`, `evaluate`, `is_enabled`, `flags_for_workspace`, create/update/impact), `app/schemas/flags.py`.
- `app/api/v1/endpoints/super_admin.py` (`GET/POST /flags`, `PATCH /flags/{key}`, `GET /flags/{key}/impact`), `app/api/v1/endpoints/flags.py` (`GET /flags`), `app/api/v1/api.py`.
- Audit actions: `flag.created`, `flag.updated`.

**Verification**
- `tests/api/test_feature_flags.py` (26): bucketing stability, uniformity, monotonic rollout, allow-list, disabled, `is_enabled`; guard matrix; step-up; create / update / refusals; audit; kill switch; blast radius; deleted workspaces; app route membership.
- `e2e/test_admin_flags.py`: one live journey against a real server on a database shared with other journeys (unique keys per run, list checks filtered). Captures in `e2e/_captures/admin_flags/`; guide `docs/fe-integration-guide-admin-flags.md`.
- Migration: upgrade, downgrade, upgrade, `alembic check` clean on a scratch database.
- One Opus review of the guards and write routes: no High; two Medium (key leak on the app route, per-workspace set rebuild) and one Low (dead allow-list ids) fixed.

**Operate / roll back**
- Create flags through the Super Admin API (or the console once built). Roll back: revert the PR and `alembic downgrade 0083_platform_staff` (drops the table; flag state is lost).
- Nothing in the app is wired to a flag yet; call `flags.is_enabled(db, "key", workspace_id)` where a feature should be gated.

**Follow-ups**
- A step-up code is spent before the cheap refusals (duplicate key, unknown flag or workspace, no-op); a corrected retry needs the next code. Could move those checks ahead of `verify_step_up`.
- Blast-radius cost grows with flags x workspaces; compute only on `/impact` or cache if the list gets slow.
- No delete or archive for flags; no plan/stage targeting rules (P7b); per-flag owner column from PRD 26.3 is not built.
- Wiring existing features to flags is separate work.
