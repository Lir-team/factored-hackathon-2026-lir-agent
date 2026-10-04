"""Messenger over the Telegram Bot API (`sendMessage`, plain text)."""

import logging

import httpx

from lir_agent.application.ports import MessageNotSentError
from lir_agent.domain.telegram import split_message

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"


class TelegramBotMessenger:
    """Sends plain-text messages as the bot. The URL holds the bot token: never log it."""

    def __init__(self, bot_token: str, client: httpx.AsyncClient | None = None) -> None:
        """Keep the token and an HTTP client (tests pass one with a mock transport)."""
        self._url = f"{API_URL}/bot{bot_token}/sendMessage"
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
