from datetime import timedelta

import pytest

from lir_agent.domain.evidence import EvidenceBuilder
from lir_agent.infrastructure.persistence import FixtureTransactionRepository
from lir_agent.infrastructure.resources import ResourceLoader
from tests.support import CUSTOMER


@pytest.fixture
def builder(settings) -> EvidenceBuilder:
    return EvidenceBuilder(
        ResourceLoader().load_country_resolver(settings.reference_path),
        timedelta(hours=24),
    )


@pytest.fixture
def repository(settings) -> FixtureTransactionRepository:
    return FixtureTransactionRepository(settings.fixture_path)


def evidence_for(builder, repository, transaction_id):
    history = repository.list_transactions(CUSTOMER)
    txn = next(t for t in history if t.transaction_id == transaction_id)
    return builder.build(txn, history, repository.get_customer(CUSTOMER))


def test_duplicate_within_window(builder, repository):
    assert evidence_for(builder, repository, "TXN-D1-006").duplicate_of == [
        "TXN-D1-005"
    ]


def test_recurring_merchant_counts_prior_payments(builder, repository):
    assert evidence_for(builder, repository, "TXN-D1-004").merchant_prior_count == 3


def test_foreign_country_uses_iso_codes(builder, repository):
    evidence = evidence_for(builder, repository, "TXN-D1-008")
    assert (
        evidence.customer_country,
        evidence.transaction_country,
        evidence.foreign,
    ) == ("MX", "AR", True)
