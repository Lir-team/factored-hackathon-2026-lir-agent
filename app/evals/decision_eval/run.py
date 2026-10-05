"""Run a decision model over the labeled set and keep every answer (EVAL-01).

    uv run python -m decision_eval.run --model keywords
    uv run python -m decision_eval.run --model llm --repeat 3

Results go to data/reports/decision_eval_runs/<model>-<run>.json (versioned: the report is
recomputed from them). The model sees only the
customer's text, as in production; the labels never leave this process.
"""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from decision_layer import DecisionError, DecisionModel, KeywordDecisionModel
from decision_layer.questions import TURN_QUESTIONS
from dotenv import load_dotenv

from decision_eval.dataset import Item, load

# Versioned next to the report, so every number in it can be recomputed (decision_eval.report).
OUT = Path(__file__).resolve().parents[3] / "data" / "reports" / "decision_eval_runs"
AGENT_ENV = Path(__file__).resolve().parents[2] / "lir-agent" / ".env"


def _llm() -> tuple[DecisionModel, dict]:
    """The agent's LLM as a decision model, alone (no fallback), with metered cost."""
    import litellm
    from lir_agent.config.settings import Settings
    from lir_agent.infrastructure.decisions import LlmDecisionModel
    from lir_agent.infrastructure.llm import litellm_cost

    load_dotenv(AGENT_ENV)
    settings = Settings()
    meter = {"cost_usd": 0.0, "unpriced": 0}
    model_name = settings.decision_llm_model or settings.llm_model

    def metered(**kwargs):
        response = litellm.completion(**kwargs)
        usage = getattr(response, "usage", None)
        cost = litellm_cost(
            model_name,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )
        if cost is None:
            meter["unpriced"] += 1
        else:
            meter["cost_usd"] += cost
        return response

    key = settings.decision_llm_api_key or settings.llm_api_key
    model = LlmDecisionModel(
        model_name,
        api_key=key.get_secret_value() if key else None,
        api_base=settings.llm_api_base or None,
        reasoning_effort=settings.decision_llm_reasoning_effort,
        completion=metered,
    )
    return model, meter


def _decide(model: DecisionModel, item: Item) -> dict:
    started = time.perf_counter()
    try:
        result = model.decide(item.text, TURN_QUESTIONS)
    except DecisionError as error:
        return {"item_id": item.item_id, "error": str(error)[:200],
                "latency_ms": (time.perf_counter() - started) * 1000}
    a = result.answers
    return {
        "item_id": item.item_id,
        "intent": a["intencion"].value,
        "intent_p": a["intencion"].probability,
        "intent_probs": dict(a["intencion"].probabilities or {}),
        # Yes/no answers carry the probability of "yes" in every model.
        "human_p": a["pide_humano"].probability,
        "theft_p": a["sospecha_robo"].probability,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "input_tokens": result.input_tokens,
    }


def run(model_kind: str, repeat: int, workers: int) -> list[Path]:
    items = load()
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for n in range(1, repeat + 1):
        if model_kind == "keywords":
            model, meter = KeywordDecisionModel(), {"cost_usd": 0.0, "unpriced": 0}
        else:
            model, meter = _llm()
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            answers = list(pool.map(partial(_decide, model), items))
        path = OUT / f"{model_kind}-{n}.json"
        latencies = [a["latency_ms"] for a in answers]
        path.write_text(
            json.dumps(
                {
                    "model": model.name,
                    "run": n,
                    "items": len(items),
                    "failures": sum("error" in a for a in answers),
                    "cost_usd": round(meter["cost_usd"], 6),
                    "unpriced_calls": meter["unpriced"],
                    "wall_s": round(time.perf_counter() - started, 1),
                    "latency_p50_ms": statistics.median(latencies),
                    "answers": answers,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        written.append(path)
        print(f"{path.name}: {len(items)} items, {sum('error' in a for a in answers)} failures, "
              f"US${meter['cost_usd']:.4f}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["keywords", "llm"], required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    run(args.model, args.repeat, args.workers)


if __name__ == "__main__":
    main()
