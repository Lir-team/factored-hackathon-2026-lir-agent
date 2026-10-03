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
    for key in ("ENVIRONMENT", "LOG_LEVEL", "LLM_MODEL", "LLM_API_BASE"):
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

    monkeypatch.delenv("JEV_ENABLED", raising=False)
    assert build_decisions(Settings()).name == "keywords-v1"


def _decision_call_kwargs(settings: Settings) -> dict:
    """Run one decision through build_decisions and return what reached the provider."""
    import json
    from types import SimpleNamespace

    from decision_layer.questions import TURN_QUESTIONS

    from lir_agent.container import build_decisions

    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps({}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    build_decisions(settings, completion=completion).decide("hola", TURN_QUESTIONS)
    return calls[0]


def test_decisions_on_the_agent_model_reuse_its_key_and_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISIONS", "llm")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("LLM_API_KEY", "sk-agent")
    monkeypatch.setenv("LLM_API_BASE", "")
    kwargs = _decision_call_kwargs(Settings())
    assert kwargs["model"] == "openai/gpt-4o"
    assert kwargs["api_key"] == "sk-agent"
    assert "api_base" not in kwargs


def test_decisions_on_another_provider_never_get_the_agent_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISIONS", "llm")
    monkeypatch.setenv("LLM_MODEL", "ollama_chat/llama3.1")
    monkeypatch.setenv("LLM_API_KEY", "sk-agent")
    monkeypatch.setenv("DECISION_LLM_MODEL", "openrouter/typesafe/jev-router")
    kwargs = _decision_call_kwargs(Settings())
    assert kwargs["model"] == "openrouter/typesafe/jev-router"
    assert "api_key" not in kwargs and "api_base" not in kwargs


def test_decision_key_is_used_for_another_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISIONS", "llm")
    monkeypatch.setenv("DECISION_LLM_MODEL", "openrouter/typesafe/jev-router")
    monkeypatch.setenv("DECISION_LLM_API_KEY", "sk-or-decisions")
    assert _decision_call_kwargs(Settings())["api_key"] == "sk-or-decisions"
