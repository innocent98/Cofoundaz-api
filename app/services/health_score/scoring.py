from app.db.models.enums import Dimension
from app.services.health_score.config import BANDS, DIMENSION_WEIGHTS


def weighted_overall(dimension_scores: dict[str, int]) -> int:
    total = sum(DIMENSION_WEIGHTS[d.value] * dimension_scores.get(d.value, 50) for d in Dimension)
    return max(0, min(100, round(total)))


def band_for(score: int) -> str:
    for low, high, key in BANDS:
        if low <= score <= high:
            return key
    return BANDS[-1][2]
