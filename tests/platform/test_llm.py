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


def test_stub_complete_json_matches_schema_shape():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {"type": "string"}}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    out = StubLLMClient().complete_json([LLMMessage(role="user", content="x")], schema=schema, max_tokens=100)
    assert set(out) == {"a", "b"}
    assert isinstance(out["a"], str) and out["a"].startswith("[stub-llm]")
    assert isinstance(out["b"], list) and out["b"] and out["b"][0].startswith("[stub-llm]")


def test_openai_complete_json_sends_json_schema_and_parses(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": "hi", "b": ["x"]}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    out = OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema=schema, max_tokens=50)
    assert out == {"a": "hi", "b": ["x"]}
    rf = captured["json"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == schema
    assert captured["json"]["max_completion_tokens"] == 50


def test_openai_complete_json_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)


def test_openai_complete_json_fails_loud_on_non_2xx(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)


def test_openai_complete_json_fails_loud_on_bad_json(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}))
    with pytest.raises(RuntimeError, match="unparseable"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)


def test_openai_complete_json_fails_loud_on_non_object(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"choices": [{"message": {"content": "[1,2,3]"}}]}))
    with pytest.raises(RuntimeError, match="non-object"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)
