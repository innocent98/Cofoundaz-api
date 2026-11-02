import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.services.health_score.config import DIMENSION_WEIGHTS, RECOMMENDATION_CATALOG


def generate_recommendations(
    db: Session,
    startup_id: uuid.UUID,
    dimension_scores: dict[str, int],
    live: list[dict[str, Any]] | None = None,
) -> int:
    """Upsert pending recommendations: catalog entries for weak assessment dimensions plus
    `live` ones for below-target live signals (D-HEA-4), ranked together by estimated lift."""
    # Candidate catalog entries for weak dimensions, ranked by weighted gap.
    candidates = []
    for dim, entries in RECOMMENDATION_CATALOG.items():
        score = dimension_scores.get(dim, 50)
        for e in entries:
            if score < e["triggers_below"]:
                weight = DIMENSION_WEIGHTS[dim]
                rank_val = (e["triggers_below"] - score) * weight
                candidates.append((rank_val, e["estimated_lift"], dim, e))
    for e in live or []:
        # Rank live recs on the same scale as catalog ones (lift is in overall points).
        candidates.append((e["estimated_lift"] * 10, e["estimated_lift"], e["dimension"], e))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    candidate_keys = {e["key"] for *_, e in candidates}

    existing = {
        r.key: r for r in db.query(HealthRecommendation).filter_by(startup_id=startup_id).all()
    }

    # Delete unacted pendings whose dimension recovered (no longer a candidate).
    for key, row in list(existing.items()):
        if row.status == RecommendationStatus.pending and key not in candidate_keys:
            db.delete(row)
            del existing[key]

    created = 0
    for priority, (_, _, dim, e) in enumerate(candidates, start=1):
        existing_row = existing.get(e["key"])
        if existing_row is None:
            created += 1
            db.add(
                HealthRecommendation(
                    startup_id=startup_id,
                    dimension=dim,
                    key=e["key"],
                    title=e["title"],
                    body=e["body"],
                    estimated_lift=e["estimated_lift"],
                    effort=e["effort"],
                    status=RecommendationStatus.pending,
                    priority=priority,
                )
            )
        elif existing_row.status == RecommendationStatus.dismissed:
            continue  # never resurrect a user decision
        else:  # pending or accepted → refresh priority/lift, keep status
            existing_row.priority = priority
            existing_row.estimated_lift = e["estimated_lift"]
    db.flush()
    return created
