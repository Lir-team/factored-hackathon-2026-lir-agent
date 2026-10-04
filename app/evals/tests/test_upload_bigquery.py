"""Eval trials map one-to-one to the eval_trials BigQuery schema, tagged with the run."""

import json
from datetime import UTC, datetime

from upload_bigquery import main, to_bq_rows

RUN_AT = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
SCHEMA_FIELDS = {
    "run_id", "run_at", "git_sha", "agent_model", "scenario_id", "jtbd", "kind", "lang",
    "expected", "code_pass", "rubric_pass", "outcome", "safety", "grounding", "language",
    "efficiency", "quality", "handoffs", "disputes", "latency_ms", "cost_usd", "error", "reason",
}


def report_row(**overrides):
    row = {
        "id": "c2_duplicate_es", "jtbd": ["C2"], "kind": "regression", "lang": "es",
        "expected": "dispute", "code_pass": True, "rubric_pass": None,
        "dims": {"outcome": 1, "safety": 1, "grounding": 1, "language": 1, "efficiency": 0.5,
                 "calidad": None},
        "error": None, "handoffs": 0, "disputes": 1, "latency_ms": 2100.0, "cost": 0.0004,
        "reason": "ok",
    }
    return {**row, **overrides}


def test_rows_follow_the_table_schema():
    [row] = to_bq_rows([report_row()], run_id="r1", run_at=RUN_AT, sha="abc1234", model="openai/gpt-4o")
    assert set(row) == SCHEMA_FIELDS
    assert row["run_at"] == "2026-10-04T18:00:00+00:00"
    assert row["quality"] is None and row["efficiency"] == 0.5
    assert row["cost_usd"] == 0.0004 and row["jtbd"] == ["C2"]


def test_long_reasons_are_truncated():
    [row] = to_bq_rows([report_row(reason="x" * 5000)], run_id="r", run_at=RUN_AT, sha=None, model=None)
    assert len(row["reason"]) == 1000


def test_dry_run_prints_one_json_row_per_trial(tmp_path, capsys):
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"results": {"results": [
        {"vars": {"id": "c1", "lang": "pt", "expect": {"outcome": "explain"}},
         "testCase": {"metadata": {"jtbd": ["C1"], "kind": "capability"}},
         "gradingResult": {"namedScores": {"outcome": 1, "safety": 1, "grounding": 1, "language": 1}},
         "response": {"metadata": {"latency_ms": 1500, "cost_usd": 0.0003}}},
    ]}}), encoding="utf-8")
    assert main([str(results), "--dry-run", "--model", "openai/gpt-4o", "--run-id", "r1"]) == 0
    [line] = capsys.readouterr().out.strip().splitlines()
    row = json.loads(line)
    assert row["scenario_id"] == "c1" and row["lang"] == "pt" and row["code_pass"] is True
    assert row["agent_model"] == "openai/gpt-4o" and row["run_id"] == "r1"
