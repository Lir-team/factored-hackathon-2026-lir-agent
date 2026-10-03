import json
from datetime import date

import pytest
from decision_layer import ChainDecisionModel, DecisionError

from lir_agent.application.use_cases import SearchCriteria
from lir_agent.container import build_container
from lir_agent.domain.errors import InvalidSearchCriteriaError, TransactionNotFoundError
from lir_agent.domain.session import SessionState
from lir_agent.infrastructure.audit import InMemoryAuditSink
from tests.support import IN_SCOPE, OTHER_CUSTOMER_TXN, ScriptedDecisions


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


def test_confirming_a_duplicate_covers_both_charges_of_the_pair(harness, session):
    # The agent looks at both duplicates; the last lookup is the pending confirmation,
    # but the customer confirmed "the duplicate charge", so either charge may be disputed.
    gather(harness, session, "TXN-D1-006")
    gather(harness, session, "TXN-D1-005")
    session.confirmed_transaction = session.pending_confirmation
    guard = harness.container.dispute_guard
    assert guard.check(session, "TXN-D1-006") is None
    assert guard.check(session, "TXN-D1-005") is None


def test_confirmation_does_not_cover_an_unrelated_charge(harness, session):
    gather(harness, session, "TXN-D1-004")
    gather(harness, session, "TXN-D1-006")
    session.confirmed_transaction = session.pending_confirmation
    assert harness.container.dispute_guard.check(session, "TXN-D1-004") is not None


def test_exact_amount_matches_hide_near_ones(make_harness, settings, tmp_path, context):
    txn = {
        "customer_id": "CLI-DEMO-001",
        "transaction_date": "2026-06-05T10:00:00",
        "currency": "MXN",
        "merchant_category": "Retail",
        "transaction_status": "Approved",
    }
    world = {
        "customers": [{"customer_id": "CLI-DEMO-001", "country": "Mexico"}],
        "transactions": [
            {
                **txn,
                "transaction_id": "T-EXACT-1",
                "amount": 250.0,
                "merchant_name": "CINE",
            },
            {
                **txn,
                "transaction_id": "T-EXACT-2",
                "amount": 250.0,
                "merchant_name": "FARMACIA",
            },
            {
                **txn,
                "transaction_id": "T-NEAR",
                "amount": 245.5,
                "merchant_name": "OXXO",
            },
        ],
    }
    (tmp_path / "world.json").write_text(json.dumps(world), encoding="utf-8")
    settings.fixture_path = tmp_path / "world.json"
    session = SessionState(context.state)
    find = make_harness().container.find_candidates

    exact = find.execute(session, SearchCriteria(amount=250))
    assert {session.resolve_ref(c["transaction_ref"]) for c in exact["candidates"]} == {
        "T-EXACT-1",
        "T-EXACT-2",
    }
    near = find.execute(
        session, SearchCriteria(amount=244)
    )  # no exact match: tolerance
    assert [session.resolve_ref(c["transaction_ref"]) for c in near["candidates"]] == [
        "T-NEAR"
    ]


def test_a_fallback_in_the_decision_chain_is_audited(settings, context):
    # A model that always fails must not go unnoticed behind the baseline.
    class Broken:
        name = "broken"

        def decide(self, state, questions):
            raise DecisionError("provider rejected the request")

    audit = InMemoryAuditSink()
    chain = ChainDecisionModel([Broken(), ScriptedDecisions(IN_SCOPE)])
    container = build_container(settings, audit=audit, decisions=chain)
    container.route_turn.execute(
        SessionState(context.state), "No reconozco un cargo", "s1"
    )
    fallback = [e for e in audit.entries if e["event"] == "decision_fallback"]
    assert fallback[0]["failures"] == [["broken", "provider rejected the request"]]


@pytest.mark.parametrize(
    "criteria",
    [
        SearchCriteria(),  # "tengo un cobro raro"
        SearchCriteria(
            date_from=date(2026, 6, 8), date_to=date(2026, 6, 14)
        ),  # "la semana pasada"
        SearchCriteria(date_from=date(2026, 6, 1)),  # open range
    ],
)
def test_a_vague_search_asks_for_a_detail_instead_of_listing(
    harness, session, criteria
):
    # Product decision: ask for the amount, merchant or exact date before showing charges.
    result = harness.container.find_candidates.execute(session, criteria)
    assert result["status"] == "needs_detail"
    assert "candidates" not in result


