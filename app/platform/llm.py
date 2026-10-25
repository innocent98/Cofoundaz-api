import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import settings
from app.core.logger import log

EMPTY_RETRY_FACTOR = 4
EMPTY_RETRY_CEILING = 16000

Role = Literal["system", "user", "assistant"]


@dataclass
class LLMMessage:
    role: Role
    content: str


# Product copy rule: no em/en dashes in anything we render. Models love them, so every
# real completion is cleaned here, at the one seam all LLM output passes through.
_DIGIT_RANGE = re.compile(r"(?<=\d)[ \t]*[\u2013\u2014][ \t]*(?=\d)")
_LINE_BULLET = re.compile(r"^([ \t]*)[\u2013\u2014][ \t]*", re.MULTILINE)
_INLINE_DASH = re.compile(r"[ \t]*[\u2013\u2014][ \t]*")


def strip_dashes(text: str) -> str:
    """Replace em/en dashes: digit ranges become '-', line bullets '- ', anything else ', '."""
    text = _DIGIT_RANGE.sub("-", text)
    text = _LINE_BULLET.sub(r"\1- ", text)
    return _INLINE_DASH.sub(", ", text)


def _strip_dashes_deep(value: object) -> object:
    if isinstance(value, str):
        return strip_dashes(value)
    if isinstance(value, list):
        return [_strip_dashes_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_dashes_deep(v) for k, v in value.items()}
    return value


class LLMClient(Protocol):
    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str: ...

    def complete_json(
        self, messages: list[LLMMessage], *, schema: dict, max_tokens: int
    ) -> dict: ...


class StubLLMClient:
    """Deterministic, offline client for tests and local dev (LLM_PROVIDER=stub).

    Returns a fixed, recognizable string so a test can assert the AI path ran without
    a network call or an API key.
    """

    last_usage_tokens: int = 0

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        return "[stub-llm] AI-generated assessment narrative."

    def complete_json(self, messages: list[LLMMessage], *, schema: dict, max_tokens: int) -> dict:
        result = self._stub_value(schema, "value")
        return result if isinstance(result, dict) else {"value": result}

    @staticmethod
    def _stub_value(node: dict, key: str) -> object:
        if "enum" in node and node["enum"]:
            return node["enum"][0]
        node_type = node.get("type")
        if node_type == "object":
            return {
                k: StubLLMClient._stub_value(v, k) for k, v in node.get("properties", {}).items()
            }
        if node_type == "array":
            return [StubLLMClient._stub_value(node.get("items", {"type": "string"}), key)]
        if node_type in ("number", "integer"):
            return 0
        if node_type == "boolean":
            return False
        return f"[stub-llm] {key}"


class EmptyCompletionError(RuntimeError):
    """The model answered with no text. Deterministic for a given prompt (a spent reasoning
    budget that a larger retry did not fix, a content filter), so retrying the job only burns
    tokens: callers end the turn instead."""


