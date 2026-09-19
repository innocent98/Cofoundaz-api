from app.core.config import settings


def test_llm_settings_have_expected_defaults():
    assert settings.LLM_PROVIDER in {"openai", "stub"}
    assert settings.LLM_MODEL == "gpt-5.6-luna"
    assert settings.LLM_TIMEOUT == 60
    assert settings.LLM_MAX_TOKENS == 800
    # API key + base url exist as strings (may be empty in the test env)
    assert isinstance(settings.LLM_API_KEY, str)
    assert isinstance(settings.LLM_BASE_URL, str)
