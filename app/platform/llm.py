from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import settings

Role = Literal["system", "user", "assistant"]


@dataclass
class LLMMessage:
    role: Role
    content: str


class LLMClient(Protocol):
    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str: ...


class StubLLMClient:
    """Deterministic, offline client for tests and local dev (LLM_PROVIDER=stub).

    Returns a fixed, recognizable string so a test can assert the AI path ran without
    a network call or an API key.
    """

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        return "[stub-llm] AI-generated assessment narrative."


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

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        if not settings.LLM_API_KEY:
            raise RuntimeError(
                "LLM_API_KEY is empty but LLM_PROVIDER='openai'. Set LLM_API_KEY, or use "
                "LLM_PROVIDER='stub' for local/dev (deterministic, no network)."
            )
        base = settings.LLM_BASE_URL or "https://api.openai.com"
        url = f"{base.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": settings.LLM_MODEL,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_completion_tokens": max_tokens,
        }
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                timeout=settings.LLM_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM request to {url} failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise RuntimeError(f"LLM API returned {resp.status_code}: {resp.text[:500]}")
        try:
            text: str = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM API returned an unparseable body: {exc}") from exc
        if not text or not text.strip():
            raise RuntimeError("LLM API returned an empty completion.")
        return text


def get_llm_client() -> LLMClient:
    provider = settings.LLM_PROVIDER
    if provider == "stub":
        return StubLLMClient()
    if provider == "openai":
        return OpenAILLMClient()
    raise RuntimeError(f"Unknown LLM_PROVIDER={provider!r}. Supported: 'openai', 'stub'.")
