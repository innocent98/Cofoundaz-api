"""Business Builder AI assists beyond whole-artifact fill (PRD 08, slice b).

- Per-section assist: draft ONE canvas block. Append-only (D-BB-4): list blocks gain up to 3 new,
  de-duplicated items; a text block is only written when empty. Never rewrites what's there.
- Mission & Vision "Give me 3 options": variants returned in the job result, never written
  (the founder's "Use this" is an ordinary canvas PUT).
- Personas from validation notes (Module 09): interview segments, notes and quotes, without
  interviewee names, plus the assumptions; new personas are appended next to existing ones.
- Pricing advisor: willingness-to-pay prompts + a suggested model and tiers from competitor prices;
  advice only, applied by the founder through the normal pricing record CRUD.

Prompts carry business context only, never member or interviewee PII.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.enums import PricingModelType, RecordKind
from app.db.models.startup import Startup
from app.db.models.validation import Assumption, Interview
from app.platform.llm import LLMMessage
from app.services.business.canvas_defs import CANVAS_BLOCKS, BlockDef

MAX_SECTION_ITEMS = 3
MAX_INTERVIEWS = 10
NOTE_CHARS = 600


def _context_line(startup: Startup) -> str:
    stage = startup.stage.value if startup.stage else "unspecified"
    return (
        f"Startup: {startup.name or 'unnamed'}. Industry: {startup.industry or 'unspecified'}. "
        f"Stage: {stage}."
    )


def block_def(canvas: BusinessCanvas, key: str) -> BlockDef | None:
    return next((b for b in CANVAS_BLOCKS[canvas.type] if b.key == key), None)


# --- per-section assist ------------------------------------------------------------------------


def section_schema(block: BlockDef) -> dict[str, Any]:
    prop = (
        {"items": {"type": "array", "maxItems": MAX_SECTION_ITEMS, "items": {"type": "string"}}}
        if block.kind == "list"
        else {"text": {"type": "string"}}
    )
    return {
        "type": "object",
        "properties": prop,
        "required": list(prop),
        "additionalProperties": False,
    }


def build_section_messages(
    startup: Startup, canvas: BusinessCanvas, block: BlockDef
) -> list[LLMMessage]:
    existing = canvas.blocks.get(block.key)
    others = "\n".join(
        f"- {b.label}: {canvas.blocks.get(b.key)}"
        for b in CANVAS_BLOCKS[canvas.type]
        if b.key != block.key and canvas.blocks.get(b.key)
    )
    ask = (
        f"Suggest up to {MAX_SECTION_ITEMS} NEW terse items for the '{block.label}' block that "
        f"are not already listed. Already listed: {existing or 'nothing yet'}."
        if block.kind == "list"
        else f"Draft the '{block.label}' block in one or two sentences."
    )
    system = (
        "You are a startup strategist helping a founder with one section of a business canvas. "
        "Be concrete and specific to this startup. Never repeat an existing item."
    )
    user = f"{_context_line(startup)}\nOther sections so far:\n{others or '(none)'}\n{ask}"
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def merge_section(canvas: BusinessCanvas, block: BlockDef, result: dict) -> list[str]:
    """Append-only merge; returns what was added (empty list = nothing new). Mutates blocks."""
    blocks = dict(canvas.blocks or {})
    if block.kind == "list":
        current = list(blocks.get(block.key) or [])
        seen = {i.strip().casefold() for i in current}
        added: list[str] = []
        for raw in (result.get("items") or [])[:MAX_SECTION_ITEMS]:
            item = str(raw).strip()
            if item and item.casefold() not in seen:
                seen.add(item.casefold())
                added.append(item)
        if added:
            blocks[block.key] = current + added
            canvas.blocks = blocks
        return added
    text = str(result.get("text") or "").strip()
    if blocks.get(block.key) or not text:
        return []
    blocks[block.key] = text
    canvas.blocks = blocks
    return [text]


# --- Mission & Vision options -----------------------------------------------------------------

MV_GUIDANCE = {
    "mission": "Why you exist: one sentence, no jargon.",
    "vision": "The world if you win.",
}


def options_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "string"},
            }
        },
        "required": ["options"],
        "additionalProperties": False,
    }


def build_options_messages(startup: Startup, field: str, current: str) -> list[LLMMessage]:
    system = (
        "You write startup mission and vision statements. Give exactly 3 distinct options, "
        "each one sentence, plain language, no jargon, no em dashes."
    )
    user = (
        f"{_context_line(startup)}\nWrite 3 {field} statement options. Guidance: "
        f"{MV_GUIDANCE[field]} Current draft: {current or 'none'}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def clean_options(result: dict) -> list[str]:
    out: list[str] = []
    for raw in result.get("options") or []:
        text = str(raw).strip()
        if text and text not in out:
            out.append(text)
    return out[:3]


# --- personas from validation -----------------------------------------------------------------


def validation_context(db: Session, startup: Startup) -> dict[str, Any]:
    interviews = (
        db.query(Interview)
        .filter_by(startup_id=startup.id)
        .order_by(Interview.held_on.desc())
        .limit(MAX_INTERVIEWS)
        .all()
    )
    assumptions = db.query(Assumption).filter_by(startup_id=startup.id).limit(20).all()
    return {
        # Interviewee names are deliberately left out (PII).
        "interviews": [
            {
                "segment": i.segment or "unspecified",
                "verdict": i.verdict.value,
                "notes": (i.notes or "")[:NOTE_CHARS],
                "quotes": [str(q)[:200] for q in (i.key_quotes or [])][:5],
            }
            for i in interviews
        ],
        "assumptions": [f"{a.statement} ({a.status.value})" for a in assumptions],
    }


def has_validation_data(ctx: dict[str, Any]) -> bool:
    return bool(ctx["interviews"] or ctx["assumptions"])


def build_persona_messages(startup: Startup, ctx: dict[str, Any]) -> list[LLMMessage]:
    lines = [
        f"- Segment {i['segment']} ({i['verdict']}): {i['notes']} Quotes: {'; '.join(i['quotes'])}"
        for i in ctx["interviews"]
    ]
    system = (
        "You are a customer researcher. From real interview notes, draft up to 3 distinct customer "
        "personas grounded ONLY in what the notes say. Use invented first names, never a real one."
    )
    user = (
        f"{_context_line(startup)}\nInterview notes:\n{chr(10).join(lines) or '(none)'}\n"
        f"Assumptions being tested: {'; '.join(ctx['assumptions']) or 'none'}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def existing_persona_names(db: Session, startup: Startup) -> set[str]:
    rows = db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=RecordKind.persona)
    return {str(r.data.get("name", "")).strip().casefold() for r in rows}


# --- pricing advisor ----------------------------------------------------------------------------

PRICING_MODELS = [m.value for m in PricingModelType]


def advisor_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "wtp_questions": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
            "recommended_model": {"type": "string", "enum": PRICING_MODELS},
            "tiers": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "price": {"type": "string"},
                        "features": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "price", "features"],
                    "additionalProperties": False,
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["wtp_questions", "recommended_model", "tiers", "rationale"],
        "additionalProperties": False,
    }


def competitor_prices(db: Session, startup: Startup) -> list[dict[str, str]]:
    rows = db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=RecordKind.competitor)
    return [
        {"name": str(r.data.get("name")), "price": str(r.data.get("price"))}
        for r in rows
        if r.data.get("price")
    ]


def build_advisor_messages(
    startup: Startup, prices: list[dict[str, str]], wtp: str | None
) -> list[LLMMessage]:
    price_lines = "\n".join(f"- {p['name']}: {p['price']}" for p in prices) or "(none given)"
    system = (
        "You are a pricing strategist for early-stage startups. Suggest willingness-to-pay "
        "interview questions, one pricing model, up to 3 tiers with prices in the startup's "
        "likely currency, and a short rationale. Be explicit that prices are starting points."
    )
    user = (
        f"{_context_line(startup)}\nCompetitor prices:\n{price_lines}\n"
        f"What customers said about willingness to pay: {wtp or 'nothing yet'}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
