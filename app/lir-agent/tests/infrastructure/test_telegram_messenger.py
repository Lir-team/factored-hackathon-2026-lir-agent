import asyncio
import json
import logging

import httpx

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


def test_a_rejected_send_is_logged_without_the_token(caplog):
    bot, _ = messenger(lambda _: httpx.Response(403, json={"ok": False}))

    with caplog.at_level(logging.DEBUG, logger="lir_agent"):
        asyncio.run(bot.send(42, "hola"))

    assert "403" in caplog.text
    assert TOKEN not in caplog.text


def test_a_network_failure_does_not_raise_nor_log_the_token(caplog):
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    bot, _ = messenger(fail)

    with caplog.at_level(logging.DEBUG, logger="lir_agent"):
        asyncio.run(bot.send(42, "hola"))

    assert "ConnectError" in caplog.text
    assert TOKEN not in caplog.text
