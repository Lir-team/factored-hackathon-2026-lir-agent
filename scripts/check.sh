#!/usr/bin/env bash
# Does everything still work? Run from anywhere in the repo:
#
#   scripts/check.sh          # fast: tests, lint and types of every app (no network, no cost)
#   scripts/check.sh --live   # also the regression evals against the real model (3 trials each)
#
# --live needs Node.js and app/lir-agent/.env with LLM_MODEL and LLM_API_KEY. It fails if any
# regression task fails the code graders in any trial; the LLM rubric never fails it.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

failed=()
step() {
    local name="$1" dir="$2"
    shift 2
    printf '\n\033[1m== %s\033[0m\n' "$name"
    if (cd "$ROOT/$dir" && "$@"); then
        printf '\033[32mOK\033[0m %s\n' "$name"
    else
        printf '\033[31mFAILED\033[0m %s\n' "$name"
        failed+=("$name")
    fi
}

for app in decision-layer lir-agent evals; do
    step "$app: install" "app/$app" uv sync --quiet
    step "$app: tests" "app/$app" uv run pytest -q
    step "$app: lint" "app/$app" uv run ruff check .
done
step "lir-agent: types" "app/lir-agent" uv run pyright

if ((LIVE)); then
    step "evals: regression gate (pass^3, real model)" "app/evals" uv run python run.py --gate --repeat 3 -j 4
fi

echo
if ((${#failed[@]})); then
    printf '\033[31m%d check(s) failed:\033[0m\n' "${#failed[@]}"
    printf '  - %s\n' "${failed[@]}"
    exit 1
fi
printf '\033[32mAll checks passed%s.\033[0m\n' "$( ((LIVE)) && echo ' (including the live regression gate)' || echo ' (offline; add --live for the regression evals)')"
