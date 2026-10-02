"""Tests for the in-memory terminal chat loop."""

from Agent.chat import EXIT_PHRASE, chat_loop


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
