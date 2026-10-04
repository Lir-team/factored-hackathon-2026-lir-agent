"""Use case: answer one customer message received by the Lir Telegram bot."""

from collections.abc import Callable
from datetime import UTC, datetime

from lir_agent.application.ports import (
    AuditSink,
    CaseStore,
    ConversationNotFoundError,
    Conversations,
    Messenger,
)
from lir_agent.application.use_cases.process_case import deliver_replies
from lir_agent.domain.language import DEFAULT_LANGUAGE, Language
from lir_agent.domain.telegram import (
    ChatLink,
    bot_language,
    bot_message,
    start_payload,
)


class AnswerTelegramMessage:
    """Use case: link a chat with `/start <token>`, then relay its messages to the agent.

    The case's conversation is started when the case is delivered to the agent
    (`ProcessCase`), never here: linking only confirms and sends the replies waiting.
    """

    def __init__(
        self,
        conversations: Conversations,
        store: CaseStore,
        messenger: Messenger,
        audit: AuditSink,
        *,
        max_message_chars: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Keep the adapters and the message size cap."""
        self._conversations = conversations
        self._store = store
        self._messenger = messenger
        self._audit = audit
        self._max_chars = max_message_chars
        self._now = now or (lambda: datetime.now(UTC))

    async def execute(self, chat_id: int, text: str) -> None:
        """Answer `text` from a private chat. Never logs or audits tokens or chat text."""
        token = start_payload(text)
        link = self._store.get_chat_link(chat_id)
        if token:
            await self._link(chat_id, token, link)
        elif link is None:
            await self._say(chat_id, DEFAULT_LANGUAGE, "use_form")
        elif token == "":  # a bare /start on a linked chat
            await self._say(chat_id, link.language, "linked", folio=link.folio)
        elif len(text.strip()) > self._max_chars:
            await self._say(chat_id, link.language, "too_long", limit=self._max_chars)
        else:
            await self._ask(chat_id, link, text.strip())

    async def _link(self, chat_id: int, token: str, current: ChatLink | None) -> None:
        start = self._store.consume_start_token(token, self._now())
        if start is None:
            language = current.language if current else DEFAULT_LANGUAGE
            await self._say(chat_id, language, "link_invalid")
            return
        link = ChatLink(
            case_id=start.case_id,
            folio=start.folio,
            language=bot_language(start.language),
        )
        self._store.link_chat(chat_id, link)
        self._audit.record("telegram_linked", None, case_id=start.case_id)
        await self._say(chat_id, link.language, "linked", folio=link.folio)
        await deliver_replies(self._store, self._messenger, self._audit, start.case_id)

    async def _ask(self, chat_id: int, link: ChatLink, text: str) -> None:
        conversation = self._store.get_conversation(link.case_id)
        if conversation is None:  # the case is still on its way to the agent
            await self._say(chat_id, link.language, "processing", folio=link.folio)
            return
        try:
            reply = await self._conversations.send(
                conversation.owner, conversation.session_id, text
            )
        except ConversationNotFoundError:
            await self._say(chat_id, link.language, "conversation_expired")
            return
        if reply.strip():
            await self._messenger.send(chat_id, reply)

    async def _say(
        self, chat_id: int, language: Language, key: str, **values: object
    ) -> None:
        await self._messenger.send(chat_id, bot_message(key, language, **values))
