"""CaseStore kept in process memory: tests and single-instance local runs."""

import threading
from datetime import datetime

from lir_agent.application.ports import StoredReceipt
from lir_agent.domain.case_intake import CaseStart
from lir_agent.domain.telegram import ChatLink


class InMemoryCaseStore:
    """CaseStore kept in process memory; lost when the process restarts."""

    def __init__(self) -> None:
        """Start empty; a lock keeps token consumption single-use across threads."""
        self._receipts: dict[str, StoredReceipt] = {}
        self._tokens: dict[str, tuple[CaseStart, datetime]] = {}
        self._chats: dict[int, ChatLink] = {}
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
        """Bind a chat to a case conversation, replacing any previous link."""
        self._chats[chat_id] = link

    def get_chat_link(self, chat_id: int) -> ChatLink | None:
        """Return the chat's current link, or None."""
        return self._chats.get(chat_id)
