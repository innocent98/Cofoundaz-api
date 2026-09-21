import json
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
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM API returned an unexpected body shape: {exc}") from exc

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        body = self._post_chat(
            {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_completion_tokens": max_tokens,
            }
        )
        self.last_usage_tokens = int((body.get("usage") or {}).get("total_tokens", 0) or 0)
        text = self._content(body)
        if not text or not text.strip():
            raise RuntimeError("LLM API returned an empty completion.")
        return text

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
        return data


def get_llm_client() -> LLMClient:
    provider = settings.LLM_PROVIDER
    if provider == "stub":
        return StubLLMClient()
    if provider == "openai":
        return OpenAILLMClient()
    raise RuntimeError(f"Unknown LLM_PROVIDER={provider!r}. Supported: 'openai', 'stub'.")
