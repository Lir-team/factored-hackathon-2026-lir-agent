"""Use case: answer one customer message received by the Lir Telegram bot."""

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from lir_agent.application.ports import (
    AuditSink,
    CaseStore,
    ChatFiles,
    ConversationNotFoundError,
    Conversations,
    FileNotDownloadedError,
    MessageNotSentError,
    Messenger,
    SpeechToText,
    TranscriptionError,
)
from lir_agent.application.use_cases.approvals import PresentApprovals
from lir_agent.application.use_cases.process_case import deliver_replies
from lir_agent.application.use_cases.resolve_handoff import ResolveHandoff
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

    Sending never fails the update: Telegram would retry it, and a `/start` token is
    already burned. Notices are best effort; an agent reply that could not be sent is
    queued and goes out, with any other waiting reply, before the chat's next answer.

    Voice notes are transcribed and answered like typed text, never as a command. A voice
    note that cannot be fetched or understood is answered, not retried.
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
        present_approvals: PresentApprovals | None = None,
        resolutions: ResolveHandoff | None = None,
        files: ChatFiles | None = None,
        speech: SpeechToText | None = None,
        max_voice_seconds: int = 60,
    ) -> None:
        """Keep the adapters, the size caps, the approval presenter and the voice path.

        Voice notes are declined politely unless both `files` and `speech` are given.
        """
        self._conversations = conversations
        self._store = store
        self._messenger = messenger
        self._audit = audit
        self._max_chars = max_message_chars
        self._now = now or (lambda: datetime.now(UTC))
        self._present_approvals = present_approvals
        self._resolutions = resolutions
        self._files = files
        self._speech = speech
        self._max_voice_seconds = max_voice_seconds

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

    async def execute_voice(self, chat_id: int, file_id: str, duration: int) -> None:
        """Answer a voice note of `duration` seconds as the text it says.

        Never logs or audits the audio or its transcript.
        """
        link = self._store.get_chat_link(chat_id)
        if link is None:
            await self._say(chat_id, DEFAULT_LANGUAGE, "use_form")
        elif self._files is None or self._speech is None:
            await self._say(chat_id, link.language, "voice_off")
        elif duration > self._max_voice_seconds:
            await self._say(
                chat_id, link.language, "voice_too_long", limit=self._max_voice_seconds
            )
        else:
            text = await self._transcribe(self._files, self._speech, file_id, link)
            if not text:
                await self._say(chat_id, link.language, "voice_not_understood")
            elif len(text) > self._max_chars:
                await self._say(
                    chat_id, link.language, "too_long", limit=self._max_chars
                )
            else:
                await self._ask(chat_id, link, text)

    @staticmethod
    async def _transcribe(
        files: ChatFiles, speech: SpeechToText, file_id: str, link: ChatLink
    ) -> str:
        """The voice note's transcript, `""` when it could not be fetched or understood."""
        try:
            audio = await files.download_file(file_id)
            return (await speech.transcribe(audio, link.language)).strip()
        except (FileNotDownloadedError, TranscriptionError):  # the adapters logged it
            return ""

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
        await self._deliver(start.case_id)
        # Requests created before the chat was linked could not be shown until now.
        if self._present_approvals:
            await self._present_approvals.execute_for_case(start.case_id)
        # A specialist may have resolved the case before the customer opened the chat.
        if self._resolutions:
            await self._resolutions.deliver_pending(start.case_id)

    async def _ask(self, chat_id: int, link: ChatLink, text: str) -> None:
        conversation = self._store.get_conversation(link.case_id)
        if conversation is None:  # the case is still on its way to the agent
            await self._say(chat_id, link.language, "processing", folio=link.folio)
            return
        try:
            turn = await self._conversations.converse(
                conversation.owner, conversation.session_id, text
            )
        except ConversationNotFoundError:
            await self._say(chat_id, link.language, "conversation_expired")
            return
        await self._send_reply(chat_id, link.case_id, turn.reply)
        # Approval requests go after the reply that explains them.
        if self._present_approvals and turn.approvals:
            await self._present_approvals.execute(turn.approvals)

    async def _send_reply(self, chat_id: int, case_id: str, reply: str) -> None:
        if not reply.strip():
            return
        if not await self._deliver(case_id):  # earlier replies first, in order
            self._store.queue_reply(case_id, reply)
            return
        try:
            await self._messenger.send(chat_id, reply)
        except MessageNotSentError:
            self._store.queue_reply(case_id, reply)

    async def _deliver(self, case_id: str) -> bool:
        """Send the case's waiting replies; False when one could not be sent (kept queued)."""
        try:
            await deliver_replies(self._store, self._messenger, self._audit, case_id)
        except MessageNotSentError:
            return False
        return True

    async def _say(
        self, chat_id: int, language: Language, key: str, **values: object
    ) -> None:
        with suppress(MessageNotSentError):  # best effort: the adapter logged it
            await self._messenger.send(chat_id, bot_message(key, language, **values))
