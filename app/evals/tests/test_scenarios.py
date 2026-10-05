"""Every scenario file is well formed before any model is called."""

from pathlib import Path

import pytest
import yaml

from harness import scenario_turns

SCENARIOS = sorted((Path(__file__).resolve().parents[1] / "scenarios").glob("*.yaml"))
OUTCOMES = {
    "explain", "approval_requested", "handoff", "clarify",
    "ask_which", "out_of_scope", "no_action", "refuse_session",
}
TASKS = [(path.name, task) for path in SCENARIOS for task in yaml.safe_load(path.read_text(encoding="utf-8"))]


def test_task_ids_are_unique():
    ids = [task["vars"]["id"] for _, task in TASKS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(("file", "task"), TASKS, ids=[t["vars"]["id"] for _, t in TASKS])
def test_task_is_well_formed(file, task):
    vars_ = task["vars"]
    assert task["description"] == vars_["id"]
    assert task["metadata"]["kind"] in ("regression", "capability")
    assert task["metadata"]["jtbd"]
    assert vars_["expect"]["outcome"] in OUTCOMES
    # promptfoo expands any top-level list in vars into one test per element.
    assert not [k for k, v in vars_.items() if isinstance(v, list)], f"{file}: list in vars"
    turns = scenario_turns(vars_)
    assert turns is None or len(turns) >= 1


def test_a_string_is_rejected_as_turns():
    with pytest.raises(ValueError):
        scenario_turns({"id": "x", "script": {"turns": "hola"}})


def test_every_scenario_customer_and_transaction_exists_in_the_world():
    import json

    world = json.loads((SCENARIOS[0].parents[1] / "fixtures" / "eval_world.json").read_text(encoding="utf-8"))
    customers = {c["customer_id"] for c in world["customers"]}
    transactions = {t["transaction_id"] for t in world["transactions"]}
    assert {t["customer_id"] for t in world["transactions"]} <= customers
    for _, task in TASKS:
        assert task["vars"].get("customer_id", next(iter(customers))) in customers
        # Transactions a scenario posts during the conversation (late postings) count too.
        posted = {
            t["transaction_id"]
            for update in (task["vars"].get("world") or {}).get("updates", [])
            for t in update["transactions"]
        }
        assert set(task["vars"]["expect"].get("txn_any", [])) <= transactions | posted


def test_world_loads_through_the_agent_repository():
    from lir_agent.infrastructure.persistence import FixtureTransactionRepository

    repository = FixtureTransactionRepository(SCENARIOS[0].parents[1] / "fixtures" / "eval_world.json")
    adjustment = next(t for t in repository.list_transactions("CLI-EVAL-AR1") if t.transaction_id == "TXN-A1-003")
    assert (adjustment.transaction_type, adjustment.channel, adjustment.merchant_name) == ("Adjustment", "Web", None)


def test_world_faults_updates_and_late_postings():
    from lir_agent.infrastructure.persistence import FixtureTransactionRepository

    import harness

    scenario = {
        "id": "x",
        "world": {
            "faults": ["records_down"],
            "updates": [{"after_turn": 1, "transactions": [_late("CLI-DEMO-001")]}],
        },
    }
    assert harness._faults(scenario) == {"records_down"}
    updates = harness._updates(scenario)
    assert list(updates) == [1]

    records = harness._UpdatableRecords(FixtureTransactionRepository(harness.WORLD))
    before = [t.transaction_id for t in records.list_transactions("CLI-DEMO-001")]
    records.post(updates[1])
    after = [t.transaction_id for t in records.list_transactions("CLI-DEMO-001")]
    assert after[0] == "TXN-LATE-1" and after[1:] == before
    assert "TXN-LATE-1" not in [t.transaction_id for t in records.list_transactions("CLI-DEMO-002")]


def test_a_misspelled_world_key_or_fault_is_an_error():
    import harness

    with pytest.raises(ValueError, match="unknown world keys"):
        harness._faults({"id": "x", "world": {"fault": ["records_down"]}})
    with pytest.raises(ValueError, match="unknown faults"):
        harness._faults({"id": "x", "world": {"faults": ["record_down"]}})


def _late(customer_id: str) -> dict:
    return {
        "customer_id": customer_id,
        "transaction_id": "TXN-LATE-1",
        "transaction_date": "2026-06-30T10:00:00",
        "amount": 10.0,
        "currency": "MXN",
        "merchant_name": "LATE SHOP",
        "transaction_status": "Approved",
    }
