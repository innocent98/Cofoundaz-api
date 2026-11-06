"""The cap table: holders, entries, the ledger view, the round model, scenarios and the audit trail.

Founder only (the routes enforce it). All maths is in `cap_engine`; this module reads rows, builds
engine inputs and shapes output. Every write takes the workspace row lock, so two edits to one cap
table never interleave, and writes one `cap_audit` row with before and after.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.cap_table import CapAudit, CapEntry, CapHolder, CapScenario
from app.db.models.startup import Startup
from app.schemas.cap_table import (
    EntryCreate,
    EntryUpdate,
    HolderCreate,
    HolderUpdate,
    ModelRequest,
    ScenarioCreate,
    as_dict,
)
from app.services.funding import cap_engine as eng
from app.services.funding import rounds as rounds_svc
from app.services.funding.errors import _validation

DISCLAIMER = (
    "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed "
    "documents. Check them with a lawyer before you rely on these numbers."
)
MAX_HOLDERS = 500
MAX_ENTRIES = 2000
MAX_SCENARIOS = 100


def _today() -> date:
    return datetime.now(UTC).date()


def lock_workspace(db: Session, startup_id: uuid.UUID) -> None:
    """Serialise every write to one workspace's cap table."""
    db.query(Startup.id).filter(Startup.id == startup_id).with_for_update().one()


def currency(db: Session, startup_id: uuid.UUID) -> str:
    rnd = rounds_svc.latest_round(db, startup_id)
    return rnd.currency if rnd else "NGN"


# ---------------------------------------------------------------- serialisation + audit


def _iso(v: date | datetime | None) -> str | None:
    return v.isoformat() if v else None


def holder_dict(h: CapHolder) -> dict[str, Any]:
    return {"id": str(h.id), "name": h.name, "kind": h.kind, "email": h.email, "notes": h.notes}


def entry_dict(e: CapEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "holder_id": str(e.holder_id),
        "security": e.security,
        "shares": e.shares,
        "price_minor": e.price_minor,
        "amount_minor": e.amount_minor,
        "valuation_cap_minor": e.valuation_cap_minor,
        "discount_bps": e.discount_bps,
        "safe_kind": e.safe_kind,
        "mfn": e.mfn,
        "interest_bps": e.interest_bps,
        "issued_on": _iso(e.issued_on),
        "vesting_cliff_months": e.vesting_cliff_months,
        "vesting_months": e.vesting_months,
        "vesting_start_on": _iso(e.vesting_start_on),
        "notes": e.notes,
    }


