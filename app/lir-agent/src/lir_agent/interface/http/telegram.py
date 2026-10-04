"""Telegram webhook: `POST /channels/telegram`, called by Telegram for each bot update.

Telegram proves the call is its own with the `secret_token` registered through `setWebhook`,
sent back in a header. Every authenticated update is answered `200`, even when ignored:
any other status makes Telegram retry it. Retries of an update already handled are dropped.
"""

import hmac
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from lir_agent.application.use_cases import AnswerTelegramMessage

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# Recent update ids remembered to drop Telegram's retries.
_RECENT_UPDATES = 1000


class _Chat(BaseModel):
    id: int
    type: str


class _Message(BaseModel):
    chat: _Chat
    text: str | None = None


class _Update(BaseModel):
    """The only fields read from an update; edits, callbacks and the rest are ignored."""

    update_id: int
    message: _Message | None = None


class _RecentUpdates:
    """The last `size` update ids, oldest dropped first."""

    def __init__(self, size: int = _RECENT_UPDATES) -> None:
        self._ids: OrderedDict[int, None] = OrderedDict()
        self._size = size

    def seen(self, update_id: int) -> bool:
        """Whether the id was already seen; remembers it otherwise."""
        if update_id in self._ids:
            return True
        self._ids[update_id] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return False


def telegram_router(secret: str, answer: AnswerTelegramMessage) -> APIRouter:
    """The webhook route, accepting only calls carrying `secret`."""
    router = APIRouter()
    recent = _RecentUpdates()

    @router.post("/channels/telegram")
    async def webhook(request: Request) -> Response:
        given = request.headers.get(SECRET_HEADER, "")
        if not hmac.compare_digest(given.encode(), secret.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid secret token")
        try:
            update = _Update.model_validate(await request.json())
        except ValueError:  # bad JSON, or not an update this bot understands
            return Response()
        message = update.message
        if (
            recent.seen(update.update_id)
            or message is None
            or message.chat.type != "private"
            or not message.text
        ):
            return Response()
        await answer.execute(message.chat.id, message.text)
        return Response()

    return router
