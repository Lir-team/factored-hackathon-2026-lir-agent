"""Run the scenario evals locally with promptfoo, then print the report.

    uv run python run.py                       # every scenario, 1 trial each
    uv run python run.py --repeat 3            # 3 trials per scenario (pass^k)
    uv run python run.py --filter-pattern dup  # scenarios whose description matches
    uv run python run.py --model openai/gpt-5.4-mini   # agent model override

Extra arguments go to `promptfoo eval` unchanged. Results land in out/results.json;
`npx promptfoo@latest view` opens the transcripts in the local web viewer.
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
PROMPTFOO = "promptfoo@0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="agent model (LiteLLM string), overrides LLM_MODEL")
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
    OUT.parent.mkdir(exist_ok=True)
    npx = shutil.which("npx") or "npx"
    cmd = [npx, "-y", PROMPTFOO, "eval", "-c", "promptfooconfig.yaml", "-o", str(OUT), "--no-cache", *extra]
    code = subprocess.call(cmd, cwd=EVALS_DIR, env=env)
    if OUT.exists():
        report.main([str(OUT)])
    return code


if __name__ == "__main__":
    sys.exit(main())
