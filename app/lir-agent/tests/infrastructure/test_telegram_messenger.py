import asyncio
import json
import logging

import httpx
import pytest

from lir_agent.application.ports import FileNotDownloadedError, MessageNotSentError
from lir_agent.infrastructure.messaging import TelegramBotMessenger

TOKEN = "123456:SECRET-bot-token"


def messenger(handler) -> tuple[TelegramBotMessenger, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return TelegramBotMessenger(TOKEN, client=client), requests


def test_sends_plain_text_with_the_bot_api():
    bot, requests = messenger(lambda _: httpx.Response(200, json={"ok": True}))

    asyncio.run(bot.send(42, "hola"))

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {"chat_id": 42, "text": "hola"}


def test_long_text_is_split_at_telegrams_limit():
    bot, requests = messenger(lambda _: httpx.Response(200, json={"ok": True}))

    asyncio.run(bot.send(42, "a" * 4096 + "b"))

    assert [json.loads(r.content)["text"] for r in requests] == ["a" * 4096, "b"]


def test_a_rejected_send_raises_and_is_logged_without_the_token(caplog):
    bot, _ = messenger(lambda _: httpx.Response(403, json={"ok": False}))

    with (
        caplog.at_level(logging.DEBUG, logger="lir_agent"),
        pytest.raises(MessageNotSentError) as raised,
    ):
        asyncio.run(bot.send(42, "hola"))

    assert "403" in caplog.text
    assert TOKEN not in caplog.text
    assert TOKEN not in str(raised.value)


def test_a_network_failure_raises_without_the_token(caplog):
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    bot, _ = messenger(fail)

    with (
        caplog.at_level(logging.DEBUG, logger="lir_agent"),
        pytest.raises(MessageNotSentError) as raised,
    ):
        asyncio.run(bot.send(42, "hola"))

    assert "ConnectError" in caplog.text
    assert TOKEN not in caplog.text
    assert TOKEN not in str(raised.value)
    assert raised.value.__cause__ is None  # the httpx error carries the URL


def test_downloads_a_file_by_its_id():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            assert json.loads(request.content) == {"file_id": "voice-1"}
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "voice/file_9.oga"}}
            )
        return httpx.Response(200, content=b"OggS-audio")

    bot, requests = messenger(answer)

    assert asyncio.run(bot.download_file("voice-1")) == b"OggS-audio"
    assert str(requests[0].url) == f"https://api.telegram.org/bot{TOKEN}/getFile"
    assert requests[1].method == "GET"
    assert (
        str(requests[1].url)
        == f"https://api.telegram.org/file/bot{TOKEN}/voice/file_9.oga"
    )


@pytest.mark.parametrize(
    "answer",
    [
        lambda _: httpx.Response(400, json={"ok": False}),
        lambda _: httpx.Response(200, json={"ok": True, "result": {}}),
        lambda r: httpx.Response(
            404 if "/file/" in r.url.path else 200,
            json={"ok": True, "result": {"file_path": "voice/x.oga"}},
        ),
    ],
    ids=["get-file-rejected", "no-path", "download-rejected"],
)
def test_a_failed_download_raises_without_the_token(caplog, answer):
    bot, _ = messenger(answer)

    with (
        caplog.at_level(logging.DEBUG, logger="lir_agent"),
        pytest.raises(FileNotDownloadedError) as raised,
    ):
        asyncio.run(bot.download_file("voice-1"))

    assert TOKEN not in caplog.text
    assert TOKEN not in str(raised.value)


def test_a_network_failure_on_download_raises_without_the_token(caplog):
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    bot, _ = messenger(fail)

    with (
        caplog.at_level(logging.DEBUG, logger="lir_agent"),
        pytest.raises(FileNotDownloadedError) as raised,
    ):
        asyncio.run(bot.download_file("voice-1"))

    assert TOKEN not in caplog.text
    assert raised.value.__cause__ is None
