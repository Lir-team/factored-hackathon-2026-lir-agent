"""Telegram webhook: `POST /channels/telegram`, called by Telegram for each bot update.

Telegram proves the call is its own with the `secret_token` registered through `setWebhook`,
sent back in a header. Every authenticated update is answered `200`, even when ignored:
any other status makes Telegram retry it. Retries of an update already handled are dropped;
an update whose handling failed is not remembered, so Telegram's retry is handled again.
"""

import hmac
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from lir_agent.application.use_cases import AnswerApprovalButton, AnswerTelegramMessage

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# Recent update ids remembered to drop Telegram's retries.
_RECENT_UPDATES = 1000


class _Chat(BaseModel):
    id: int
    type: str


class _Voice(BaseModel):
    """A voice note (OGG/Opus); its bytes are fetched by `file_id`."""

    file_id: str
    duration: int


class _Message(BaseModel):
    chat: _Chat
    text: str | None = None
    voice: _Voice | None = None


class _CallbackQuery(BaseModel):
    """A press on a button under a bot message."""

    id: str
    message: _Message | None = None
    data: str | None = None


class _Update(BaseModel):
    """The only fields read from an update; edits and the rest are ignored."""

    update_id: int
    message: _Message | None = None
    callback_query: _CallbackQuery | None = None


class _RecentUpdates:
    """The last `size` update ids, oldest dropped first."""

    def __init__(self, size: int = _RECENT_UPDATES) -> None:
        self._ids: OrderedDict[int, None] = OrderedDict()
        self._size = size

    def seen(self, update_id: int) -> bool:
        """Whether the id was already handled."""
        return update_id in self._ids

    def remember(self, update_id: int) -> None:
        """Remember a handled id."""
        self._ids[update_id] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)


def telegram_router(
    secret: str,
    answer: AnswerTelegramMessage,
    answer_button: AnswerApprovalButton | None = None,
) -> APIRouter:
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
        if recent.seen(update.update_id):
            return Response()
        press = update.callback_query
        if press is not None:
            chat = press.message.chat if press.message else None
            if answer_button and chat is not None and chat.type == "private":
                await answer_button.execute(chat.id, press.id, press.data)
            recent.remember(update.update_id)
            return Response()
        message = update.message
        if message is None or message.chat.type != "private":
            return Response()
        if message.text:
            await answer.execute(message.chat.id, message.text)
        elif message.voice:
            voice = message.voice
            await answer.execute_voice(message.chat.id, voice.file_id, voice.duration)
        else:
            return Response()
        recent.remember(update.update_id)  # only once handled: a failure is retried
        return Response()

    return router
