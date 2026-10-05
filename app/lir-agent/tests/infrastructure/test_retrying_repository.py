"""Bounded retries in front of the bank's records, then a safe "unavailable"."""

import pytest

from lir_agent.domain.errors import DataUnavailableError
from lir_agent.domain.models import Customer, Transaction
from lir_agent.infrastructure.persistence import RetryingTransactionRepository


class Flaky:
    """Fails the first `failures` reads with `error`, then answers."""

    name = "flaky"

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.error = error or OSError("store down")
        self.calls = 0

    def list_transactions(self, customer_id: str) -> list[Transaction]:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return []

    def get_customer(self, customer_id: str) -> Customer | None:
        self.list_transactions(customer_id)
        return None


def test_a_transient_failure_is_retried():
    flaky = Flaky(failures=1)
    repository = RetryingTransactionRepository(flaky, attempts=2, sleep=lambda _: None)
    assert repository.list_transactions("CLI-1") == []
    assert flaky.calls == 2


def test_retries_are_bounded_then_the_records_are_unavailable():
    flaky = Flaky(failures=5)
    repository = RetryingTransactionRepository(flaky, attempts=2, sleep=lambda _: None)
    with pytest.raises(DataUnavailableError):
        repository.get_customer("CLI-1")
    assert flaky.calls == 2


def test_a_bug_is_not_retried_nor_hidden_as_an_outage():
    flaky = Flaky(failures=1, error=KeyError("no such column"))
    repository = RetryingTransactionRepository(flaky, attempts=3, sleep=lambda _: None)
    with pytest.raises(KeyError):
        repository.list_transactions("CLI-1")
    assert flaky.calls == 1


def test_a_duckdb_io_error_is_transient():
    import duckdb

    flaky = Flaky(failures=1, error=duckdb.IOException("file locked"))
    repository = RetryingTransactionRepository(flaky, attempts=2, sleep=lambda _: None)
    assert repository.list_transactions("CLI-1") == []


def _down(harness) -> None:
    down = RetryingTransactionRepository(Flaky(failures=99), attempts=2, sleep=lambda _: None)
    for use_case in (
        harness.container.get_profile,
        harness.container.find_candidates,
        harness.container.gather_evidence,
    ):
        use_case._repository = down


def test_every_reading_tool_says_unavailable_instead_of_failing_the_turn(make_harness, context):
    from lir_agent.domain.session import SessionState

    harness = make_harness()
    _down(harness)
    ref = SessionState(context.state).ref_for("TXN-D1-006")
    results = [
        harness.toolkit.get_my_customer_profile(context),
        harness.toolkit.find_candidate_transactions(context, amount=245.5),
        harness.toolkit.get_transaction_evidence(context, ref),
    ]
    assert [r["status"] for r in results] == ["unavailable"] * 3
    assert all("request_human_handoff" in r["instruction"] for r in results)
