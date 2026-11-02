from app.core.config import Settings


def test_llm_settings_have_expected_defaults():
    fields = Settings.model_fields
    assert fields["LLM_PROVIDER"].default == "openai"
    assert fields["LLM_MODEL"].default == "gpt-5.6-luna"
    assert fields["LLM_TIMEOUT"].default == 60
    assert fields["LLM_MAX_TOKENS"].default == 800
    assert fields["LLM_API_KEY"].default == ""
    assert fields["LLM_BASE_URL"].default == ""


def test_default_daily_token_budget_fits_a_business_plan():
    """One business plan used ~24k tokens live (2026-10-19), above the old 15k cap, so a plan
    failed over_budget whenever AI had been used earlier that day. Adebayo raised it to 100k."""
    from app.core.config import Settings

    assert Settings.model_fields["LLM_DAILY_TOKEN_BUDGET"].default == 100_000
