from typing import Any

from app.core.errors import AppError
from app.services.assessment.bank import Bank, QType, Question


def _cond(node: dict, answers: dict, startup: Any) -> bool:
    if "all" in node:
        return all(_cond(c, answers, startup) for c in node["all"])
    if "any" in node:
        return any(_cond(c, answers, startup) for c in node["any"])
    if "field" in node:
        attr = getattr(startup, node["field"], None)
        val = attr.value if attr is not None and hasattr(attr, "value") else attr
        if "in" in node:
            return val in node["in"]
        return val == node.get("eq")
    if "answer" in node:
        val = answers.get(node["answer"])
        if val is None:
            return False
        if "in" in node:
            return val in node["in"]
        return bool(val == node.get("eq"))
    return True


def is_applicable(question: Question, answers: dict, startup: Any) -> bool:
    if question.show_if is None:
        return True
    return _cond(question.show_if, answers, startup)


def next_question(bank: Bank, answers: dict, startup: Any) -> Question | None:
    for q in bank.questions:
        if q.key in answers:
            continue
        if is_applicable(q, answers, startup):
            return q
    return None


def validate_answer(question: Question, value: Any) -> None:
    err = AppError("INVALID_ANSWER", "That answer isn't valid for this question.", 422)
    if question.qtype == QType.SINGLE_CHOICE:
        if value not in {o["value"] for o in (question.options or [])}:
            raise err
    elif question.qtype == QType.MULTI_CHOICE:
        opts = {o["value"] for o in (question.options or [])}
        if not isinstance(value, list) or not value or any(v not in opts for v in value):
            raise err
    elif question.qtype == QType.SCALE_1_5:
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 5):
            raise err
    elif question.qtype == QType.NUMERIC_CURRENCY:
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise err
    elif question.qtype == QType.SHORT_TEXT:
        if not isinstance(value, str) or not value.strip():
            raise err
    else:
        raise err