def _audit(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    db.add(
        CapAudit(
            created_at=datetime.now(
                UTC
            ),  # a wall-clock stamp, so order is exact inside one transaction
            startup_id=startup_id,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
        )
    )


def audit_log(db: Session, startup_id: uuid.UUID, *, limit: int, offset: int) -> dict[str, Any]:
    q = db.query(CapAudit).filter_by(startup_id=startup_id)
    rows = q.order_by(CapAudit.created_at.desc(), CapAudit.id).limit(limit).offset(offset).all()
    return {
        "total": q.count(),
        "items": [
            {
                "id": str(r.id),
                "at": r.created_at.isoformat(),
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "entity_type": r.entity_type,
                "entity_id": str(r.entity_id),
                "action": r.action,
                "before": r.before,
                "after": r.after,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------- holders


def get_holder(db: Session, startup_id: uuid.UUID, holder_id: uuid.UUID) -> CapHolder:
    row = db.query(CapHolder).filter_by(id=holder_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_holder(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, data: HolderCreate
) -> CapHolder:
    lock_workspace(db, startup_id)
    if db.query(CapHolder).filter_by(startup_id=startup_id).count() >= MAX_HOLDERS:
        raise _validation("name", f"A cap table can have at most {MAX_HOLDERS} holders.")
    if data.kind == "pool" and (
        db.query(CapHolder).filter_by(startup_id=startup_id, kind="pool").first() is not None
    ):
        raise AppError("POOL_EXISTS", "The option pool already exists. Add options to it.", 409)
    row = CapHolder(
        startup_id=startup_id,
        name=data.name,
        kind=data.kind,
        email=data.email,
        notes=data.notes,
    )
    db.add(row)
    db.flush()
    _audit(db, startup_id, actor_id, "holder", row.id, "create", None, holder_dict(row))
    return row


def update_holder(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID,
    holder_id: uuid.UUID,
    data: HolderUpdate,
) -> CapHolder:
    lock_workspace(db, startup_id)
    row = get_holder(db, startup_id, holder_id)
    before = holder_dict(row)
    for field in data.model_fields_set:
        setattr(row, field, getattr(data, field))
    db.flush()
    _audit(db, startup_id, actor_id, "holder", row.id, "update", before, holder_dict(row))
    return row


def delete_holder(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, holder_id: uuid.UUID
) -> None:
    """Removes the holder and their entries; each is audited with its before-image."""
    lock_workspace(db, startup_id)
    row = get_holder(db, startup_id, holder_id)
    for e in db.query(CapEntry).filter_by(holder_id=row.id).all():
        _audit(db, startup_id, actor_id, "entry", e.id, "delete", entry_dict(e), None)
        db.delete(e)
    _audit(db, startup_id, actor_id, "holder", row.id, "delete", holder_dict(row), None)
    db.delete(row)
    db.flush()


# ---------------------------------------------------------------- entries


def get_entry(db: Session, startup_id: uuid.UUID, entry_id: uuid.UUID) -> CapEntry:
    row = db.query(CapEntry).filter_by(id=entry_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def _check_entry_for_holder(holder: CapHolder, security: str) -> None:
    if holder.kind == "pool" and security != "option":
        raise _validation("security", "The option pool holds options only.")


def _apply(row: CapEntry, data: EntryCreate) -> None:
    safe_kind = data.safe_kind or ("post_money" if data.security in eng.CONVERTIBLES else None)
    for name in (
        "security",
        "shares",
        "price_minor",
        "amount_minor",
        "valuation_cap_minor",
        "discount_bps",
        "mfn",
        "interest_bps",
        "issued_on",
        "vesting_cliff_months",
        "vesting_months",
        "vesting_start_on",
        "notes",
    ):
        setattr(row, name, getattr(data, name))
    row.safe_kind = safe_kind
    if data.vesting_months is not None and row.vesting_cliff_months is None:
        row.vesting_cliff_months = 0


def create_entry(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, data: EntryCreate
) -> CapEntry:
    lock_workspace(db, startup_id)
    holder = get_holder(db, startup_id, data.holder_id)
    _check_entry_for_holder(holder, data.security)
    if db.query(CapEntry).filter_by(startup_id=startup_id).count() >= MAX_ENTRIES:
        raise _validation("security", f"A cap table can have at most {MAX_ENTRIES} entries.")
    row = CapEntry(startup_id=startup_id, holder_id=holder.id)
    _apply(row, data)
    db.add(row)
    db.flush()
    _audit(db, startup_id, actor_id, "entry", row.id, "create", None, entry_dict(row))
    return row


def update_entry(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID,
    entry_id: uuid.UUID,
    data: EntryUpdate,
) -> CapEntry:
    lock_workspace(db, startup_id)
    row = db.query(CapEntry).filter_by(id=entry_id, startup_id=startup_id).with_for_update().first()
    if row is None:
        raise NotFound()
    before = entry_dict(row)
    merged = {k: v for k, v in before.items() if k != "id"}
    merged.update({k: getattr(data, k) for k in data.model_fields_set})
    if data.holder_id is None:
        merged["holder_id"] = row.holder_id
    try:
        full = EntryCreate.model_validate(merged)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise _validation(
            str(first["loc"][-1]) if first["loc"] else "security",
            str(first["msg"]).removeprefix("Value error, "),
        ) from None
    holder = get_holder(db, startup_id, full.holder_id)
    _check_entry_for_holder(holder, full.security)
    row.holder_id = holder.id
    _apply(row, full)
    db.flush()
    _audit(db, startup_id, actor_id, "entry", row.id, "update", before, entry_dict(row))
    return row


def delete_entry(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, entry_id: uuid.UUID
) -> None:
    lock_workspace(db, startup_id)
    row = db.query(CapEntry).filter_by(id=entry_id, startup_id=startup_id).with_for_update().first()
    if row is None:
        raise NotFound()
    _audit(db, startup_id, actor_id, "entry", row.id, "delete", entry_dict(row), None)
    db.delete(row)
    db.flush()


# ---------------------------------------------------------------- ledger view


def _load(db: Session, startup_id: uuid.UUID) -> tuple[list[CapHolder], list[CapEntry]]:
    holders = (
        db.query(CapHolder).filter_by(startup_id=startup_id).order_by(CapHolder.created_at).all()
    )
    entries = (
        db.query(CapEntry).filter_by(startup_id=startup_id).order_by(CapEntry.created_at).all()
    )
    return holders, entries


def engine_inputs(
    holders: list[CapHolder], entries: list[CapEntry]
) -> tuple[list[eng.Position], list[eng.Convertible]]:
    by_id = {h.id: h for h in holders}
    positions: list[eng.Position] = []
    convertibles: list[eng.Convertible] = []
    for e in entries:
        h = by_id[e.holder_id]
        key = str(h.id)
        if e.security in eng.CONVERTIBLES:
            convertibles.append(
                eng.Convertible(
                    key=str(e.id),
                    name=h.name,
                    security=e.security,
                    principal_minor=e.amount_minor or 0,
                    interest_bps=e.interest_bps or 0,
                    issued_on=e.issued_on,
                    cap_minor=e.valuation_cap_minor,
                    discount_bps=e.discount_bps,
                    safe_kind=e.safe_kind or "post_money",
                    mfn=e.mfn,
                )
            )
        else:
            positions.append(eng.Position(key, h.name, h.kind, e.security, e.shares or 0))
    return positions, convertibles


def ledger(
    db: Session, startup_id: uuid.UUID, *, fully_diluted: bool, as_of: date | None = None
) -> dict[str, Any]:
    as_of = as_of or _today()
    holders, entries = _load(db, startup_id)
    positions, convertibles = engine_inputs(holders, entries)
    shares = eng.holder_shares(positions, fully_diluted=fully_diluted)
    bps = eng.largest_remainder_bps(shares)
    issued = sum(p.shares for p in positions if p.security != "option")
    options = sum(p.shares for p in positions if p.security == "option" and p.kind != "pool")
    pool = sum(p.shares for p in positions if p.kind == "pool")
    entries_by_holder: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for e in entries:
        item = entry_dict(e)
        if e.security in eng.SHARE_SECURITIES:
            item["vested_shares"] = eng.vested_shares(
                e.shares or 0,
                start=e.vesting_start_on,
                cliff_months=e.vesting_cliff_months or 0,
                months=e.vesting_months or 0,
                as_of=as_of,
            )
        else:
            item["converting_amount_minor"] = eng.converting_amount(
                next(c for c in convertibles if c.key == str(e.id)), as_of
            )
        entries_by_holder.setdefault(e.holder_id, []).append(item)
    rows = []
    for h in holders:
        key = str(h.id)
        rows.append(
            {
                **holder_dict(h),
                "shares": shares.get(key, 0),
                "pct_bps": bps.get(key) if key in shares else None,
                "entries": entries_by_holder.get(h.id, []),
            }
        )
    rows.sort(key=lambda r: (-r["shares"], r["name"], r["id"]))
    return {
        "currency": currency(db, startup_id),
        "fully_diluted": fully_diluted,
        "as_of": as_of.isoformat(),
        "empty": not holders,
        "totals": {
            "issued_shares": issued,
            "granted_option_shares": options,
            "unallocated_pool_shares": pool,
            "fully_diluted_shares": issued + options + pool,
            "denominator_shares": sum(shares.values()),
            "convertible_amount_minor": sum(eng.converting_amount(c, as_of) for c in convertibles),
        },
        "holders": rows,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------- the round model


def _names(positions: list[eng.Position], convertibles: list[eng.Convertible]) -> dict[str, str]:
    out = {p.key: p.name for p in positions}
    out.update({c.key: c.name for c in convertibles})
    return out


def run_model(db: Session, startup_id: uuid.UUID, req: ModelRequest) -> dict[str, Any]:
    as_of = req.as_of or _today()
    holders, entries = _load(db, startup_id)
    positions, convertibles = engine_inputs(holders, entries)
    rounds = [
        eng.RoundInput(r.name, r.new_money_minor, r.pre_money_minor, r.pool_target_bps)
        for r in req.rounds
    ]
    try:
        after, steps = eng.model_rounds(positions, convertibles, rounds, as_of=as_of)
    except eng.EngineError as exc:
        raise AppError(exc.code, exc.message, 422) from None
    before_rows = {r["ref"]: r for r in eng.ownership_table(positions)}
    after_rows = {r["ref"]: r for r in eng.ownership_table(after)}
    names = _names(after, convertibles)
    dilution = []
    for key in sorted(after_rows, key=lambda k: (-after_rows[k]["shares"], names.get(k, k))):
        a = after_rows[key]
        b = before_rows.get(key)
        dilution.append(
            {
                "ref": key,
                "name": a["name"],
                "kind": a["kind"],
                "before_shares": b["shares"] if b else 0,
                "before_bps": b["bps"] if b else 0,
                "after_shares": a["shares"],
                "after_bps": a["bps"],
                "change_bps": a["bps"] - (b["bps"] if b else 0),
            }
        )
    first, last = steps[0], steps[-1]
    return {
        "currency": currency(db, startup_id),
        "as_of": as_of.isoformat(),
        "headline": {
            "founders_before_bps": first.founders_before_bps,
            "founders_after_bps": last.founders_after_bps,
        },
        "steps": [s.__dict__ for s in steps],
        "dilution": dilution,
        "total_shares_before": sum(p.shares for p in positions),
        "total_shares_after": sum(p.shares for p in after),
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------- scenarios


def list_scenarios(db: Session, startup_id: uuid.UUID) -> list[CapScenario]:
    return (
        db.query(CapScenario)
        .filter_by(startup_id=startup_id)
        .order_by(CapScenario.created_at.desc(), CapScenario.id)
        .all()
    )


def get_scenario(db: Session, startup_id: uuid.UUID, scenario_id: uuid.UUID) -> CapScenario:
    row = db.query(CapScenario).filter_by(id=scenario_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def scenario_dict(s: CapScenario, *, full: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(s.id),
        "name": s.name,
        "created_at": s.created_at.isoformat(),
        "headline": s.result.get("headline"),
    }
    if full:
        out["inputs"] = s.inputs
        out["result"] = s.result
    return out


def create_scenario(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, data: ScenarioCreate
) -> CapScenario:
    lock_workspace(db, startup_id)
    if db.query(CapScenario).filter_by(startup_id=startup_id).count() >= MAX_SCENARIOS:
        raise _validation("name", f"You can keep at most {MAX_SCENARIOS} scenarios.")
    result = run_model(db, startup_id, data.inputs)
    row = CapScenario(
        created_at=datetime.now(UTC),
        startup_id=startup_id,
        name=data.name,
        inputs=as_dict(data.inputs),
        result=result,
        created_by_id=actor_id,
    )
    db.add(row)
    db.flush()
    _audit(
        db, startup_id, actor_id, "scenario", row.id, "create", None, scenario_dict(row, full=False)
    )
    return row


def rename_scenario(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, scenario_id: uuid.UUID, name: str
) -> CapScenario:
    lock_workspace(db, startup_id)
    row = get_scenario(db, startup_id, scenario_id)
    before = scenario_dict(row, full=False)
    row.name = name
    db.flush()
    _audit(
        db,
        startup_id,
        actor_id,
        "scenario",
        row.id,
        "update",
        before,
        scenario_dict(row, full=False),
    )
    return row


def delete_scenario(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, scenario_id: uuid.UUID
) -> None:
    lock_workspace(db, startup_id)
    row = get_scenario(db, startup_id, scenario_id)
    _audit(
        db, startup_id, actor_id, "scenario", row.id, "delete", scenario_dict(row, full=False), None
    )
    db.delete(row)
    db.flush()
