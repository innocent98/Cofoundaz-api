"""Pure scoring maths: no database, so every rule is unit-testable."""

from dataclasses import dataclass
from typing import Any

from app.services.readiness.config import (
    BANDS,
    CAP_SCORE,
    DIMENSION_WEIGHTS,
    GAP_LIMIT,
    GAPS_PER_DIMENSION,
    READY_AT,
)


@dataclass(frozen=True)
class Reading:
    key: str
    dimension: str
    label: str
    unit: str
    weight: float
    value: float
    target: float
    score: float  # 0..100
    href: str
    note: str  # gap note, already filled with the real numbers


def signal_score(value: float, target: float) -> float:
    return 100.0 if target <= 0 else round(min(value / target, 1.0) * 100, 1)


def dimension_score(readings: list[Reading]) -> int | None:
    """Weighted mean of a dimension's applicable signals; None = nothing to judge yet."""
    total = sum(r.weight for r in readings)
    if not readings or total <= 0:
        return None
    return round(sum(r.score * r.weight for r in readings) / total)


def raw_overall(dim_scores: dict[str, int | None]) -> int:
    """Weighted mean over the dimensions that can be scored (weights renormalised)."""
    scored = {d: s for d, s in dim_scores.items() if s is not None}
    total = sum(DIMENSION_WEIGHTS[d] for d in scored)
    if total == 0:
        return 0
    return round(sum(DIMENSION_WEIGHTS[d] * s for d, s in scored.items()) / total)


def apply_cap(raw: int, must_haves: list[dict[str, Any]]) -> tuple[int, bool]:
    """D-IR-2: no 70+ until every must-have exists. Returns (score, capped)."""
    if raw >= READY_AT and any(not m["met"] for m in must_haves):
        return CAP_SCORE, True
    return raw, False


def band_and_verdict(score: int) -> tuple[str, str]:
    for low, key, verdict in BANDS:
        if score >= low:
            return key, verdict
    return BANDS[-1][1], BANDS[-1][2]


def rank_gaps(readings: list[Reading]) -> list[dict[str, Any]]:
    """Top gaps by the overall points hitting target would earn; at most two per dimension so one
    weak area cannot hide the rest."""
    by_dim: dict[str, list[Reading]] = {}
    for r in readings:
        by_dim.setdefault(r.dimension, []).append(r)
    scored_dims = {d: sum(r.weight for r in rs) for d, rs in by_dim.items()}
    weight_sum = sum(DIMENSION_WEIGHTS[d] for d in scored_dims)
    candidates = []
    for r in readings:
        if r.score >= 100:
            continue
        share = DIMENSION_WEIGHTS[r.dimension] / weight_sum * (r.weight / scored_dims[r.dimension])
        candidates.append((share * (100 - r.score), r))
    candidates.sort(key=lambda c: (-c[0], c[1].key))
    out: list[dict[str, Any]] = []
    taken: dict[str, int] = {}
    for lift, r in candidates:
        if taken.get(r.dimension, 0) >= GAPS_PER_DIMENSION:
            continue
        taken[r.dimension] = taken.get(r.dimension, 0) + 1
        out.append(
            {
                "signal_key": r.key,
                "dimension": r.dimension,
                "note": r.note,
                "href": r.href,
                "lift": max(1, round(lift)),
            }
        )
        if len(out) == GAP_LIMIT:
            break
    return out
