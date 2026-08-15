from app.platform.ai import StubAIPanel


def test_stub_ai_returns_canned_reply():
    out = StubAIPanel().reply({"industry": "fintech", "stage": "idea"})
    assert isinstance(out, str) and len(out) > 0
