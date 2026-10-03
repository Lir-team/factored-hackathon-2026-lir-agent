"""Every scenario file is well formed before any model is called."""

from pathlib import Path

import pytest
import yaml

from harness import scenario_turns

SCENARIOS = sorted((Path(__file__).resolve().parents[1] / "scenarios").glob("*.yaml"))
OUTCOMES = {
    "explain", "dispute", "confirm_pending", "handoff", "clarify",
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
        assert set(task["vars"]["expect"].get("txn_any", [])) <= transactions


def test_world_loads_through_the_agent_repository():
    from lir_agent.infrastructure.persistence import FixtureTransactionRepository

    repository = FixtureTransactionRepository(SCENARIOS[0].parents[1] / "fixtures" / "eval_world.json")
    adjustment = next(t for t in repository.list_transactions("CLI-EVAL-AR1") if t.transaction_id == "TXN-A1-003")
    assert (adjustment.transaction_type, adjustment.channel, adjustment.merchant_name) == ("Adjustment", "Web", None)