@pytest.mark.parametrize(
    "criteria",
    [
        SearchCriteria(amount=99),
        SearchCriteria(merchant_hint="Oxxo"),
        SearchCriteria(
            date_from=date(2026, 6, 11), date_to=date(2026, 6, 11)
        ),  # one day
    ],
)
def test_one_concrete_detail_is_enough_to_search(harness, session, criteria):
    assert (
        harness.container.find_candidates.execute(session, criteria)["status"] == "ok"
    )


TODAY = date(2026, 6, 15)


@pytest.fixture
def find_today(settings, tmp_path):
    """The find use case over a fixed 'today' and charges on 06-10, 06-14 and 06-20."""
    txn = {
        "customer_id": "CLI-DEMO-001",
        "currency": "MXN",
        "merchant_category": "Retail",
        "transaction_status": "Approved",
        "amount": 10.0,
        "merchant_name": "CINE",
    }
    day = lambda d: f"2026-06-{d}T10:00:00"  # noqa: E731
    world = {
        "customers": [{"customer_id": "CLI-DEMO-001", "country": "Mexico"}],
        "transactions": [
            {**txn, "transaction_id": "T-10", "transaction_date": day(10)},
            {**txn, "transaction_id": "T-14", "transaction_date": day(14)},
            {**txn, "transaction_id": "T-20", "transaction_date": day(20)},
        ],
    }
    (tmp_path / "world.json").write_text(json.dumps(world), encoding="utf-8")
    settings.fixture_path = tmp_path / "world.json"
    container = build_container(
        settings,
        audit=InMemoryAuditSink(),
        decisions=ScriptedDecisions(IN_SCOPE),
        today=lambda: TODAY,
    )
    return container.find_candidates


def found_ids(session, result):
    return {session.resolve_ref(c["transaction_ref"]) for c in result["candidates"]}


@pytest.mark.parametrize(
    "criteria",
    [
        SearchCriteria(date_from=TODAY),  # date_to defaults to today: a one-day span
        SearchCriteria(date_from=date(2026, 6, 14)),  # yesterday, within the range
        SearchCriteria(
            date_from=date(2026, 6, 14), date_to=date(2026, 6, 14)
        ),  # exact day
    ],
)
def test_date_from_alone_is_a_concrete_detail_when_close_to_today(
    find_today, session, criteria
):
    assert find_today.execute(session, criteria)["status"] == "ok"


@pytest.mark.parametrize(
    "criteria",
    [
        SearchCriteria(date_from=date(2026, 6, 10)),  # five days up to today
        SearchCriteria(date_to=date(2026, 6, 14)),  # no start date
        SearchCriteria(
            date_from=date(2026, 6, 14), date_to=date(2026, 6, 10)
        ),  # inverted
        SearchCriteria(date_from=date(2026, 6, 20)),  # in the future: today < date_from
    ],
)
def test_other_date_shapes_ask_for_a_detail(find_today, session, criteria):
    assert find_today.execute(session, criteria)["status"] == "needs_detail"


def test_a_missing_date_to_filters_up_to_today(find_today, session):
    result = find_today.execute(session, SearchCriteria(date_from=date(2026, 6, 14)))
    assert found_ids(session, result) == {"T-14"}  # T-20 is after today


def test_an_inverted_date_range_is_rejected_when_parsed():
    with pytest.raises(InvalidSearchCriteriaError) as error:
        SearchCriteria.parse(None, "2026-06-14", "2026-06-10", None)
    assert error.value.field == "date_to"


MERCHANT_KEY = "comercio"


@pytest.fixture
def find_merchants(settings, tmp_path):
    """Build the find use case over the given (id, merchant, amount) rows.

    Returns (find, decisions); a merchant of None models a non-Purchase row.
    """

    def _build(rows, answers=None):
        txn = {
            "customer_id": "CLI-DEMO-001",
            "currency": "MXN",
            "merchant_category": "Retail",
            "transaction_status": "Approved",
        }
        world = {
            "customers": [{"customer_id": "CLI-DEMO-001", "country": "Mexico"}],
            "transactions": [
                {
                    **txn,
                    "transaction_id": tid,
                    "merchant_name": merchant,
                    "amount": amount,
                    "transaction_date": f"2026-06-{day:02d}T10:00:00",
                }
                for tid, merchant, amount, day in rows
            ],
        }
        (tmp_path / "world.json").write_text(json.dumps(world), encoding="utf-8")
        settings.fixture_path = tmp_path / "world.json"
        decisions = ScriptedDecisions({**IN_SCOPE, **(answers or {})})
        container = build_container(
            settings,
            audit=InMemoryAuditSink(),
            decisions=decisions,
            today=lambda: TODAY,
        )
        return container.find_candidates, decisions

    return _build


def found_in_order(session, result):
    return [session.resolve_ref(c["transaction_ref"]) for c in result["candidates"]]


