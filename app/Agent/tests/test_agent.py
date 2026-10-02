"""Tests for the ADK root agent wiring."""

import pathlib

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from Agent.agent.agent import build_root_agent
from Agent.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("ENVIRONMENT", "LOG_LEVEL", "LLM_MODEL", "LLM_API_BASE"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


def test_root_agent_uses_litellm_with_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o-mini")
    agent = build_root_agent()
    assert isinstance(agent, LlmAgent)
    assert isinstance(agent.model, LiteLlm)
    assert agent.model.model == "openai/gpt-4o-mini"


def test_root_agent_has_a_name_and_instruction() -> None:
    agent = build_root_agent()
    assert agent.name == "clir_agent"
    assert agent.instruction


def test_root_agent_defaults_to_local_ollama() -> None:
    agent = build_root_agent()
    assert isinstance(agent.model, LiteLlm)
    assert agent.model.model.startswith("ollama_chat/")


def test_root_agent_passes_api_base_to_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_BASE", "http://ollama.internal:11434")
    agent = build_root_agent()
    assert isinstance(agent.model, LiteLlm)
    assert agent.model._additional_args["api_base"] == "http://ollama.internal:11434"
