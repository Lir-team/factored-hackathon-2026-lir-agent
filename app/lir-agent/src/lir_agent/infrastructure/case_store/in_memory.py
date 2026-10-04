"""CaseStore kept in process memory: tests and single-instance local runs."""

import threading
from datetime import datetime

from lir_agent.application.ports import StoredReceipt
from lir_agent.domain.case_intake import CaseConversation, CaseStart
from lir_agent.domain.telegram import ChatLink


class InMemoryCaseStore:
    """CaseStore kept in process memory; lost when the process restarts."""

    def __init__(self) -> None:
        """Start empty; a lock keeps tokens, conversations and replies race-free."""
        self._receipts: dict[str, StoredReceipt] = {}
        self._tokens: dict[str, tuple[CaseStart, datetime]] = {}
        self._chats: dict[int, ChatLink] = {}
        self._case_chats: dict[str, int] = {}
        self._conversations: dict[str, CaseConversation] = {}
        self._replies: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def get_receipt(self, idempotency_key: str) -> StoredReceipt | None:
        """Return the answer stored for this key, or None."""
        return self._receipts.get(idempotency_key)

    def save_receipt(self, idempotency_key: str, stored: StoredReceipt) -> None:
        """Keep a successful answer for replay."""
        self._receipts[idempotency_key] = stored

    def add_start_token(
        self, token: str, start: CaseStart, expires_at: datetime
    ) -> None:
        """Bind a single-use start token to a case until `expires_at`."""
        with self._lock:
            self._tokens[token] = (start, expires_at)

    def consume_start_token(self, token: str, now: datetime) -> CaseStart | None:
        """Burn the token and return its case; None when unknown, used or expired."""
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None or now >= entry[1]:
            return None
        return entry[0]

    def link_chat(self, chat_id: int, link: ChatLink) -> None:
        """Bind a chat to a case, replacing the chat's previous link."""
        with self._lock:
            self._chats[chat_id] = link
            self._case_chats[link.case_id] = chat_id

    def get_chat_link(self, chat_id: int) -> ChatLink | None:
        """Return the chat's current link, or None."""
        return self._chats.get(chat_id)

    def get_case_chat(self, case_id: str) -> int | None:
        """Return the chat last linked to the case, or None."""
        return self._case_chats.get(case_id)

    def add_conversation(self, conversation: CaseConversation) -> bool:
        """Keep the case's conversation unless it has one; False when it already had one."""
        with self._lock:
            if conversation.case_id in self._conversations:
                return False
            self._conversations[conversation.case_id] = conversation
            return True

    def get_conversation(self, case_id: str) -> CaseConversation | None:
        """Return the case's conversation, or None while the case was not worked."""
        return self._conversations.get(case_id)

    def queue_reply(self, case_id: str, text: str) -> None:
        """Keep an agent reply until a chat is linked to the case."""
        with self._lock:
            self._replies.setdefault(case_id, []).append(text)

    def pop_replies(self, case_id: str) -> list[str]:
        """Remove and return the case's queued replies, oldest first."""
        with self._lock:
            return self._replies.pop(case_id, [])