def test_a_merchant_nobody_has_is_not_answered_with_other_charges(
    find_merchants, session
):
    # Real bug: a customer whose rows carry no merchant asked for "Cine Premium" and got
    # their latest charges presented as that merchant.
    find, decisions = find_merchants([("T-1", None, 10.0, 10), ("T-2", None, 20.0, 12)])
    result = find.execute(session, SearchCriteria(merchant_hint="Cine Premium"))
    assert result["status"] == "no_merchant_match"
    assert result["candidates"] == []
    assert result["total_matches"] == 0
    assert result["merchant_hint"] == "Cine Premium"
    assert "note" in result
    assert decisions.calls == []  # nothing to choose from: no D4 call


def test_a_merchant_search_returns_only_the_charges_of_that_merchant(
    find_merchants, session
):
    find, _ = find_merchants(
        [
            ("T-CINE-OLD", "Cine Premium", 10.0, 8),
            ("T-OTHER", "Farmacia", 10.0, 9),
            ("T-NONE", None, 10.0, 10),
            ("T-CINE-NEW", "CINE PREMIUM", 10.0, 12),
        ]
    )
    result = find.execute(session, SearchCriteria(merchant_hint="cine premium"))
    assert result["status"] == "ok"
    assert found_in_order(session, result) == ["T-CINE-NEW", "T-CINE-OLD"]
    assert result["total_matches"] == 2


def test_a_fuzzy_decision_pick_comes_first_and_the_rest_is_excluded(
    find_merchants, session
):
    # "oxxo tienda" is not a substring of "OXXO": only the D4 pick can match it.
    find, _ = find_merchants(
        [
            ("T-A", "Farmacia", 10.0, 12),
            ("T-B", "OXXO", 10.0, 9),
            ("T-C", "Cine", 10.0, 11),
        ],
        {MERCHANT_KEY: ("c2", 0.9)},  # options follow the repository order: A, C, B
    )
    result = find.execute(session, SearchCriteria(merchant_hint="oxxo tienda"))
    assert found_in_order(session, result) == ["T-B"]


def test_a_decision_pick_is_listed_before_substring_matches(find_merchants, session):
    find, _ = find_merchants(
        [("T-NEW", "Oxxo Sur", 10.0, 12), ("T-OLD", "OXXO", 10.0, 9)],
        {MERCHANT_KEY: ("c1", 0.9)},
    )
    result = find.execute(session, SearchCriteria(merchant_hint="oxxo"))
    assert found_in_order(session, result) == ["T-OLD", "T-NEW"]


def test_no_merchant_match_answer_beats_an_amount_match(find_merchants, session):
    find, _ = find_merchants(
        [("T-1", "Farmacia", 250.0, 12), ("T-2", "Cine", 250.0, 11)],
        {MERCHANT_KEY: ("ninguno", 0.9)},
    )
    result = find.execute(
        session, SearchCriteria(amount=250, merchant_hint="Gasolinera")
    )
    assert result["status"] == "no_merchant_match"
    assert result["candidates"] == []


def test_a_single_amount_match_of_another_merchant_does_not_slip_through(
    find_merchants, session
):
    find, decisions = find_merchants(
        [("T-1", "Farmacia", 250.0, 12), ("T-2", "Cine", 99.0, 11)],
        {MERCHANT_KEY: ("ninguno", 0.9)},
    )
    result = find.execute(session, SearchCriteria(amount=250, merchant_hint="Cine"))
    # T-2 is the Cine charge but does not match the amount; T-1 matches the amount only.
    assert result["status"] == "no_merchant_match"
    assert len(decisions.calls) == 1


def test_a_failing_decision_falls_back_to_the_substring(find_merchants, session):
    find, _ = find_merchants(
        [("T-1", "Farmacia", 10.0, 12), ("T-2", "Cine", 10.0, 11)], {}
    )  # no scripted D4 answer: the stub raises DecisionError
    result = find.execute(session, SearchCriteria(merchant_hint="cine"))
    assert found_in_order(session, result) == ["T-2"]


def test_merchantless_charges_are_not_offered_to_the_decision(find_merchants, session):
    find, decisions = find_merchants(
        [("T-1", None, 10.0, 12), ("T-2", "Cine", 10.0, 11), ("T-3", None, 10.0, 10)],
        {MERCHANT_KEY: ("ninguno", 0.9)},
    )
    find.execute(session, SearchCriteria(merchant_hint="Gasolinera"))
    ((_, questions),) = decisions.calls
    options = questions[MERCHANT_KEY].options
    assert "None" not in " ".join(options.values())
    assert len(options) == 2  # the one merchant plus "ninguno"
