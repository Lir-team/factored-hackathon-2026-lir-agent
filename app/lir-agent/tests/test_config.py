"""Tests for settings loading."""

import pathlib

import pytest
from pydantic import ValidationError

from lir_agent.config.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Give each test an empty environment and a directory with no `.env`."""
    monkeypatch.chdir(tmp_path)
    for key in (
        "ENVIRONMENT",
        "LOG_LEVEL",
        "LLM_MODEL",
        "LLM_API_BASE",
        "JEV_ENABLED",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


def test_defaults_apply_when_nothing_is_set() -> None:
    assert Settings().environment == "local"


def test_process_environment_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().environment == "production"


def test_dotenv_file_is_read(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".env").write_text("ENVIRONMENT=staging\n")
    assert Settings().environment == "staging"


def test_process_environment_wins_over_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    (tmp_path / ".env").write_text("ENVIRONMENT=staging\n")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().environment == "production"


def test_invalid_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "not-a-real-environment")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_accessor_is_cached() -> None:
    assert get_settings() is get_settings()


def test_llm_defaults_point_at_local_ollama() -> None:
    settings = Settings()
    assert settings.llm_model == "ollama_chat/llama3.1"
    assert settings.llm_api_base == "http://localhost:11434"


def test_llm_settings_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "ollama_chat/qwen3:8b")
    monkeypatch.setenv("LLM_API_BASE", "http://10.0.0.5:11434")
    settings = Settings()
    assert settings.llm_model == "ollama_chat/qwen3:8b"
    assert settings.llm_api_base == "http://10.0.0.5:11434"


def test_staging_dir_derives_from_data_dir(tmp_path: pathlib.Path) -> None:
    assert Settings(data_dir=tmp_path).staging_dir == tmp_path / "staging"


def test_api_key_is_kept_out_of_reprs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    settings = Settings()
    assert "sk-test-secret" not in repr(settings)
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "sk-test-secret"


def test_empty_reference_date_means_today(tmp_path: pathlib.Path) -> None:
    # `.env.example` documents `REFERENCE_DATE=` (empty) as "use the current date".
    (tmp_path / ".env").write_text("REFERENCE_DATE=\n")
    assert Settings().reference_date is None


def test_llm_decisions_chain_falls_back_to_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    from lir_agent.container import build_decisions

    monkeypatch.setenv("DECISIONS", "llm")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-6-luna")
    chain = build_decisions(Settings())
    assert chain.name == "llm:openai/gpt-6-luna > keywords-v1"


def test_default_decisions_keep_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    from lir_agent.container import build_decisions

    assert build_decisions(Settings()).name == "keywords-v1"


def test_jev_settings_are_typed_and_the_token_is_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret")
    settings = Settings()
    assert settings.jev_enabled is True
    assert settings.cloudflare_account_id == "acct-1"
    assert settings.cloudflare_api_token is not None
    assert settings.cloudflare_api_token.get_secret_value() == "cf-secret"
    assert "cf-secret" not in repr(settings)


def test_jev_is_off_by_default() -> None:
    settings = Settings()
    assert settings.jev_enabled is False
    assert settings.cloudflare_api_token is None


def test_enabled_jev_with_credentials_leads_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lir_agent.container import build_decisions

    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret")
    assert build_decisions(Settings()).name == "jev > keywords-v1"


def test_enabled_jev_without_a_token_keeps_the_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lir_agent.container import build_decisions

    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
    assert build_decisions(Settings()).name == "keywords-v1"
