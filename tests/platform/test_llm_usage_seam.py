from app.platform.llm import OpenAILLMClient, StubLLMClient


def test_stub_reports_zero_usage():
    c = StubLLMClient()
    c.complete([], max_tokens=10)
    assert c.last_usage_tokens == 0


def test_openai_parses_usage(monkeypatch):
    c = OpenAILLMClient()
    fake_body = {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 512}}
    monkeypatch.setattr(c, "_post_chat", lambda payload: fake_body)
    out = c.complete([], max_tokens=10)
    assert out == "hi"
    assert c.last_usage_tokens == 512


def test_openai_usage_defaults_zero_when_absent(monkeypatch):
    c = OpenAILLMClient()
    monkeypatch.setattr(
        c, "_post_chat", lambda payload: {"choices": [{"message": {"content": "x"}}]}
    )
    c.complete([], max_tokens=10)
    assert c.last_usage_tokens == 0
