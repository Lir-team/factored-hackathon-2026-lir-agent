"""Tests for the agent guardrails."""

import asyncio
import logging
import pathlib
import types
from typing import Any

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from Agent.agent.agent import build_root_agent
from Agent.agent.hardening.guardrails.authentication import (
    CUSTOMER_ID_STATE_KEY,
    UNAUTHENTICATED_REPLY,
    UnauthenticatedSessionError,
    require_authenticated_customer,
    require_customer_id,
)
from Agent.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("ENVIRONMENT", "LOG_LEVEL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    # Unroutable on purpose: a regression that reaches the model fails fast.
    monkeypatch.setenv("LLM_API_BASE", "http://127.0.0.1:9")
    get_settings.cache_clear()


def context(state: dict[str, Any]) -> Any:
    """Fake callback context exposing only the session state."""
    return types.SimpleNamespace(state=state)


def test_require_customer_id_returns_the_id() -> None:
    assert require_customer_id({CUSTOMER_ID_STATE_KEY: "CLI-1"}) == "CLI-1"


@pytest.mark.parametrize(
    "state",
    [
        {},
        {CUSTOMER_ID_STATE_KEY: ""},
        {CUSTOMER_ID_STATE_KEY: "   "},
        {CUSTOMER_ID_STATE_KEY: 123},
        {CUSTOMER_ID_STATE_KEY: None},
    ],
)
def test_require_customer_id_raises_without_a_valid_id(state: dict[str, Any]) -> None:
    with pytest.raises(UnauthenticatedSessionError):
        require_customer_id(state)


def test_guardrail_lets_an_authenticated_session_through() -> None:
    ctx = context({CUSTOMER_ID_STATE_KEY: "CLI-1"})
    assert require_authenticated_customer(ctx) is None


def test_guardrail_refuses_an_unauthenticated_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="Agent"):
        reply = require_authenticated_customer(context({}))

    assert reply is not None
    assert reply.parts is not None
    assert reply.parts[0].text == UNAUTHENTICATED_REPLY
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_unauthenticated_session_is_refused_before_the_model_runs() -> None:
    app_name = "guardrail-test"
    runner = InMemoryRunner(agent=build_root_agent(), app_name=app_name)
    asyncio.run(
        runner.session_service.create_session(
            app_name=app_name, user_id="u", session_id="s"
        )
    )
    message = genai_types.Content(role="user", parts=[genai_types.Part(text="hi")])

    replies = [
        part.text
        for event in runner.run(user_id="u", session_id="s", new_message=message)
        if event.is_final_response() and event.content and event.content.parts
        for part in event.content.parts
    ]

    assert replies == [UNAUTHENTICATED_REPLY]
