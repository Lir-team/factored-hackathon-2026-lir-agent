"""Tests for the in-memory terminal chat loop."""

import logging

import pytest
from google.adk.events import Event
from google.genai import types

from Agent.chat import EXIT_PHRASE, build_ask, chat_loop


def make_reader(lines: list[str]):
    queue = iter(lines)
    return lambda: next(queue)


def test_exit_phrase_ends_the_loop_without_calling_the_agent() -> None:
    calls: list[str] = []
    out: list[str] = []

    chat_loop(
        ask=lambda m: calls.append(m) or "x",
        read=make_reader(["chao pescao"]),
        write=out.append,
    )

    assert calls == []


def test_exit_phrase_is_case_and_whitespace_insensitive() -> None:
    calls: list[str] = []

    chat_loop(
        ask=lambda m: calls.append(m) or "x",
        read=make_reader(["  Chao Pescao  "]),
        write=lambda _: None,
    )

    assert calls == []
    assert EXIT_PHRASE == "chao pescao"


def test_messages_are_forwarded_in_order_until_exit() -> None:
    calls: list[str] = []
    out: list[str] = []

    def ask(message: str) -> str:
        calls.append(message)
        return f"echo: {message}"

    chat_loop(
        ask=ask, read=make_reader(["hola", "que tal", "chao pescao"]), write=out.append
    )

    assert calls == ["hola", "que tal"]
    assert out == ["echo: hola", "echo: que tal"]


def test_blank_input_is_skipped() -> None:
    calls: list[str] = []

    chat_loop(
        ask=lambda m: calls.append(m) or "x",
        read=make_reader(["", "   ", "hola", "chao pescao"]),
        write=lambda _: None,
    )

    assert calls == ["hola"]


def test_end_of_input_ends_the_loop() -> None:
    def read() -> str:
        raise EOFError

    chat_loop(ask=lambda m: "x", read=read, write=lambda _: None)


class FakeRunner:
    """Stands in for InMemoryRunner, replaying a fixed list of ADK events."""

    def __init__(self, events: list[Event]) -> None:
        self.events = events

    def run(self, **_: object) -> list[Event]:
        return self.events


def text_event(text: str) -> Event:
    return Event(author="agent", content=types.Content(parts=[types.Part(text=text)]))


def tool_call_event(name: str) -> Event:
    call = types.FunctionCall(name=name, args={"customer_id": "42"})
    return Event(
        author="agent", content=types.Content(parts=[types.Part(function_call=call)])
    )


def test_ask_returns_final_reply_text() -> None:
    ask = build_ask(FakeRunner([text_event("hi there")]), "s1")  # type: ignore[arg-type]

    assert ask("hello") == "hi there"


def test_ask_logs_tool_calls(caplog: pytest.LogCaptureFixture) -> None:
    runner = FakeRunner([tool_call_event("get_customer"), text_event("done")])

    with caplog.at_level(logging.DEBUG, logger="Agent.chat"):
        build_ask(runner, "s1")("who is 42?")  # type: ignore[arg-type]

    assert "get_customer" in caplog.text


def test_ask_warns_when_agent_returns_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = FakeRunner([tool_call_event("get_customer")])

    with caplog.at_level(logging.WARNING, logger="Agent.chat"):
        reply = build_ask(runner, "s1")("who is 42?")  # type: ignore[arg-type]

    assert reply == ""
    assert any(r.levelno == logging.WARNING for r in caplog.records)
