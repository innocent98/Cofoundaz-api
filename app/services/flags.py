"""Feature flags (Admin slice D, D-ADM-13).

`is_enabled(db, key, workspace_id)` is the one question the app asks. A workspace gets a flag when
the flag is enabled and either its id is on the allow-list or its bucket is below `rollout_pct`.
The bucket is a SHA-256 of `key:workspace_id` reduced to 0-99: stable for a given pair, so a
workspace never flips on a re-deploy, and raising the percentage only ever adds workspaces. The key
is part of the hash so the same workspaces aren't first in line for every rollout.
"""

import hashlib
import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.errors import AppError
from app.db.models.enums import FlagStage
from app.db.models.feature_flag import FeatureFlag
from app.db.models.startup import Startup
from app.platform.audit import write_audit

BUCKETS = 100


def _err(code: str, message: str, status: int) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def bucket(key: str, workspace_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"{key}:{workspace_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def evaluate(
    *,
    key: str,
    enabled: bool,
    rollout_pct: int,
    allowlist: Collection[uuid.UUID],
    workspace_id: uuid.UUID,
) -> bool:
    if not enabled:
        return False
    return workspace_id in allowlist or bucket(key, workspace_id) < rollout_pct


def _evaluate_row(flag: FeatureFlag, workspace_id: uuid.UUID) -> bool:
    return evaluate(
        key=flag.key,
        enabled=flag.enabled,
        rollout_pct=flag.rollout_pct,
        allowlist=flag.workspace_allowlist,
        workspace_id=workspace_id,
    )


def is_enabled(db: Session, key: str, workspace_id: uuid.UUID) -> bool:
    """False for a flag that doesn't exist, so code can ship before its flag is created."""
    flag = db.query(FeatureFlag).filter(FeatureFlag.key == key).first()
    return flag is not None and _evaluate_row(flag, workspace_id)


def flags_for_workspace(db: Session, workspace_id: uuid.UUID) -> dict[str, bool]:
    """Only the flags that are on. A missing key means off, so a member can't list the keys of
    disabled, internal or unreleased flags."""
    rows = db.query(FeatureFlag).order_by(FeatureFlag.key.asc()).all()
    return {f.key: True for f in rows if _evaluate_row(f, workspace_id)}


def _workspace_ids(db: Session) -> list[uuid.UUID]:
    return [r[0] for r in db.query(Startup.id).filter(Startup.deleted_at.is_(None)).all()]


def affected_workspaces(
    db: Session,
    *,
    key: str,
    enabled: bool,
    rollout_pct: int,
    allowlist: Sequence[uuid.UUID],
    ids: list[uuid.UUID] | None = None,
) -> int:
    """How many live workspaces would get the flag with these settings (PRD 26.3 blast radius)."""
    ids = _workspace_ids(db) if ids is None else ids
    allowed = frozenset(allowlist)  # once per flag, not once per workspace
    return sum(
        evaluate(
            key=key, enabled=enabled, rollout_pct=rollout_pct, allowlist=allowed, workspace_id=w
        )
        for w in ids
    )


def serialize(
    db: Session, flags: list[FeatureFlag], ids: list[uuid.UUID] | None = None
) -> list[dict[str, Any]]:
    ids = _workspace_ids(db) if ids is None else ids
    return [
        {
            "id": str(f.id),
            "key": f.key,
            "name": f.name,
            "description": f.description,
            "stage": f.stage.value,
            "enabled": f.enabled,
            "rollout_pct": f.rollout_pct,
            "workspace_allowlist": [str(w) for w in f.workspace_allowlist],
            "affected_workspaces": affected_workspaces(
                db,
                key=f.key,
                enabled=f.enabled,
                rollout_pct=f.rollout_pct,
                allowlist=f.workspace_allowlist,
                ids=ids,
            ),
            "created_at": f.created_at.isoformat(),
            "updated_at": f.updated_at.isoformat(),
        }
        for f in flags
    ]


def list_flags(db: Session) -> dict[str, Any]:
    rows = db.query(FeatureFlag).order_by(FeatureFlag.created_at.asc(), FeatureFlag.key.asc()).all()
    ids = _workspace_ids(db)
    return {"flags": serialize(db, rows, ids), "total_workspaces": len(ids)}


