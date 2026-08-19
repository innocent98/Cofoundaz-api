from typing import Any

from app.db.models.enums import Dimension
from app.services.assessment.bank import Bank, QType, Question
from app.services.assessment.engine import is_applicable


def _points(q: Question, value: Any) -> int:
    if q.qtype == QType.SCALE_1_5:
        return int(value)
    if q.qtype == QType.SINGLE_CHOICE:
        return int(q.scoring.get(value, 0))
    if q.qtype == QType.MULTI_CHOICE:
        earned = sum(int(q.scoring.get(v, 0)) for v in value)
        return min(earned, int(q.scoring["max"]))
    return 0


def score(bank: Bank, answers: dict, startup: Any) -> dict:
    dim_scores: dict[str, int] = {}
    for dim in Dimension:
        earned = maxsum = 0
        for q in bank.questions:
            if q.dimension != dim:
                continue
            if q.scoring.get("max", 0) <= 0:
                continue  # informational
            if q.key not in answers or not is_applicable(q, answers, startup):
                continue
            earned += _points(q, answers[q.key])
            maxsum += int(q.scoring["max"])
        pct = round(100 * earned / maxsum) if maxsum else 50
        dim_scores[dim.value] = min(100, max(0, pct))

    overall = round(sum(dim_scores.values()) / len(dim_scores))
    top = max(dim_scores, key=lambda d: dim_scores[d])
    low = min(dim_scores, key=lambda d: dim_scores[d])
    narrative = (
        f"Your strongest area is {top} ({dim_scores[top]}). "
        f"Focus next on {low} ({dim_scores[low]})."
    )
    return {"dimension_scores": dim_scores, "overall_provisional": overall, "narrative": narrative}
