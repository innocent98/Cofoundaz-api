import json
from typing import Any

from app.platform.llm import LLMMessage

_MAX_SIBLING_CHARS = 600
_MAX_CONTEXT_CHARS = 8000


def build_draft_messages(
    *,
    template_name: str,
    heading: str,
    doc_title: str,
    context: dict[str, Any],
    siblings: list[tuple[str, str]],
) -> list[LLMMessage]:
    """Prompt for one empty section of a templated document. Business context only (the same
    PII-free snapshot the plan generator uses); the sections already written are given so the
    draft agrees with them. Returns the section BODY only."""
    system = (
        f"You are drafting one section of a startup's {template_name}. Write clear, concrete "
        "prose for the section BODY only: no heading, no preamble, no closing remarks. Use only "
        "the provided context; if it does not support a claim, leave the claim out rather than "
        "inventing a fact, figure or name. Keep it to about 120 to 250 words."
    )
    written = "\n".join(f"- {h}: {b[:_MAX_SIBLING_CHARS]}" for h, b in siblings if b.strip())
    ctx = json.dumps(context, ensure_ascii=False)[:_MAX_CONTEXT_CHARS]
    user = (
        f"Document: {doc_title}\nSection to write: {heading}\n\n"
        f"Sections already written:\n{written or '(none yet)'}\n\n"
        f"Startup context (JSON):\n{ctx}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
