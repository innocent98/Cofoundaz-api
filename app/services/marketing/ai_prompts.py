from typing import Any

from app.platform.llm import LLMMessage


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
                        "channel": {"type": "string"},
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
