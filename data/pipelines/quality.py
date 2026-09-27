"""Scorecard de calidad por dimensión sobre staging/ (observabilidad + medición continua).

Lee contracts/quality_rules.yaml, evalúa cada regla y escribe:
  - manifests/quality/quality_<run>.json   (histórico, para observar tendencias)
  - reports/data_quality.md                (resumen legible, se regenera)

Uso:
    python -m pipelines.quality
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone

import duckdb
import yaml

from .paths import CONTRACTS, DATA_DIR, MANIFESTS, STAGING

REPORTS = DATA_DIR / "reports"


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for p in STAGING.glob("*.parquet"):
        con.execute(f"create view {p.stem} as select * from '{str(p).replace(chr(92), '/')}'")
    return con


def evaluate(con, rule: dict) -> dict:
    t0 = time.perf_counter()
    res = {k: rule.get(k) for k in ("id", "table", "dimension", "use", "why")}
    if "fail_when" in rule:
        # Materializa las filas que fallan una vez y cuenta; `t` = alias de la tabla.
        n, fails = con.execute(
            f"select count(*), count(*) filter (where coalesce(({rule['fail_when']}), false)) "
            f"from {rule['table']} t").fetchone()
        rate = fails / n if n else 0.0
        res.update(rows=n, failing=fails, fail_rate=round(rate, 6),
                   threshold=rule.get("max_fail_rate", 0), passed=rate <= rule.get("max_fail_rate", 0))
    else:
        v = con.execute(rule["metric"]).fetchone()[0]
        lo, hi = rule.get("min", float("-inf")), rule.get("max", float("inf"))
        res.update(value=round(float(v), 6), threshold={"min": rule.get("min"), "max": rule.get("max")},
                   passed=lo <= v <= hi)
    res["seconds"] = round(time.perf_counter() - t0, 2)
    return res


def run() -> dict:
    spec = yaml.safe_load(open(CONTRACTS / "quality_rules.yaml", encoding="utf-8"))
    con = _connect()
    results = []
    for rule in spec["rules"]:
        r = evaluate(con, rule)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        val = f"{r['fail_rate']:.2%} de {r['rows']:,}" if "fail_rate" in r else f"valor={r['value']}"
        print(f"[{mark}] {r['dimension']:13} {r['id']:36} {val}", flush=True)

    by_dim = defaultdict(lambda: [0, 0])
    for r in results:
        by_dim[r["dimension"]][0] += r["passed"]
        by_dim[r["dimension"]][1] += 1
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {"run_at": run_at, "summary": {d: {"passed": p, "total": n} for d, (p, n) in by_dim.items()},
           "data_products": spec["data_products"], "results": results}

    d = MANIFESTS / "quality"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"quality_{run_at[:19].replace(':', '')}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_report(out)
    return out


def _write_report(out: dict) -> None:
    REPORTS.mkdir(exist_ok=True)
    lines = [
        "# Scorecard de calidad de datos (staging)",
        "",
        f"Generado por `python -m pipelines.quality` — {out['run_at']}. Reglas en `contracts/quality_rules.yaml`.",
        "",
        "## Resumen por dimensión",
        "",
        "| Dimensión | Reglas OK | Total |",
        "|---|---|---|",
        *[f"| {d} | {s['passed']} | {s['total']} |" for d, s in sorted(out["summary"].items())],
        "",
        "## Reglas que fallan",
        "",
        "| Regla | Tabla | Dimensión | Uso | Resultado | Umbral | Por qué importa |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in out["results"]:
        if r["passed"]:
            continue
        val = f"{r['fail_rate']:.2%} ({r['failing']:,}/{r['rows']:,})" if "fail_rate" in r else f"{r['value']}"
        thr = f"≤ {r['threshold']:.0%}" if "fail_rate" in r else json.dumps(r["threshold"])
        lines.append(f"| `{r['id']}` | {r['table']} | {r['dimension']} | {r['use']} | {val} | {thr} | {r.get('why') or ''} |")
    lines += ["", "## Reglas que pasan", ""]
    lines += [f"- `{r['id']}` ({r['dimension']})" for r in out["results"] if r["passed"]]
    (REPORTS / "data_quality.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
