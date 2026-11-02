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


def _length_empty(reasoning=800):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {
                "total_tokens": 900,
                "completion_tokens_details": {"reasoning_tokens": reasoning},
            },
        },
    )


def test_empty_completion_from_exhausted_reasoning_budget_retries_larger(monkeypatch):
    """Finance D9: a reasoning model burned the whole cap thinking, so content was empty."""
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    sent = []

    def fake_post(url, json, headers, timeout):
        sent.append(json["max_completion_tokens"])
        if len(sent) == 1:
            return _length_empty()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Start with payroll."}}],
                "usage": {"total_tokens": 1500},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OpenAILLMClient()
    out = client.complete([LLMMessage(role="user", content="u")], max_tokens=800)
    assert out == "Start with payroll."
    assert sent == [800, 3200]
    assert client.last_usage_tokens == 2400  # both calls are metered


def test_empty_completion_after_retry_raises_with_finish_reason(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(1)
        return _length_empty()

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match=r"finish_reason=length, reasoning_tokens=800"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=800)
    assert len(calls) == 2


def test_empty_completion_other_finish_reason_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(1)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": None}, "finish_reason": "content_filter"}]},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="finish_reason=content_filter"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=800)
    assert len(calls) == 1


def test_stub_complete_json_matches_schema_shape():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    out = StubLLMClient().complete_json(
        [LLMMessage(role="user", content="x")], schema=schema, max_tokens=100
    )
    assert set(out) == {"a", "b"}
    assert isinstance(out["a"], str) and out["a"].startswith("[stub-llm]")
    assert isinstance(out["b"], list) and out["b"] and out["b"][0].startswith("[stub-llm]")


def test_stub_complete_json_recurses_nested_objects_and_enums():
    schema = {
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "score": {"type": "number"},
                        "level": {"type": "string", "enum": ["low", "high"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "score", "level", "tags"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["records"],
        "additionalProperties": False,
    }
    out = StubLLMClient().complete_json(
        [LLMMessage(role="user", content="x")], schema=schema, max_tokens=100
    )
    assert isinstance(out["records"], list) and len(out["records"]) == 1
    rec = out["records"][0]
    assert isinstance(rec["name"], str) and rec["name"].startswith("[stub-llm]")
    assert rec["score"] == 0
    assert rec["level"] == "low"  # first enum value
    assert rec["tags"] == ["[stub-llm] tags"]


def test_stub_complete_json_still_flat_for_canvas_shape():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    out = StubLLMClient().complete_json(
        [LLMMessage(role="user", content="x")], schema=schema, max_tokens=50
    )
    assert out["a"].startswith("[stub-llm]") and out["b"] == ["[stub-llm] b"]


def test_openai_complete_json_sends_json_schema_and_parses(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"a": "hi", "b": ["x"]}'}}]}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    out = OpenAILLMClient().complete_json(
        [LLMMessage(role="user", content="u")], schema=schema, max_tokens=50
    )
    assert out == {"a": "hi", "b": ["x"]}
    rf = captured["json"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == schema
    assert captured["json"]["max_completion_tokens"] == 50


def test_openai_complete_json_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        OpenAILLMClient().complete_json(
            [LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10
        )


def test_openai_complete_json_fails_loud_on_non_2xx(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        OpenAILLMClient().complete_json(
            [LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10
        )


def test_openai_complete_json_fails_loud_on_bad_json(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"choices": [{"message": {"content": "not json"}}]}
        ),
    )
    with pytest.raises(RuntimeError, match="unparseable"):
        OpenAILLMClient().complete_json(
            [LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10
        )


def test_openai_complete_json_fails_loud_on_non_object(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"choices": [{"message": {"content": "[1,2,3]"}}]}
        ),
    )
    with pytest.raises(RuntimeError, match="non-object"):
        OpenAILLMClient().complete_json(
            [LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10
        )


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("value—giving founders", "value, giving founders"),
        ("ship it — then measure", "ship it, then measure"),
        ("talk to 3–5 users", "talk to 3-5 users"),
        ("pages 10 – 12", "pages 10-12"),
        ("— first step\n– second step", "- first step\n- second step"),
        ("no dashes here - plain hyphen", "no dashes here - plain hyphen"),
    ],
)
def test_strip_dashes(raw, clean):
    from app.platform.llm import strip_dashes

    assert strip_dashes(raw) == clean


def test_openai_complete_strips_em_and_en_dashes(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, headers, timeout: httpx.Response(
            200, json={"choices": [{"message": {"content": "Why: value—giving 2–3 wins"}}]}
        ),
    )
    out = OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=10)
    assert out == "Why: value, giving 2-3 wins"


def test_openai_complete_json_strips_dashes_in_nested_strings(monkeypatch):
    import json as _json

    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    payload = {"title": "Plan — v1", "items": [{"why": "fast—cheap"}, 3], "n": 2}
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, headers, timeout: httpx.Response(
            200, json={"choices": [{"message": {"content": _json.dumps(payload)}}]}
        ),
    )
    out = OpenAILLMClient().complete_json(
        [LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10
    )
    assert out == {"title": "Plan, v1", "items": [{"why": "fast, cheap"}, 3], "n": 2}


def test_no_em_or_en_dashes_in_api_copy():
    """Rendered copy has no em/en dashes (product rule). Every string literal in `app/` that
    isn't a docstring is copy, an error message, a prompt or a title: keep them dash-free so a
    new one can't slip into the UI (the 2026-10-19 re-walk found eight)."""
    import ast
    import pathlib

    def docstrings(tree: ast.AST) -> set[int]:
        found = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (
                isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                found.add(id(body[0].value))
        return found

    offenders = []
    for path in sorted(pathlib.Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text())
        skip = docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and ("—" in node.value or "–" in node.value)
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_empty_completion_is_a_terminal_error_type(monkeypatch):
    from app.platform.llm import EmptyCompletionError

    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _length_empty())
    with pytest.raises(EmptyCompletionError):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=800)


def test_larger_retry_is_skipped_when_headroom_is_too_small(monkeypatch):
    from app.platform.llm import EmptyCompletionError

    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json["max_completion_tokens"])
        return _length_empty()

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OpenAILLMClient()
    client.headroom = 2000  # first call spends 900; the 3200-token retry no longer fits
    with pytest.raises(EmptyCompletionError):
        client.complete([LLMMessage(role="user", content="u")], max_tokens=800)
    assert calls == [800]

    calls.clear()
    client.headroom = 10_000  # fits
    with pytest.raises(EmptyCompletionError):
        client.complete([LLMMessage(role="user", content="u")], max_tokens=800)
    assert calls == [800, 3200]
