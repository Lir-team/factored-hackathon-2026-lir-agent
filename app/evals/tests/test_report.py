"""The regression gate fails on any failing or errored regression trial, and only on those."""

from report import regression_failures


def row(task_id, kind="regression", code_pass=True):
    return {"id": task_id, "kind": kind, "code_pass": code_pass}


def test_gate_passes_when_every_regression_trial_passes():
    assert regression_failures([row("a"), row("a"), row("b")]) == []


def test_one_failing_trial_fails_its_task():  # pass^k: every trial must pass
    assert regression_failures([row("a"), row("a", code_pass=False)]) == ["a"]


def test_capability_failures_do_not_fail_the_gate():
    assert regression_failures([row("c", kind="capability", code_pass=False)]) == []
