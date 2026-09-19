from app.core.config import Settings


def test_llm_settings_have_expected_defaults():
    fields = Settings.model_fields
    assert fields["LLM_PROVIDER"].default == "openai"
    assert fields["LLM_MODEL"].default == "gpt-5.6-luna"
    assert fields["LLM_TIMEOUT"].default == 60
    assert fields["LLM_MAX_TOKENS"].default == 800
    assert fields["LLM_API_KEY"].default == ""
    assert fields["LLM_BASE_URL"].default == ""
