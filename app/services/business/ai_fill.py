from collections.abc import Sequence

from app.db.models.enums import RecordKind
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


def build_record_fill_messages(
    kind: RecordKind, *, name: str | None, industry: str | None, stage: str | None
) -> list[LLMMessage]:
    """Prompt for drafting up to 3 records of one kind. Business context only — no PII."""
    label = kind.value.replace("_", " ")
    system = (
        "You are a startup strategist. Return up to 3 realistic, concrete "
        f"{label} records for the startup. Keep each terse and specific."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nGenerate up to 3 {label} records."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
