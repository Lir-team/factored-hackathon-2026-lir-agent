"""Summarize a promptfoo results file: per scenario, per JTBD, per language, and Bases metrics.

    uv run python report.py out/results.json

Every rate travels with its counts, and a zero denominator is reported as "not defined".
A trial passes the code graders when outcome, safety, grounding and language all pass;
the LLM rubric ("calidad") is reported separately.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CODE_DIMENSIONS = ("outcome", "safety", "grounding", "language")
AUTOMATABLE = {"explain", "dispute"}
SHOULD_HAND_OFF = {"handoff"}


def load_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", data)
    rows = results.get("results", results) if isinstance(results, dict) else results
    out = []
    for r in rows:
        vars_ = r.get("vars") or (r.get("testCase") or {}).get("vars") or {}
        meta = (r.get("testCase") or {}).get("metadata") or r.get("metadata") or {}
        grading = r.get("gradingResult") or {}
        named = grading.get("namedScores") or r.get("namedScores") or {}
        trial = (r.get("response") or {}).get("metadata") or {}
        out.append({
            "id": vars_.get("id") or (r.get("testCase") or {}).get("description"),
            "jtbd": meta.get("jtbd") or [],
            "kind": meta.get("kind", "capability"),
            "lang": vars_.get("lang") or "-",
            "expected": (vars_.get("expect") or {}).get("outcome"),
            "code_pass": bool(trial) and all(named.get(d, 0) >= 1 for d in CODE_DIMENSIONS),
            "dims": {d: named.get(d) for d in (*CODE_DIMENSIONS, "efficiency", "calidad")},
            "rubric_pass": named.get("calidad", 0) >= 1 if "calidad" in named else None,
            "error": r.get("error") if not trial else None,
            "handoffs": len(trial.get("handoffs", [])),
            "disputes": len(trial.get("disputes", [])),
            "latency_ms": trial.get("latency_ms"),
            "cost": trial.get("cost_usd"),
            "reason": grading.get("reason", ""),
        })
    return out


def rate(n: int, of: int) -> str:
    return "not defined" if of == 0 else f"{n / of:.0%} ({n}/{of})"


def pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    rows = load_rows(Path(argv[0] if argv else "out/results.json"))
    if not rows:
        print("No results.")
        return
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_id[r["id"]].append(r)
    k = max(len(v) for v in by_id.values())

    print(f"\n=== Escenarios ({len(by_id)} tareas, {len(rows)} trials, k={k}) ===")
    print(f"{'escenario':40} {'tipo':11} {'lang':4} {'pass':>7} {'pass^k':>6} {'pass@k':>6} {'rubric':>7}  falla")
    for sid, trials in sorted(by_id.items()):
        n = sum(t["code_pass"] for t in trials)
        rub = [t["rubric_pass"] for t in trials if t["rubric_pass"] is not None]
        fail = next((t["error"] or t["reason"] for t in trials if not t["code_pass"]), "")
        print(f"{sid:40} {trials[0]['kind']:11} {trials[0]['lang']:4} {n}/{len(trials):<5} "
              f"{'✓' if n == len(trials) else '✗':>6} {'✓' if n else '✗':>6} "
              f"{(str(sum(rub)) + '/' + str(len(rub))) if rub else '-':>7}  {fail[:90]}")

    def group(title: str, key) -> None:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            for g in key(r):
                buckets[g].append(r)
        print(f"\n=== {title} ===")
        for g, rs in sorted(buckets.items()):
            print(f"  {g:12} {rate(sum(r['code_pass'] for r in rs), len(rs))}")

    group("Por tipo (regression debe estar en ~100%)", lambda r: [r["kind"]])
    group("Por JTBD", lambda r: r["jtbd"])
    group("Por idioma", lambda r: [r["lang"]])
    print("\n=== Por dimensión (trials que pasan cada grader) ===")
    for dim in (*CODE_DIMENSIONS, "efficiency", "calidad"):
        graded = [r["dims"][dim] for r in rows if r["dims"].get(dim) is not None]
        print(f"  {dim:12} {rate(sum(v >= 1 for v in graded), len(graded))}")

    ok = [r for r in rows if not r["error"]]
    auto = [r for r in ok if r["expected"] in AUTOMATABLE]
    resolved = [r for r in auto if r["code_pass"]]
    must_handoff = [r for r in ok if r["expected"] in SHOULD_HAND_OFF]
    other = [r for r in ok if r["expected"] not in SHOULD_HAND_OFF]
    unsafe = [r for r in ok if r["dims"].get("safety") == 0]
    lat = [r["latency_ms"] for r in ok if r["latency_ms"] is not None]
    cost = [r["cost"] or 0.0 for r in ok]
    print("\n=== Métricas de las Bases (sobre trials) ===")
    print(f"  resolución automática segura   {rate(len(resolved), len(auto))}  (casos donde se espera explicar o disputar)")
    print(f"  contención (sin derivar)       {rate(sum(r['handoffs'] == 0 for r in ok), len(ok))}  (no es éxito por sí sola)")
    print(f"  derivaciones perdidas          {rate(sum(r['handoffs'] == 0 for r in must_handoff), len(must_handoff))}")
    print(f"  derivaciones innecesarias      {rate(sum(r['handoffs'] > 0 for r in other), len(other))}")
    print(f"  resultados inseguros           {rate(len(unsafe), len(ok))}")
    if lat:
        print(f"  latencia por caso p50 / p95    {pct(lat, 0.5) / 1000:.1f} s / {pct(lat, 0.95) / 1000:.1f} s")
    print(f"  costo por caso intentado       US${statistics.mean(cost):.5f}" if cost else "")
    total = sum(r["cost"] or 0.0 for r in auto)
    print(f"  costo por resolución exitosa   {'not defined' if not resolved else f'US${total / len(resolved):.5f}'}")
    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"\n  {len(errors)} trials con error de infraestructura (excluidos de las métricas): {errors[0]['error'][:200]}")


if __name__ == "__main__":
    main()
