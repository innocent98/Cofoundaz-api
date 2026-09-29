from typing import Any

from app.db.models.enums import ChannelKey
from app.platform.llm import LLMMessage

CHANNEL_KEYS = [c.value for c in ChannelKey]


def copy_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "variants": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string"}}
        },
        "required": ["variants"],
        "additionalProperties": False,
    }


def build_copy_messages(
    *,
    asset_type: str,
    channel: str | None,
    tone: str,
    key_message: str,
    cta: str | None,
    segment_name: str | None,
) -> list[LLMMessage]:
    system = (
        "You are a senior marketing copywriter. Write exactly THREE distinct, ready-to-ship "
        "copy variants for the requested asset type, in the requested tone. Each variant is a "
        "single self-contained string. No numbering, no commentary."
    )
    user = (
        f"Asset type: {asset_type}. Channel: {channel or 'unspecified'}. Tone: {tone}.\n"
        f"Audience: {segment_name or 'general'}.\n"
        f"Key message: {key_message}\n"
        f"Call to action: {cta or 'none'}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def plan_week_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "channel": {"type": "string", "enum": CHANNEL_KEYS},
                        "body": {"type": "string"},
                        "day_offset": {"type": "integer"},
                    },
                    "required": ["title", "channel", "body", "day_offset"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entries"],
        "additionalProperties": False,
    }


def build_plan_week_messages(
    *, stage: str | None, industry: str | None, name: str | None
) -> list[LLMMessage]:
    system = (
        "You are a startup marketing coach. Propose a 7-day starter content calendar (at most 7 "
        "entries). Each entry has a short title, a channel key (one of: organic_social, paid_social, "
        "search, email, content_seo, partnerships, events, referral), a short copy body, and a "
        "day_offset 0-6 (0 = today). Spread channels sensibly for the founder's stage."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'early'}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def channel_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "channel_mix": {
                "type": "object",
                "properties": {k: {"type": "integer"} for k in CHANNEL_KEYS},
                "required": CHANNEL_KEYS,
                "additionalProperties": False,
            },
            "rationale": {"type": "string"},
        },
        "required": ["channel_mix", "rationale"],
        "additionalProperties": False,
    }


def build_channel_plan_messages(
    *, objective: str, stage: str | None, industry: str | None, budget: int | None
) -> list[LLMMessage]:
    system = (
        "You are a startup growth strategist. Recommend how to split marketing effort across "
        "these eight channels: " + ", ".join(CHANNEL_KEYS) + ". Return an integer percentage for "
        "every channel (0 allowed); the percentages should sum to about 100. Also give a short "
        "rationale a founder can act on. Weight the split for the campaign objective and stage."
    )
    budget_line = f" Monthly budget (cents): {budget}." if budget is not None else ""
    user = (
        f"Objective: {objective}. Stage: {stage or 'early'}. "
        f"Industry: {industry or 'unspecified'}.{budget_line}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def channel_fit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "notes": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in CHANNEL_KEYS},
                "required": CHANNEL_KEYS,
                "additionalProperties": False,
            }
        },
        "required": ["notes"],
        "additionalProperties": False,
    }


def build_channel_fit_messages(
    *, stage: str | None, industry: str | None, statuses: dict[str, str]
) -> list[LLMMessage]:
    system = (
        "You are a marketing channel advisor. For each of these eight channels, write ONE short "
        "fit note (one or two sentences) telling this founder how well the channel fits their "
        "business and why. Channels: " + ", ".join(CHANNEL_KEYS) + "."
    )
    status_line = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())) or "all not_started"
    user = (
        f"Stage: {stage or 'early'}. Industry: {industry or 'unspecified'}. "
        f"Current channel statuses: {status_line}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def content_gap_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "gaps": {
                "type": "array",
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "target_keyword": {"type": "string"},
                        "angle": {"type": "string"},
                    },
                    "required": ["title", "target_keyword", "angle"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["gaps"],
        "additionalProperties": False,
    }


def build_content_gap_messages(
    *, keywords: list[str], stage: str | None, industry: str | None
) -> list[LLMMessage]:
    system = (
        "You are an SEO content strategist. Propose up to 7 content ideas (articles/pages) this "
        "startup has NOT covered yet but should, to rank for its target keywords. Each idea has a "
        "short title, a target_keyword, and a one-line angle."
    )
    kw = ", ".join(keywords) if keywords else "none tracked yet"
    user = (
        f"Stage: {stage or 'early'}. Industry: {industry or 'unspecified'}. "
        f"Tracked keywords: {kw}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
