"""CaseStore kept in process memory: tests and single-instance local runs."""

import threading
from datetime import datetime

from lir_agent.application.ports import StoredReceipt


class InMemoryCaseStore:
    """CaseStore kept in process memory; lost when the process restarts."""

    def __init__(self) -> None:
        """Start empty; a lock keeps token consumption single-use across threads."""
        self._receipts: dict[str, StoredReceipt] = {}
        self._tokens: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def get_receipt(self, idempotency_key: str) -> StoredReceipt | None:
        """Return the answer stored for this key, or None."""
        return self._receipts.get(idempotency_key)

    def save_receipt(self, idempotency_key: str, stored: StoredReceipt) -> None:
        """Keep a successful answer for replay."""
        self._receipts[idempotency_key] = stored

    def add_start_token(self, token: str, case_id: str, expires_at: datetime) -> None:
        """Bind a single-use start token to a case until `expires_at`."""
        with self._lock:
            self._tokens[token] = (case_id, expires_at)

    def consume_start_token(self, token: str, now: datetime) -> str | None:
        """Burn the token and return its case id; None when unknown, used or expired."""
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None or now >= entry[1]:
            return None
        return entry[0]
