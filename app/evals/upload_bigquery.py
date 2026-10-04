"""Upload the trials of an eval run to BigQuery, for the Looker Studio evaluation page.

    uv run python upload_bigquery.py out/results.json --table lir-agent:lir_analytics.eval_trials

The table (schema in lir-infra, `analytics.tf`) gets one row per trial, tagged with the run id,
time, git commit and agent model, so runs can be compared over time. Rows reuse `report.py`'s
reading of the promptfoo results, so the dashboard and the console report always agree.

Uses the `bq` CLI that ships with the Google Cloud SDK (no extra Python dependency); the table
can also come from EVALS_BQ_TABLE. `--dry-run` prints the rows instead of loading them.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from report import load_rows

TABLE_ENV = "EVALS_BQ_TABLE"
MODEL_ENV = "LLM_MODEL"


def git_sha() -> str | None:
    """Short commit of the working tree the run was made from, if git is available."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def to_bq_rows(
    rows: list[dict], *, run_id: str, run_at: datetime, sha: str | None, model: str | None
) -> list[dict]:
    """Map `report.load_rows` output to the eval_trials schema."""
    stamp = run_at.isoformat()
    return [
        {
            "run_id": run_id,
            "run_at": stamp,
            "git_sha": sha,
            "agent_model": model,
            "scenario_id": r["id"],
            "jtbd": list(r["jtbd"]),
            "kind": r["kind"],
            "lang": r["lang"],
            "expected": r["expected"],
            "code_pass": r["code_pass"],
            "rubric_pass": r["rubric_pass"],
            "outcome": r["dims"].get("outcome"),
            "safety": r["dims"].get("safety"),
            "grounding": r["dims"].get("grounding"),
            "language": r["dims"].get("language"),
            "efficiency": r["dims"].get("efficiency"),
            "quality": r["dims"].get("calidad"),
            "handoffs": r["handoffs"],
            "disputes": r["disputes"],
            "latency_ms": r["latency_ms"],
            "cost_usd": r["cost"],
            "error": r["error"],
            "reason": (r["reason"] or "")[:1000],
        }
        for r in rows
    ]


def load(rows: list[dict], table: str) -> None:
    """Append the rows to `table` (project:dataset.table) with `bq load`."""
    bq = shutil.which("bq") or shutil.which("bq.cmd")
    if bq is None:
        raise SystemExit("bq not found: install the Google Cloud SDK")
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        path = f.name
    try:
        subprocess.run(
            [bq, "load", "--source_format=NEWLINE_DELIMITED_JSON", table, path],
            check=True,
        )
    finally:
        Path(path).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", nargs="?", default="out/results.json", help="promptfoo results file")
    parser.add_argument("--table", default=os.environ.get(TABLE_ENV), help=f"project:dataset.table (or {TABLE_ENV})")
    parser.add_argument("--model", default=os.environ.get(MODEL_ENV), help=f"agent model of the run (or {MODEL_ENV})")
    parser.add_argument("--run-id", help="defaults to <UTC timestamp>-<git sha>")
    parser.add_argument("--dry-run", action="store_true", help="print the rows instead of loading them")
    args = parser.parse_args(argv)

    run_at = datetime.now(UTC)
    sha = git_sha()
    run_id = args.run_id or f"{run_at:%Y%m%dT%H%M%SZ}-{sha or 'nogit'}"
    rows = to_bq_rows(load_rows(Path(args.results)), run_id=run_id, run_at=run_at, sha=sha, model=args.model)
    if args.dry_run:
        print("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
        return 0
    if not args.table:
        parser.error(f"--table or {TABLE_ENV} is required")
    load(rows, args.table)
    print(f"Uploaded {len(rows)} trials of run {run_id} to {args.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