def _snapshot(f: FeatureFlag) -> dict[str, Any]:
    return {
        "name": f.name,
        "description": f.description,
        "stage": f.stage.value,
        "enabled": f.enabled,
        "rollout_pct": f.rollout_pct,
        "workspace_allowlist": sorted(str(w) for w in f.workspace_allowlist),
    }


def _check_allowlist(db: Session, allowlist: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    unique = list(dict.fromkeys(allowlist))
    if unique:
        found = {
            r[0]
            for r in db.query(Startup.id)
            .filter(Startup.id.in_(unique), Startup.deleted_at.is_(None))
            .all()
        }
        missing = [str(w) for w in unique if w not in found]
        if missing:
            raise _err("UNKNOWN_WORKSPACE", f"No such workspace: {', '.join(missing[:5])}.", 422)
    return unique


def _get(db: Session, key: str, *, lock: bool = False) -> FeatureFlag:
    q = db.query(FeatureFlag).filter(FeatureFlag.key == key)
    flag = (q.with_for_update() if lock else q).first()
    if flag is None:
        raise _err("FLAG_NOT_FOUND", "No flag with that key.", 404)
    return flag


def create_flag(
    db: Session,
    ctx: StaffContext,
    *,
    key: str,
    name: str,
    description: str,
    stage: FlagStage,
    enabled: bool,
    rollout_pct: int,
    allowlist: Sequence[uuid.UUID],
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    exists = _err("FLAG_KEY_EXISTS", "A flag with that key already exists.", 409)
    if db.query(FeatureFlag.id).filter(FeatureFlag.key == key).first():
        raise exists
    flag = FeatureFlag(
        key=key,
        name=name,
        description=description,
        stage=stage,
        enabled=enabled,
        rollout_pct=rollout_pct,
        workspace_allowlist=_check_allowlist(db, allowlist),
        created_by=ctx.user.id,
        updated_by=ctx.user.id,
    )
    db.add(flag)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise exists from None
    write_audit(
        db,
        "flag.created",
        actor_user_id=ctx.user.id,
        entity_type="feature_flag",
        entity_id=flag.id,
        reason=reason,
        details={"key": key, "to": _snapshot(flag)},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(flag)
    return serialize(db, [flag])[0]


def update_flag(
    db: Session,
    ctx: StaffContext,
    *,
    key: str,
    changes: dict[str, Any],
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    flag = _get(db, key, lock=True)
    if "workspace_allowlist" in changes:
        # Only ids being added must exist: one that was deleted since it was added can stay or go.
        sent = list(dict.fromkeys(changes["workspace_allowlist"]))
        added = [w for w in sent if w not in set(flag.workspace_allowlist)]
        _check_allowlist(db, added)
        changes["workspace_allowlist"] = sent
    before = _snapshot(flag)
    for field, value in changes.items():
        setattr(flag, field, value)
    after = _snapshot(flag)
    if before == after:
        raise _err("NO_CHANGE", "Those are already this flag's settings.", 422)
    flag.updated_by = ctx.user.id
    changed = [k for k in after if before[k] != after[k]]
    write_audit(
        db,
        "flag.updated",
        actor_user_id=ctx.user.id,
        entity_type="feature_flag",
        entity_id=flag.id,
        reason=reason,
        details={
            "key": key,
            "from": {k: before[k] for k in changed},
            "to": {k: after[k] for k in changed},
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(flag)
    return serialize(db, [flag])[0]


def impact(
    db: Session, *, key: str, enabled: bool | None, rollout_pct: int | None
) -> dict[str, Any]:
    """Blast radius of a change before it is made: workspaces with the flag now and after."""
    flag = _get(db, key)
    ids = _workspace_ids(db)
    common = {"key": key, "allowlist": flag.workspace_allowlist, "ids": ids}
    now = affected_workspaces(
        db, enabled=flag.enabled, rollout_pct=flag.rollout_pct, **common  # type: ignore[arg-type]
    )
    after = affected_workspaces(
        db,
        enabled=flag.enabled if enabled is None else enabled,
        rollout_pct=flag.rollout_pct if rollout_pct is None else rollout_pct,
        **common,  # type: ignore[arg-type]
    )
    return {"key": key, "total_workspaces": len(ids), "affected_now": now, "affected_after": after}
