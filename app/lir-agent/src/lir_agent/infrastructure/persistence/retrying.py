"""Bounded retries in front of the bank's records (Bases §6: "bounded retries, safe fallback").

A read that keeps failing becomes `DataUnavailableError` after `attempts` tries: the tools
turn it into "the records are unavailable" and the agent hands the case to a person instead
of guessing. Reads only; nothing here writes.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from lir_agent.application.ports import TransactionRepository
from lir_agent.domain.errors import DataUnavailableError
from lir_agent.domain.models import Customer, Transaction

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RetryingTransactionRepository:
    """Retries a failing read a bounded number of times, then reports the records unavailable."""

    def __init__(
        self,
        inner: TransactionRepository,
        attempts: int = 2,
        backoff_s: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Wrap the repository; `attempts` counts the first try."""
        self._inner = inner
        self._attempts = max(1, attempts)
        self._backoff_s = backoff_s
        self._sleep = sleep
        self.name = getattr(inner, "name", type(inner).__name__)

    def list_transactions(self, customer_id: str) -> list[Transaction]:
        """The customer's transactions, most recent first."""
        return self._read("list_transactions", lambda: self._inner.list_transactions(customer_id))

    def get_customer(self, customer_id: str) -> Customer | None:
        """The customer, or None when the id is unknown."""
        return self._read("get_customer", lambda: self._inner.get_customer(customer_id))

    def _read(self, what: str, read: Callable[[], T]) -> T:
        for attempt in range(1, self._attempts + 1):
            try:
                return read()
            except Exception as error:  # the store's own errors vary (DuckDB, network, files)
                logger.warning(
                    "%s failed (attempt %d/%d): %s", what, attempt, self._attempts, type(error).__name__
                )
                if attempt == self._attempts:
                    raise DataUnavailableError(what) from error
                self._sleep(self._backoff_s * attempt)
        raise AssertionError("unreachable")  # pragma: no cover
