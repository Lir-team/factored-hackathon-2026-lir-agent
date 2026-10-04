"""Bounded retries in front of the bank's records, then a safe "unavailable"."""

import pytest

from lir_agent.domain.errors import DataUnavailableError
from lir_agent.infrastructure.persistence import RetryingTransactionRepository


class Flaky:
    """Fails the first `failures` reads, then answers."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def list_transactions(self, customer_id: str) -> list:
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("store down")
        return ["txn"]

    def get_customer(self, customer_id: str):
        return self.list_transactions(customer_id)


def test_a_transient_failure_is_retried():
    flaky = Flaky(failures=1)
    repository = RetryingTransactionRepository(flaky, attempts=2, sleep=lambda _: None)
    assert repository.list_transactions("CLI-1") == ["txn"]
    assert flaky.calls == 2


def test_retries_are_bounded_then_the_records_are_unavailable():
    flaky = Flaky(failures=5)
    repository = RetryingTransactionRepository(flaky, attempts=2, sleep=lambda _: None)
    with pytest.raises(DataUnavailableError):
        repository.get_customer("CLI-1")
    assert flaky.calls == 2


def test_a_tool_says_unavailable_instead_of_failing_the_turn(make_harness, context):
    harness = make_harness()
    harness.container.find_candidates._repository = RetryingTransactionRepository(  # noqa: SLF001
        Flaky(failures=9), attempts=2, sleep=lambda _: None
    )
    result = harness.toolkit.find_candidate_transactions(context, amount=245.5)
    assert result["status"] == "unavailable"
    assert "request_human_handoff" in result["instruction"]
