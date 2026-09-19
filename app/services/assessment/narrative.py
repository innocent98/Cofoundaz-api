from app.platform.llm import LLMMessage


def build_narrative_messages(
    *,
    dimension_scores: dict[str, int],
    overall: int,
    industry: str | None,
    stage: str | None,
) -> list[LLMMessage]:
    """Turn a scored assessment + startup profile into an LLM prompt.

    Data minimization: only business signals go in — no names, emails, or identifiers.
    """
    dims = ", ".join(f"{k}: {v}/100" for k, v in sorted(dimension_scores.items()))
    system = (
        "You are an experienced startup advisor writing a concise, encouraging but honest "
        "assessment narrative for a founder. Write 2-3 short paragraphs. No preamble, no headings."
    )
    user = (
        f"Industry: {industry or 'unspecified'}. Stage: {stage or 'unspecified'}. "
        f"Overall readiness: {overall}/100. Dimension scores: {dims}. "
        "Write the narrative: what is strong, and what to prioritise next."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