class OpenAILLMClient:
    """Calls the OpenAI (or OpenAI-compatible) Chat Completions API over httpx. Fail-loud.

    Never returns a silent empty/partial completion: a missing key, non-2xx, transport
    error, unparseable body, or empty text all raise RuntimeError so the worker job's
    retry/backoff can act on it.

    Sends `max_completion_tokens` rather than `max_tokens`: OpenAI deprecated `max_tokens`
    in favor of `max_completion_tokens` for newer models (and it's outright incompatible
    with reasoning models), confirmed live against the Chat Completions API reference
    while building this client for LLM_MODEL=gpt-5.6-luna. The `complete()` keyword stays
    `max_tokens` -- that's this seam's public interface, independent of the wire param name.

    Does NOT forward `temperature` to the API. Confirmed live: gpt-5.6-luna 400s on any
    explicit `temperature` (including this seam's own default of 0.7) with "Only the
    default (1) value is supported" -- the same reasoning-model restriction OpenAI applies
    to o-series models for `max_tokens`. `temperature` stays in `complete()`'s signature for
    Protocol conformance and for any future OpenAI-compatible provider/model that does honor
    it; this client just doesn't have anywhere live to put it for the configured model today.
    """

    def __init__(self) -> None:
        self.last_usage_tokens: int = 0
        # Tokens the caller can still spend today (None = unlimited); caps the larger retry.
        self.headroom: int | None = None

    def _post_chat(self, payload: dict) -> dict:
        if not settings.LLM_API_KEY:
            raise RuntimeError(
                "LLM_API_KEY is empty but LLM_PROVIDER='openai'. Set LLM_API_KEY, or use "
                "LLM_PROVIDER='stub' for local/dev (deterministic, no network)."
            )
        base = settings.LLM_BASE_URL or "https://api.openai.com/v1"
        url = f"{base.rstrip('/')}/chat/completions"
        try:
            resp = httpx.post(
                url,
                json={"model": settings.LLM_MODEL, **payload},
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                timeout=settings.LLM_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM request to {url} failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise RuntimeError(f"LLM API returned {resp.status_code}: {resp.text[:500]}")
        try:
            body: dict = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"LLM API returned an unparseable body: {exc}") from exc
        return body

    def _content(self, body: dict) -> str:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM API returned an unexpected body shape: {exc}") from exc
        # A refusal / content-filter / exhausted-reasoning reply carries `content: null`; that
        # must read as empty, not as the string "None".
        return "" if content is None else str(content)

    def _finish(self, body: dict) -> tuple[str, int]:
        """(finish_reason, reasoning_tokens) of the first choice, for diagnostics."""
        try:
            reason = str(body["choices"][0].get("finish_reason"))
        except (KeyError, IndexError, TypeError, AttributeError):
            reason = "unknown"
        details = (body.get("usage") or {}).get("completion_tokens_details") or {}
        return reason, int(details.get("reasoning_tokens", 0) or 0)

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        wire = [{"role": m.role, "content": m.content} for m in messages]
        budget = max_tokens
        spent = 0
        for attempt in (1, 2):
            body = self._post_chat({"messages": wire, "max_completion_tokens": budget})
            spent += int((body.get("usage") or {}).get("total_tokens", 0) or 0)
            self.last_usage_tokens = spent
            text = self._content(body)
            if text and text.strip():
                return strip_dashes(text)
            reason, reasoning = self._finish(body)
            log.warning(
                f"[llm] empty completion model={settings.LLM_MODEL} finish_reason={reason} "
                f"reasoning_tokens={reasoning} max_completion_tokens={budget} attempt={attempt}"
            )
            # Reasoning models spend max_completion_tokens on hidden reasoning first; when the
            # whole budget went there the visible answer is empty and finish_reason is "length".
            # Retry once with a larger budget instead of failing the turn.
            larger = min(max_tokens * EMPTY_RETRY_FACTOR, EMPTY_RETRY_CEILING)
            affordable = self.headroom is None or self.headroom - spent >= larger
            if reason != "length" or attempt == 2 or not affordable:
                raise EmptyCompletionError(
                    f"LLM API returned an empty completion (finish_reason={reason}, "
                    f"reasoning_tokens={reasoning})."
                )
            budget = larger
        raise RuntimeError("LLM API returned an empty completion.")  # pragma: no cover

    def complete_json(self, messages: list[LLMMessage], *, schema: dict, max_tokens: int) -> dict:
        body = self._post_chat(
            {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_completion_tokens": max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": schema, "strict": True},
                },
            }
        )
        self.last_usage_tokens = int((body.get("usage") or {}).get("total_tokens", 0) or 0)
        content = self._content(body)
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise RuntimeError(f"LLM API returned an unparseable JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("LLM API returned a non-object JSON result.")
        cleaned: dict = _strip_dashes_deep(data)  # type: ignore[assignment]
        return cleaned


def get_llm_client() -> LLMClient:
    provider = settings.LLM_PROVIDER
    if provider == "stub":
        return StubLLMClient()
    if provider == "openai":
        return OpenAILLMClient()
    raise RuntimeError(f"Unknown LLM_PROVIDER={provider!r}. Supported: 'openai', 'stub'.")
