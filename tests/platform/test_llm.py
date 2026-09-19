import httpx
import pytest

from app.core.config import settings
from app.platform.llm import (
    LLMMessage,
    OpenAILLMClient,
    StubLLMClient,
    get_llm_client,
)


def test_stub_client_is_deterministic_and_offline():
    out = StubLLMClient().complete([LLMMessage(role="user", content="hi")], max_tokens=50)
    assert out.startswith("[stub-llm]")


def test_get_llm_client_switches_on_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    assert isinstance(get_llm_client(), StubLLMClient)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    assert isinstance(get_llm_client(), OpenAILLMClient)


def test_get_llm_client_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        get_llm_client()


def test_openai_client_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="hi")], max_tokens=50)


def test_openai_client_happy_path(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["auth"] = headers.get("Authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello there"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = OpenAILLMClient().complete(
        [LLMMessage(role="system", content="s"), LLMMessage(role="user", content="u")],
        max_tokens=42,
    )
    assert out == "hello there"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["json"]["model"] == "gpt-5.6-luna"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["json"]["messages"][0] == {"role": "system", "content": "s"}


def test_openai_client_base_url_already_includes_v1(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.openai.com/v1")

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello there"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=10)
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"


def test_openai_client_fails_loud_on_non_2xx(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=10)


def test_openai_client_fails_loud_on_empty_completion(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}),
    )
    with pytest.raises(RuntimeError, match="empty"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=10)
