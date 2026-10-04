"""Messenger over the Telegram Bot API: plain text, messages with buttons, button answers."""

import contextlib
import logging

import httpx

from lir_agent.application.ports import MessageNotSentError
from lir_agent.domain.telegram import InlineButton, split_message

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"


class TelegramBotMessenger:
    """Sends plain-text messages as the bot. The URL holds the bot token: never log it."""

    def __init__(self, bot_token: str, client: httpx.AsyncClient | None = None) -> None:
        """Keep the token and an HTTP client (tests pass one with a mock transport)."""
        self._base = f"{API_URL}/bot{bot_token}"
        self._url = f"{self._base}/sendMessage"
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def send(self, chat_id: int, text: str) -> None:
        """Send `text`, split at Telegram's limit; a failure is logged and raised.

        Raises:
            MessageNotSentError: On a network error or an error status; the parts after
                the failed one are not sent. It never carries the URL (it holds the token).
        """
        for part in split_message(text):
            try:
                response = await self._client.post(
                    self._url, json={"chat_id": chat_id, "text": part}
                )
            except httpx.HTTPError as error:
                # The exception message may contain the URL: log its type only.
                reason = type(error).__name__
                logger.warning("Telegram sendMessage failed: %s", reason)
                raise MessageNotSentError(reason) from None
            if response.is_error:
                logger.warning(
                    "Telegram sendMessage failed with status %s", response.status_code
                )
                raise MessageNotSentError(f"status {response.status_code}")

    async def send_buttons(
        self, chat_id: int, text: str, rows: list[list[InlineButton]]
    ) -> None:
        """Send one message (at most 4096 characters) with inline buttons under it, if any.

        Raises:
            MessageNotSentError: On a network error or an error status.
        """
        keyboard = [
            [
                {"text": b.text, "url": b.url}
                if b.url
                else {"text": b.text, "callback_data": b.callback_data}
                for b in row
            ]
            for row in rows
        ]
        body: dict = {"chat_id": chat_id, "text": text}
        if keyboard:
            body["reply_markup"] = {"inline_keyboard": keyboard}
        await self._call("sendMessage", body)

    async def answer_callback(
        self, callback_id: str, text: str, alert: bool = False
    ) -> None:
        """Acknowledge a button press; a failure is logged, never raised (the press stands)."""
        with contextlib.suppress(MessageNotSentError):
            await self._call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": text, "show_alert": alert},
            )

    async def _call(self, method: str, body: dict) -> None:
        try:
            response = await self._client.post(f"{self._base}/{method}", json=body)
        except httpx.HTTPError as error:
            reason = type(error).__name__  # the message may hold the URL (the token)
            logger.warning("Telegram %s failed: %s", method, reason)
            raise MessageNotSentError(reason) from None
        if response.is_error:
            logger.warning("Telegram %s failed with status %s", method, response.status_code)
            raise MessageNotSentError(f"status {response.status_code}")
