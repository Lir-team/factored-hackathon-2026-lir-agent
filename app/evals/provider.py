"""promptfoo Python provider: one call = one isolated trial of the scenario in `vars`.

Each trial runs in its own Python process (`harness.py`), so trials that promptfoo runs
concurrently never share ADK sessions, event loops or LiteLLM state. The prompt text is
unused: the scenario comes from the test vars. The full trial goes back as `metadata` for
the graders and the report.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR))

from harness import RESULT_MARKER, transcript  # noqa: E402

TRIAL_TIMEOUT_S = 300


def call_api(prompt, options, context):
    config = options.get("config") or {}
    request = {"scenario": context["vars"], "agent_model": config.get("agent_model")}
    try:
        proc = subprocess.run(
            [sys.executable, str(EVALS_DIR / "harness.py")],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TRIAL_TIMEOUT_S,
            cwd=EVALS_DIR,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return {"error": f"trial timed out after {TRIAL_TIMEOUT_S}s"}
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(RESULT_MARKER)), None)
    if line is None:
        return {"error": f"trial failed (exit {proc.returncode}): {proc.stderr[-1500:]}"}
    trial = json.loads(line[len(RESULT_MARKER):])
    return {
        "output": transcript(trial),
        "metadata": trial,
        "cost": trial["cost_usd"],
        "tokenUsage": {
            "prompt": trial["input_tokens"],
            "completion": trial["output_tokens"],
            "total": trial["input_tokens"] + trial["output_tokens"],
        },
    }
