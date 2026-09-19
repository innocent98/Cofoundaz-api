from collections.abc import Sequence

from app.platform.llm import LLMMessage
from app.services.business.canvas_defs import BlockDef


def build_canvas_fill_messages(
    *, name: str | None, industry: str | None, stage: str | None, blocks: Sequence[BlockDef]
) -> list[LLMMessage]:
    """Prompt for drafting a business canvas. Business context only — no PII."""
    lines = "\n".join(
        f"- {b.key} ({b.label}): {'a list of short strings' if b.kind == 'list' else 'a short string'}"
        for b in blocks
    )
    system = (
        "You are a startup strategist drafting a business canvas. Return concise, concrete content "
        "for each block. Lists should be short arrays of terse phrases; text blocks a sentence or two."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nFill every block:\n{lines}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
