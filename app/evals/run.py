"""Run the scenario evals locally with promptfoo, then print the report.

    uv run python run.py                       # every scenario, 1 trial each
    uv run python run.py --repeat 3            # 3 trials per scenario (pass^k)
    uv run python run.py --filter-pattern dup  # scenarios whose description matches
    uv run python run.py --model openai/gpt-5.4-mini   # agent model override
    uv run python run.py --gate --repeat 3     # regression tasks only; exit 1 if any fails
    uv run python run.py --filter-failing out/previous.json   # rerun what failed last time

`--gate` exits non-zero only when a regression task fails the code graders in any trial
(pass^k) or a trial errors; the uncalibrated LLM rubric never fails the gate.

Extra arguments go to `promptfoo eval` unchanged. Results land in out/results.json and the
previous run is kept as out/previous.json. `npx promptfoo@0 view` opens the transcripts in
the local web viewer. See AGENTS.md for scoped runs.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

import report

EVALS_DIR = Path(__file__).resolve().parent
OUT = EVALS_DIR / "out" / "results.json"
PREVIOUS = EVALS_DIR / "out" / "previous.json"
PROMPTFOO = "promptfoo@0"
# cmd.exe metacharacters. npx on Windows is a .cmd that cmd.exe parses twice, so a
# literal "|" (e.g. in --filter-pattern "a|b") must reach it as "^^^|".
CMD_METACHARS = "^&|<>"


def cmd_escape(arg: str) -> str:
    """Escape an argument for a .cmd/.bat launcher that re-parses its arguments."""
    return "".join(f"^^^{c}" if c in CMD_METACHARS else c for c in arg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="agent model (LiteLLM string), overrides LLM_MODEL")
    parser.add_argument("--gate", action="store_true", help="regression tasks only; fail if any fails")
    args, extra = parser.parse_known_args()

    load_dotenv(EVALS_DIR.parent / "lir-agent" / ".env", override=False)
    env = {
        **os.environ,
        "PROMPTFOO_PYTHON": sys.executable,  # the provider runs in this uv environment
        "PROMPTFOO_DISABLE_TELEMETRY": "1",
        "PROMPTFOO_DISABLE_SHARING": "1",
        "PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if args.model:
        env["LLM_MODEL"] = args.model
    if args.gate:
        extra = ["--filter-metadata", "kind=regression", *extra]
    OUT.parent.mkdir(exist_ok=True)
    # Keep the last run for --filter-failing; a stale results file must never pass the gate.
    if OUT.exists():
        OUT.replace(PREVIOUS)
    npx = shutil.which("npx") or "npx"
    if npx.lower().endswith((".cmd", ".bat")):
        extra = [cmd_escape(arg) for arg in extra]
    cmd = [npx, "-y", PROMPTFOO, "eval", "-c", "promptfooconfig.yaml", "-o", str(OUT), "--no-cache", *extra]
    code = subprocess.call(cmd, cwd=EVALS_DIR, env=env)
    if not OUT.exists():
        print("promptfoo produced no results")
        return code or 1
    failing = report.main([str(OUT)])
    return (1 if failing else 0) if args.gate else code


if __name__ == "__main__":
    sys.exit(main())
