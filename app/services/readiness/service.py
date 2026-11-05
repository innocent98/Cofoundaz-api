"""Investor Readiness score: compute, cache, history, inputs (PRD 15.1, 15.5).

Computing is pure SQL reads plus templates: zero LLM tokens, so it is safe to run on read when the
cached row is stale (> 15 minutes), on events and nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.readiness import ReadinessInput, ReadinessScore, ReadinessScoreHistory
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.readiness.config import (
    DIMENSION_LABELS,
    DIMENSION_ORDER,
    DIMENSION_WEIGHTS,
    MIN_RECOMPUTE_INTERVAL_MINUTES,
    NOTIFY_MIN_RISE,
    READINESS_CONFIG_VERSION,
    READY_AT,
)
from app.services.readiness.scoring import (
    Reading,
    apply_cap,
    band_and_verdict,
    dimension_score,
    rank_gaps,
    raw_overall,
)
from app.services.readiness.signals import must_haves, read_signals

HISTORY_RANGES = (30, 90, 365)


def _dimension_payload(readings: list[Reading]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for dim in DIMENSION_ORDER:
        rs = [r for r in readings if r.dimension == dim]
        score = dimension_score(rs)
        out[dim] = {
            "key": dim,
            "label": DIMENSION_LABELS[dim],
            "weight": DIMENSION_WEIGHTS[dim],
            "score": score,
            "signals": [
                {
                    "key": r.key,
                    "label": r.label,
                    "unit": r.unit,
                    "value": round(r.value, 2),
                    "target": r.target,
                    "score": r.score,
                    "href": r.href,
                    "note": r.note or None,
                }
                for r in rs
            ],
        }
    return out


def compute(db: Session, startup: Startup) -> dict[str, Any]:
    readings = read_signals(db, startup)
    dimensions = _dimension_payload(readings)
    raw = raw_overall({d: v["score"] for d, v in dimensions.items()})
    mh = must_haves(db, startup)
    score, capped = apply_cap(raw, mh)
    band, _ = band_and_verdict(score)
    return {
        "score": score,
        "raw_score": raw,
        "band": band,
        "capped": capped,
        "dimensions": dimensions,
        "must_haves": mh,
        "gaps": rank_gaps(readings),
    }


def _dim_scores(dimensions: dict[str, Any]) -> dict[str, int | None]:
    return {d: v["score"] for d, v in dimensions.items()}


def recompute_readiness(db: Session, startup: Startup, *, trigger: str) -> ReadinessScore:
    now = datetime.now(UTC)
    res = compute(db, startup)
    prev = db.query(ReadinessScore).filter_by(startup_id=startup.id).first()
    previous_score = prev.score if prev else None
    changed = (
        prev is None
        or prev.score != res["score"]
        or prev.capped != res["capped"]
        or _dim_scores(prev.dimensions) != _dim_scores(res["dimensions"])
    )
    values = {
        "score": res["score"],
        "raw_score": res["raw_score"],
        "band": res["band"],
        "capped": res["capped"],
        "dimensions": res["dimensions"],
        "must_haves": res["must_haves"],
        "gaps": res["gaps"],
        "trigger": trigger[:40],
        "config_version": READINESS_CONFIG_VERSION,
    }
    db.execute(
        pg_insert(ReadinessScore)
        .values(startup_id=startup.id, **values)
        .on_conflict_do_update(
            index_elements=["startup_id"],
            # ON CONFLICT DO UPDATE bypasses the ORM's onupdate; the 15 minute throttle reads this.
            set_={**values, "updated_at": func.now()},
        )
    )
    if changed:
        db.add(
            ReadinessScoreHistory(
                startup_id=startup.id,
                score=res["score"],
                band=res["band"],
                capped=res["capped"],
                dimension_scores=_dim_scores(res["dimensions"]),
                trigger=trigger[:40],
                config_version=READINESS_CONFIG_VERSION,
            )
        )
    db.flush()
    row = (
        db.query(ReadinessScore)
        .filter_by(startup_id=startup.id)
        .execution_options(populate_existing=True)
        .one()
    )
    if changed:
        event_bus.publish(
            db,
            "readiness.updated",
            {
                "startup_id": str(startup.id),
                "score": row.score,
                "previous_score": previous_score,
                "band": row.band,
                "ready": row.score >= READY_AT,
                "computed_at": now.isoformat(),
            },
        )
        _maybe_notify(db, startup, row, previous_score, now)
    return row


def _maybe_notify(
    db: Session, startup: Startup, row: ReadinessScore, previous: int | None, now: datetime
) -> None:
    """ "Your investor readiness score improved to {score}": founders only, at most once a day, on
    a rise of NOTIFY_MIN_RISE points or a better band than the last time we told them."""
    if previous is None or row.score <= previous:
        return
    if row.notified_at is not None and now - row.notified_at < timedelta(days=1):
        return
    base = row.notified_score if row.notified_score is not None else previous
    prev_band = band_and_verdict(base)[0]
    if row.score - base < NOTIFY_MIN_RISE and row.band == prev_band:
        return
    row.notified_at = now
    row.notified_score = row.score
    event_bus.publish(
        db,
        "readiness.improved",
        {"startup_id": str(startup.id), "score": row.score, "previous_score": previous},
    )


def refresh_readiness(
    db: Session, startup: Startup, *, trigger: str, force: bool = False
) -> ReadinessScore:
    """Recompute unless it ran in the last 15 minutes (the same policy as Health Score)."""
    row = db.query(ReadinessScore).filter_by(startup_id=startup.id).first()
    if (
        not force
        and row is not None
        and row.updated_at is not None
        and datetime.now(UTC) - row.updated_at < timedelta(minutes=MIN_RECOMPUTE_INTERVAL_MINUTES)
    ):
        return row
    return recompute_readiness(db, startup, trigger=trigger)


def score_view(row: ReadinessScore) -> dict[str, Any]:
    _, verdict = band_and_verdict(row.score)
    return {
        "score": row.score,
        "band": row.band,
        "verdict": verdict,
        "ready": row.score >= READY_AT,
        "capped": row.capped,
        "raw_score": row.raw_score,
        "dimensions": [row.dimensions[d] for d in DIMENSION_ORDER if d in row.dimensions],
        "must_haves": row.must_haves,
        "gaps": row.gaps,
        "computed_at": row.updated_at.isoformat(),
        "config_version": row.config_version,
    }


def get_score(db: Session, startup: Startup) -> dict[str, Any]:
    """Cached row, recomputed on read when missing or older than 15 minutes."""
    row = refresh_readiness(db, startup, trigger="lazy_read")
    db.commit()
    return score_view(row)


def force_recompute(db: Session, startup: Startup) -> dict[str, Any]:
    row = db.query(ReadinessScore).filter_by(startup_id=startup.id).first()
    if (
        row is not None
        and row.trigger == "manual"
        and datetime.now(UTC) - row.updated_at < timedelta(seconds=60)
    ):
        raise AppError("RATE_LIMITED", "Recalculated a moment ago. Try again in a minute.", 429)
    row = recompute_readiness(db, startup, trigger="manual")
    db.commit()
    return score_view(row)


def snapshot(db: Session, startup: Startup) -> dict[str, Any]:
    """Share-safe subset (score, band, six dimension scores): what Funding's data room shows an
    investor. No gap notes, no signal detail."""
    row = refresh_readiness(db, startup, trigger="snapshot")
    db.commit()
    return {
        "score": row.score,
        "band": row.band,
        "ready": row.score >= READY_AT,
        "dimensions": [
            {"key": d, "label": DIMENSION_LABELS[d], "score": row.dimensions[d]["score"]}
            for d in DIMENSION_ORDER
        ],
        "computed_at": row.updated_at.isoformat(),
    }


def history(db: Session, startup: Startup, days: int) -> dict[str, Any]:
    if days not in HISTORY_RANGES:
        raise AppError(
            "VALIDATION_ERROR",
            "Unknown range.",
            422,
            field_errors=[{"field": "range", "message": "Use 30, 90 or 365."}],
        )
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        db.query(ReadinessScoreHistory)
        .filter(
            ReadinessScoreHistory.startup_id == startup.id,
            ReadinessScoreHistory.created_at >= since,
        )
        .order_by(ReadinessScoreHistory.created_at.asc())
        .all()
    )
    points = [
        {
            "at": r.created_at.isoformat(),
            "score": r.score,
            "band": r.band,
            "capped": r.capped,
            "dimensions": r.dimension_scores,
        }
        for r in rows
    ]
    markers = []
    last: ReadinessScoreHistory | None = None
    for r in rows:
        if last is not None and last.band != r.band:
            markers.append({"at": r.created_at.isoformat(), "label": f"Moved to {r.band}"})
        if last is not None and last.capped and not r.capped:
            markers.append({"at": r.created_at.isoformat(), "label": "Cap lifted"})
        last = r
    return {"range": days, "points": points, "markers": markers}


# ---- Inputs --------------------------------------------------------------------------------


def input_view(row: ReadinessInput | None) -> dict[str, Any]:
    if row is None:
        return {
            "tam_minor": None,
            "sam_minor": None,
            "som_minor": None,
            "currency": "NGN",
            "source_url": None,
        }
    return {
        "tam_minor": row.tam_minor,
        "sam_minor": row.sam_minor,
        "som_minor": row.som_minor,
        "currency": row.currency,
        "source_url": row.source_url,
    }


def get_inputs(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    return input_view(db.query(ReadinessInput).filter_by(startup_id=startup_id).first())


def put_inputs(
    db: Session, startup: Startup, user_id: uuid.UUID, data: dict[str, Any]
) -> dict[str, Any]:
    row = db.query(ReadinessInput).filter_by(startup_id=startup.id).first()
    if row is None:
        row = ReadinessInput(startup_id=startup.id)
        db.add(row)
    for k, v in data.items():
        setattr(row, k, v)
    row.updated_by_id = user_id
    db.flush()
    refresh_readiness(db, startup, trigger="inputs", force=True)
    db.commit()
    return input_view(row)
