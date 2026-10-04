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


def test_evidence_carries_transaction_type_and_channel(builder, repository):
    evidence = evidence_for(builder, repository, "TXN-D1-001")
    assert (evidence.transaction_type, evidence.channel) == ("Purchase", "Web")


@pytest.mark.parametrize(
    ("value", "code"),
    [("BR", "BR"), ("Brasil", "BR"), ("US", "US"), ("Estados Unidos", "US"), ("ES", "ES")],
)
def test_every_country_in_the_staged_data_resolves(settings, value, code):
    resolver = ResourceLoader().load_country_resolver(settings.reference_path)
    assert resolver.code(value) == code


def test_a_charge_abroad_is_foreign(builder, repository):
    history = repository.list_transactions(CUSTOMER)
    txn = next(t for t in history if t.transaction_id == "TXN-D1-008")
    abroad = txn.model_copy(update={"transaction_country": "Brazil"})

    evidence = builder.build(abroad, history, repository.get_customer(CUSTOMER))

    assert (evidence.transaction_country, evidence.foreign, evidence.country_resolved) == (
        "BR",
        True,
        True,
    )


@pytest.mark.parametrize("country", ["Atlantis", None])
def test_an_unknown_country_is_not_taken_for_a_domestic_one(builder, repository, country):
    history = repository.list_transactions(CUSTOMER)
    txn = next(t for t in history if t.transaction_id == "TXN-D1-008")
    unknown = txn.model_copy(update={"transaction_country": country})

    evidence = builder.build(unknown, history, repository.get_customer(CUSTOMER))

    assert (evidence.foreign, evidence.country_resolved) == (False, False)
