import pytest

from lir_agent.application.use_cases import SearchCriteria
from lir_agent.domain.errors import InvalidSearchCriteriaError, TransactionNotFoundError
from lir_agent.domain.session import SessionState
from tests.support import OTHER_CUSTOMER_TXN


@pytest.fixture
def session(context) -> SessionState:
    return SessionState(context.state)


def gather(harness, session, transaction_id):
    return harness.container.gather_evidence.execute(
        session, session.ref_for(transaction_id)
    )


@pytest.mark.parametrize(
    ("transaction_id", "lane", "rule_id"),
    [
        ("TXN-D1-004", "explain", "C9_habitual_merchant"),
        ("TXN-D1-006", "dispute", "C7_duplicate"),
        ("TXN-D1-007", "explain", "C3_pending"),
        ("TXN-D1-008", "escalate", "C1_high_fraud_score"),
    ],
)
def test_case_lanes_follow_policy(harness, session, transaction_id, lane, rule_id):
    result = gather(harness, session, transaction_id)
    assert result["outcome"]["lane"] == lane
    assert session.case_outcome.rule_id == rule_id  # rule ids stay internal
    assert "rule_id" not in result["outcome"]


def test_dispute_lane_registers_confirmation_request(harness, session):
    gather(harness, session, "TXN-D1-006")
    assert session.pending_confirmation == "TXN-D1-006"


def test_other_customers_transaction_is_not_found(harness, session):
    with pytest.raises(TransactionNotFoundError):
        gather(harness, session, OTHER_CUSTOMER_TXN)


def test_unknown_ref_is_not_found(harness, session):
    with pytest.raises(TransactionNotFoundError):
        harness.container.gather_evidence.execute(session, "T99")


def test_find_by_amount_returns_both_duplicates(harness, session):
    result = harness.container.find_candidates.execute(
        session, SearchCriteria(amount=245.5)
    )
    resolved = {session.resolve_ref(c["transaction_ref"]) for c in result["candidates"]}
    assert resolved == {"TXN-D1-005", "TXN-D1-006"}


def test_llm_facing_results_are_minimized(harness, session):
    found = harness.container.find_candidates.execute(
        session, SearchCriteria(amount=38900)
    )
    evidence = harness.container.gather_evidence.execute(
        session, found["candidates"][0]["transaction_ref"]
    )
    payload = str(found) + str(evidence)
    for leaked in (
        "TXN-",
        "CLI-",
        "fraud_score",
        "transaction_country",
        "customer_country",
        "86.5",
    ):
        assert leaked not in payload


def test_invalid_date_is_rejected():
    with pytest.raises(InvalidSearchCriteriaError):
        SearchCriteria.parse(None, "14/06/2026", None, None)


def test_open_dispute_blocked_without_confirmation(harness, session):
    ref = session.ref_for("TXN-D1-006")
    harness.container.gather_evidence.execute(session, ref)
    result = harness.container.open_dispute.execute(session, ref, "duplicate")
    assert result == {"status": "blocked", "reason": "confirmation_required"}


def test_handoff_packet_uses_verified_facts(harness, session):
    gather(harness, session, "TXN-D1-008")
    result = harness.container.request_handoff.execute(session, "summary", ["q1"])
    packet = harness.container.cases.get_handoff(result["handoff_id"])
    assert result["status"] == "submitted"
    assert packet.verified_evidence[0].transaction_id == "TXN-D1-008"
    assert packet.model_summary == "summary"
