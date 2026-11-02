import uuid

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.enums import StageKind
from app.db.models.sales import Deal, PipelineStage, SalesSettings
from app.platform.activity import write_activity
from app.schemas.sales import SettingsUpdate, StageIn
from app.services.sales.errors import _validation

MAX_STAGES = 10

DEFAULT_STAGES: list[tuple[str, StageKind, int]] = [
    ("New", StageKind.open, 10),
    ("Contacted", StageKind.open, 20),
    ("Qualified", StageKind.open, 40),
    ("Proposal", StageKind.open, 60),
    ("Negotiation", StageKind.open, 80),
    ("Closed Won", StageKind.won, 100),
    ("Closed Lost", StageKind.lost, 0),
]


def _lock(db: Session, startup_id: uuid.UUID) -> None:
    """Serialise stage writes per workspace (seeding and replacing) for this transaction."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"sales-stages:{startup_id}"}
    )


def _live(db: Session, startup_id: uuid.UUID) -> list[PipelineStage]:
    return list(
        db.scalars(
            select(PipelineStage)
            .where(PipelineStage.startup_id == startup_id, PipelineStage.archived_at.is_(None))
            .order_by(PipelineStage.position, PipelineStage.created_at)
            .execution_options(populate_existing=True)
        )
    )


def ensure_stages(db: Session, startup_id: uuid.UUID) -> list[PipelineStage]:
    live = _live(db, startup_id)
    if live:
        return live
    _lock(db, startup_id)
    live = _live(db, startup_id)  # a concurrent request may have seeded while we waited
    if live:
        return live
    for pos, (name, kind, prob) in enumerate(DEFAULT_STAGES):
        db.add(
            PipelineStage(
                startup_id=startup_id, name=name, position=pos, kind=kind, win_probability=prob
            )
        )
    db.flush()
    return _live(db, startup_id)


def list_stages(db: Session, startup_id: uuid.UUID) -> list[PipelineStage]:
    return ensure_stages(db, startup_id)


def _validate_items(
    items: list[StageIn], existing: dict[uuid.UUID, PipelineStage]
) -> set[uuid.UUID]:
    if not 1 <= len(items) <= MAX_STAGES:
        raise _validation("stages", f"A pipeline needs between 1 and {MAX_STAGES} stages.")
    seen_ids: set[uuid.UUID] = set()
    seen_names: set[str] = set()
    for it in items:
        if it.id is not None:
            if it.id not in existing or it.id in seen_ids:
                raise _validation("stages", "Unknown stage.")
            seen_ids.add(it.id)
            if existing[it.id].kind != it.kind:
                raise _validation("stages", "A stage's type can't be changed.")
        key = it.name.strip().lower()
        if key in seen_names:
            raise _validation("stages", f"Stage names must be unique: {it.name}.")
        seen_names.add(key)
    if sum(i.kind == StageKind.won for i in items) != 1:
        raise _validation("stages", "The pipeline needs exactly one Won stage.")
    if sum(i.kind == StageKind.lost for i in items) != 1:
        raise _validation("stages", "The pipeline needs exactly one Lost stage.")
    return seen_ids


def _check_removable(db: Session, removed: list[PipelineStage]) -> None:
    for stage in removed:
        if stage.kind != StageKind.open:
            raise _validation("stages", f"{stage.name} can't be removed.")
        has_deals = db.scalar(
            select(func.count()).select_from(Deal).where(Deal.stage_id == stage.id)
        )
        if has_deals:
            raise _validation("stages", f"Move the deals out of {stage.name} first.")


def replace_stages(
    db: Session,
    startup_id: uuid.UUID,
    items: list[StageIn],
    *,
    actor_id: uuid.UUID | None = None,
) -> list[PipelineStage]:
    ensure_stages(db, startup_id)
    _lock(db, startup_id)
    existing = {s.id: s for s in _live(db, startup_id)}

    seen_ids = _validate_items(items, existing)
    removed = [s for sid, s in existing.items() if sid not in seen_ids]
    _check_removable(db, removed)

    for stage in removed:
        stage.archived_at = func.now()
    for pos, it in enumerate(items):
        if it.id is not None:
            row = existing[it.id]
            row.name, row.position, row.win_probability = it.name, pos, it.win_probability
        else:
            db.add(
                PipelineStage(
                    startup_id=startup_id,
                    name=it.name,
                    position=pos,
                    kind=it.kind,
                    win_probability=it.win_probability,
                )
            )
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.stages.updated",
        summary="Updated the sales pipeline stages",
        actor_user_id=actor_id,
        entity_type="sales_stage",
        meta={"stage_count": len(items), "removed": len(removed)},
    )
    return _live(db, startup_id)


def get_settings(db: Session, startup_id: uuid.UUID) -> SalesSettings:
    db.execute(
        pg_insert(SalesSettings)
        .values(startup_id=startup_id, stall_days=14)
        .on_conflict_do_nothing(index_elements=["startup_id"])
    )
    return db.execute(
        select(SalesSettings)
        .where(SalesSettings.startup_id == startup_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def update_settings(
    db: Session,
    startup_id: uuid.UUID,
    data: SettingsUpdate,
    *,
    actor_id: uuid.UUID | None = None,
) -> SalesSettings:
    row = get_settings(db, startup_id)
    fields = data.model_fields_set
    changed: list[str] = []
    if "stall_days" in fields:
        if data.stall_days is None:
            raise _validation("stall_days", "Stall days is required.")
        row.stall_days = data.stall_days
        changed.append("stall_days")
    if "outreach_postal_address" in fields:
        addr = (data.outreach_postal_address or "").strip()
        row.outreach_postal_address = addr or None
        changed.append("outreach_postal_address")
    db.flush()
    if changed:
        write_activity(
            db,
            startup_id=startup_id,
            action="sales.settings.updated",
            summary="Updated the sales settings",
            actor_user_id=actor_id,
            entity_type="sales_settings",
            meta={"fields": changed},
        )
    return row
