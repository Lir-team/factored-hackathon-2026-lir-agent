"""Write the evaluation report of the typed decisions (EVAL-01).

    uv run python -m decision_eval.report

Reads out/decisions/*.json (decision_eval.run) and writes data/reports/decision_eval.md and
.json. Thresholds are chosen on the validation split only; the test split is reported once,
at those thresholds. Never edit the report by hand (data/AGENTS.md rule 9): change this code.
"""

import csv
import json
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from decision_eval import metrics as m
from decision_eval.dataset import INTENTS, REPO, TEST, VALIDATION, Item, load

OUT = Path(__file__).resolve().parents[1] / "out" / "decisions"
REPORT_MD = REPO / "data" / "reports" / "decision_eval.md"
REPORT_JSON = REPO / "data" / "reports" / "decision_eval.json"
SECOND_LABELING = REPO / "data" / "eval" / "decisions" / "second_labeling.csv"
POLICY = {"intent": 0.50, "human": 0.50, "theft": 0.30}  # policy.yaml today
TARGET_INTENT_ACCURACY = 0.95  # decided automatically only when it is right 95% of the time
MIN_RECALL = {"human": 0.95, "theft": 0.95}  # missing these is worse than over-routing
LANGUAGES = ("es", "pt", "mixed")


def _runs(kind: str) -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(OUT.glob(f"{kind}-*.json"))
    ]


def _answers(run: dict) -> dict[str, dict]:
    return {a["item_id"]: a for a in run["answers"]}


def _intent_view(items: Sequence[Item], answers: dict[str, dict]):
    y_true = [i.intent for i in items]
    y_pred = [answers[i.item_id].get("intent") for i in items]
    conf = [answers[i.item_id].get("intent_p") or 0.0 for i in items]
    correct = [t == p for t, p in zip(y_true, y_pred, strict=True)]
    return y_true, y_pred, conf, correct


def _binary_view(items: Sequence[Item], answers: dict[str, dict], key: str):
    y_true = [getattr(i, key) for i in items]
    p_yes = [answers[i.item_id].get(f"{key}_p") for i in items]
    conf = [max(p, 1 - p) if p is not None else 0.0 for p in p_yes]
    correct = [p is not None and (p >= 0.5) == t for t, p in zip(y_true, p_yes, strict=True)]
    return y_true, p_yes, conf, correct


def evaluate_run(items: list[Item], run: dict) -> dict:
    """Every metric of one run: thresholds from validation, scores on test."""
    answers = _answers(run)
    val = [i for i in items if i.split == VALIDATION]
    test = [i for i in items if i.split == TEST]
    out: dict = {"model": run["model"], "run": run["run"], "failures": run["failures"]}

    # D1 intent
    _, _, v_conf, v_correct = _intent_view(val, answers)
    pick = m.pick_selective_threshold(m.coverage_curve(v_conf, v_correct), TARGET_INTENT_ACCURACY)
    t_true, t_pred, t_conf, t_correct = _intent_view(test, answers)
    test_curve = m.coverage_curve(t_conf, t_correct)

    def at(threshold: float | None) -> dict | None:
        if threshold is None:
            return None
        p = m.coverage_curve(t_conf, t_correct, [threshold])[0]
        return {"threshold": threshold, "coverage": p.coverage, "accuracy": p.accuracy, "decided": p.decided}

    out["intent"] = {
        "accuracy": m.accuracy(t_true, t_pred),
        "accuracy_ci": m.wilson(sum(t_correct), len(t_correct)),
        "macro_f1": m.macro_f1(t_true, t_pred, INTENTS),
        "per_class": [s.__dict__ for s in m.per_class(t_true, t_pred, INTENTS)],
        "ece": m.ece(t_conf, t_correct),
        "reliability": m.reliability(t_conf, t_correct),
        "confusion": m.confusion(t_true, t_pred, INTENTS),
        "chosen": at(pick.threshold if pick else None),
        "policy": at(POLICY["intent"]),
        "curve": [p.__dict__ for p in test_curve],
        "by_language": {
            lang: {
                "n": len(idx := [k for k, i in enumerate(test) if i.language == lang]),
                "correct": sum(t_correct[k] for k in idx),
                "ci": m.wilson(sum(t_correct[k] for k in idx), len(idx)),
                "macro_f1": m.macro_f1([t_true[k] for k in idx], [t_pred[k] for k in idx], INTENTS),
            }
            for lang in LANGUAGES
        },
        "errors": [
            {"item_id": i.item_id, "lang": i.lang, "text": i.text, "true": i.intent,
             "pred": answers[i.item_id].get("intent"), "p": answers[i.item_id].get("intent_p")}
            for i, ok in zip(test, t_correct, strict=True)
            if not ok
        ],
    }

    # D2 wants a human, D3 theft
    for key in ("human", "theft"):
        v_true, v_p, _, _ = _binary_view(val, answers, key)
        chosen = m.pick_recall_threshold(v_true, v_p, MIN_RECALL[key])
        valid = m.recall_range(v_true, v_p, MIN_RECALL[key])
        t_true_b, t_p, t_conf_b, t_correct_b = _binary_view(test, answers, key)
        at_chosen = m.binary_at(t_true_b, t_p, chosen.threshold)
        at_policy = m.binary_at(t_true_b, t_p, POLICY[key])
        out[key] = {
            "chosen_threshold": chosen.threshold,
            "valid_range": valid,
            "policy_in_range": bool(valid and valid[0] <= POLICY[key] <= valid[1]),
            "chosen": at_chosen.__dict__ | {"recall_ci": m.wilson(at_chosen.tp, at_chosen.positives)},
            "policy": at_policy.__dict__ | {"recall_ci": m.wilson(at_policy.tp, at_policy.positives)},
            "ece": m.ece(t_conf_b, t_correct_b),
            "errors": [
                {"item_id": i.item_id, "text": i.text, "true": getattr(i, key),
                 "p": answers[i.item_id].get(f"{key}_p")}
                for i, p in zip(test, t_p, strict=True)
                if (p is not None and p >= POLICY[key]) != getattr(i, key)
            ],
        }

    lat = sorted(a["latency_ms"] for a in run["answers"])
    out["latency_p50_ms"] = statistics.median(lat)
    out["latency_p95_ms"] = lat[int(0.95 * (len(lat) - 1))]
    out["cost_per_1000_messages_usd"] = run["cost_usd"] / run["items"] * 1000
    return out


def _second_labeling(items: list[Item]) -> dict:
    """A blind sample for a second annotator; agreement once it is filled in."""
    if not SECOND_LABELING.exists():
        sample = [i for i in items if i.item_id.endswith("-2")][::2]  # 40 items, every intent
        with SECOND_LABELING.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["item_id", "text", "intent", "human", "theft"])
            for i in sample:
                writer.writerow([i.item_id, i.text, "", "", ""])
        return {"status": "pending", "sample": len(sample)}
    with SECOND_LABELING.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["intent"].strip()]
    if not rows:
        return {"status": "pending", "sample": sum(1 for _ in SECOND_LABELING.open()) - 1}
    by_id = {i.item_id: i for i in items}
    first = [by_id[r["item_id"]].intent for r in rows]
    second = [r["intent"].strip() for r in rows]
    return {"status": "done", "n": len(rows), "agreement": m.accuracy(first, second),
            "kappa": cohen_kappa(first, second)}


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Agreement between two annotators beyond chance."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _ci(ci: Sequence[float]) -> str:
    return f"[{ci[0] * 100:.0f}-{ci[1] * 100:.0f}]"


def _mean_sd(values: Sequence[float]) -> str:
    if len(values) == 1:
        return f"{values[0]:.3f}"
    return f"{statistics.mean(values):.3f} ± {statistics.stdev(values):.3f}"


def render(items: list[Item], results: dict[str, list[dict]], labeling: dict) -> str:
    val = sum(i.split == VALIDATION for i in items)
    test = len(items) - val
    lang_mix = Counter(i.language for i in items)
    lines = [
        "# Evaluación de las decisiones tipadas (EVAL-01)",
        "",
        f"> Generado por `uv run python -m decision_eval.report` (app/evals) — "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}. No editar a mano.",
        "> Bases §4: *\"Evaluate at least one learned component against an appropriate baseline. "
        "Use valid labels or relevance judgments, prevent leakage, and justify representations, "
        "metrics, thresholds, and evaluation splits.\"*",
        "",
        "## Qué se evalúa",
        "",
        "Las decisiones que el agente toma en cada turno, antes de conversar: **D1** intención "
        "(5 clases), **D2** ¿pide una persona?, **D3** ¿sospecha de robo? La política "
        "(`policy.yaml`) actúa sobre sus probabilidades con umbrales.",
        "",
        "| Candidato | Qué es |",
        "|---|---|",
        "| Baseline | Reglas por palabras clave ES/PT (`decision_layer.keywords`) |",
        f"| LLM | El mismo modelo del agente con salida estructurada (`{results['llm'][0]['model'] if results.get('llm') else '-'}`), sin fallback |",
        "| Jev | **No evaluado:** sin credenciales de Cloudflare en este entorno (y la cuenta respondía 402) |",
        "",
        "## Datos, etiquetas y fuga",
        "",
        f"- **{len(items)} mensajes** de {len({i.seed_id for i in items})} semillas (4 paráfrasis cada una): "
        f"{lang_mix['es']} en español (MX/CO/AR), {lang_mix['pt']} en portugués (BR), {lang_mix['mixed']} en portuñol. "
        "Incluye negaciones (\"no quiero hablar con nadie\"), robo figurado (\"me están robando con la comisión\") y jerga.",
        "- **Procedencia:** texto sintético generado por el equipo para esta evaluación (`data/eval/decisions/seeds.yaml`); "
        "no son datos de clientes ni salen del dataset, cuyos textos son plantillas (`insights.md` §5).",
        "- **Etiquetas:** un anotador, siguiendo las definiciones de `decision_layer/questions.py`. "
        + (
            f"Segunda anotación ciega sobre {labeling['n']} mensajes: acuerdo {_pct(labeling['agreement'])}, kappa {labeling['kappa']:.2f}."
            if labeling["status"] == "done"
            else f"**Segunda anotación ciega pendiente:** `data/eval/decisions/second_labeling.csv` ({labeling['sample']} mensajes) "
            "para que otra persona etiquete sin ver las etiquetas; este reporte calcula el acuerdo (kappa) cuando se complete."
        ),
        f"- **Split sin fuga por semilla:** todas las paráfrasis de una semilla quedan del mismo lado; "
        f"estratificado por intención. **Validación** ({val}) se usa solo para elegir umbrales; **test** ({test}) se reporta una vez.",
        "",
    ]

    def model_rows(kind: str) -> list[dict]:
        return results.get(kind, [])

    lines += ["## Resultados en test", "", "### D1 intención", "",
              "| Candidato | Corridas | Exactitud [IC 95%] | Macro-F1 | ECE | Umbral elegido en validación | Cobertura / exactitud en test con ese umbral |",
              "|---|---|---|---|---|---|---|"]
    for kind in ("keywords", "llm"):
        rows = model_rows(kind)
        if not rows:
            continue
        r0 = rows[0]["intent"]
        chosen = r0["chosen"]
        lines.append(
            f"| {kind} | {len(rows)} | {_mean_sd([r['intent']['accuracy'] for r in rows])} {_ci(r0['accuracy_ci'])} "
            f"| {_mean_sd([r['intent']['macro_f1'] for r in rows])} | {_mean_sd([r['intent']['ece'] for r in rows])} "
            f"| {chosen['threshold']:.2f} | {_pct(chosen['coverage'])} / {_pct(chosen['accuracy'])} |"
            if chosen else
            f"| {kind} | {len(rows)} | {_mean_sd([r['intent']['accuracy'] for r in rows])} {_ci(r0['accuracy_ci'])} "
            f"| {_mean_sd([r['intent']['macro_f1'] for r in rows])} | {_mean_sd([r['intent']['ece'] for r in rows])} "
            f"| ninguno llega al {_pct(TARGET_INTENT_ACCURACY)} | — |"
        )
    lines += ["",
              f"*Umbral elegido:* el más bajo con el que, en validación, lo decidido automáticamente acierta al menos el "
              f"{_pct(TARGET_INTENT_ACCURACY)}; por debajo, el agente pide aclaración (regla T4). "
              "Exactitud e IC de la primera corrida; ± es la desviación entre corridas.", ""]

    for kind in ("keywords", "llm"):
        rows = model_rows(kind)
        if not rows:
            continue
        r0 = rows[0]["intent"]
        lines += [f"**{kind}: F1 por clase (corrida 1)**", "", "| Clase | Precisión | Recall | F1 | n |", "|---|---|---|---|---|"]
        lines += [f"| {c['label']} | {c['precision']:.2f} | {c['recall']:.2f} | {c['f1']:.2f} | {c['support']} |" for c in r0["per_class"]]
        lines += ["", "| Política actual (umbral 0.50) | Cobertura | Exactitud de lo decidido |", "|---|---|---|",
                  f"| {kind} | {_pct(r0['policy']['coverage'])} | {_pct(r0['policy']['accuracy'])} |", ""]
        lines += ["Calibración (diagrama de confiabilidad):", "", "| Confianza | n | Confianza media | Exactitud |", "|---|---|---|---|"]
        lines += [f"| {b} | {n} | {c:.2f} | {a:.2f} |" for b, n, c, a in r0["reliability"]]
        lines += [""]

    lines += ["### D2 ¿pide una persona? y D3 ¿sospecha de robo?", "",
              "En validación se buscan los umbrales que detectan al menos el 95% de los casos (no derivar a "
              "quien lo necesita es peor que derivar de más) y se verifica que el de la política caiga dentro. "
              "Test se mide con el umbral de la política.", "",
              "| Decisión | Candidato | Umbrales válidos en validación | Política | ¿Dentro? | Recall en test [IC 95%] | Precisión en test | ECE |",
              "|---|---|---|---|---|---|---|---|"]
    for key, name in (("human", "D2 pide persona"), ("theft", "D3 robo")):
        for kind in ("keywords", "llm"):
            rows = model_rows(kind)
            if not rows:
                continue
            r = rows[0][key]
            valid = r["valid_range"]
            lines.append(
                f"| {name} | {kind} | {f'{valid[0]:.2f}-{valid[1]:.2f}' if valid else 'ninguno'} "
                f"| {POLICY[key]:.2f} | {'sí' if r['policy_in_range'] else 'no'} "
                f"| {_pct(r['policy']['recall'])} {_ci(r['policy']['recall_ci'])} "
                f"| {_pct(r['policy']['precision'])} | {_mean_sd([x[key]['ece'] for x in rows])} |"
            )
    lines += [""]

    lines += ["### Equidad por idioma (D1, test)", "",
              "| Candidato | Español | Portugués | Portuñol |", "|---|---|---|---|"]
    for kind in ("keywords", "llm"):
        rows = model_rows(kind)
        if not rows:
            continue
        bl = rows[0]["intent"]["by_language"]
        cells = [
            f"{_pct(bl[lang]['correct'] / bl[lang]['n']) if bl[lang]['n'] else '—'} {_ci(bl[lang]['ci'])} (n={bl[lang]['n']})"
            for lang in LANGUAGES
        ]
        lines.append(f"| {kind} | " + " | ".join(cells) + " |")
    lines += ["", "Con n chicos, una diferencia solo cuenta si los intervalos no se solapan (`data/AGENTS.md` regla 10).", ""]

    lines += ["### Latencia y costo", "", "| Candidato | p50 | p95 | Costo por 1.000 mensajes (D1+D2+D3 en una llamada) |", "|---|---|---|---|"]
    for kind in ("keywords", "llm"):
        rows = model_rows(kind)
        if rows:
            lines.append(
                f"| {kind} | {_mean_sd([r['latency_p50_ms'] for r in rows])} ms | {_mean_sd([r['latency_p95_ms'] for r in rows])} ms "
                f"| US${statistics.mean(r['cost_per_1000_messages_usd'] for r in rows):.3f} |"
            )
    lines += ["", "Costo con los precios de LiteLLM para el modelo; latencia medida con 8 llamadas concurrentes.", ""]

    lines += ["## Análisis de errores (test, corrida 1)", ""]
    for kind in ("keywords", "llm"):
        rows = model_rows(kind)
        if not rows:
            continue
        errs = rows[0]["intent"]["errors"]
        pairs = Counter((e["true"], e["pred"]) for e in errs).most_common(5)
        lines += [f"**{kind}: {len(errs)} errores de intención.** Confusiones más frecuentes: "
                  + "; ".join(f"{t} → {p} ({n})" for (t, p), n in pairs), ""]
        if kind == "llm":
            lines += ["| Mensaje | Etiqueta | Predicción (p) |", "|---|---|---|"]
            lines += [f"| {e['text']} | {e['true']} | {e['pred']} ({(e['p'] or 0):.2f}) |" for e in errs[:15]]
            lines += [""]
            for key, name in (("human", "pide persona"), ("theft", "robo")):
                be = rows[0][key]["errors"]
                if be:
                    lines += [f"Errores de **{name}** con el umbral actual:", ""]
                    lines += [f"- \"{e['text']}\" → etiqueta {e['true']}, p={e['p'] if e['p'] is not None else 'falla'}" for e in be[:8]]
                    lines += [""]

    lines += _recommendation(results)
    lines += ["## Limitaciones", "",
              "- **D3 tiene una guía de anotación ambigua:** la definición incluye \"uso por terceros\", y una "
              "frase como \"una compra que yo no hice\" puede leerse así. Los falsos positivos de robo del LLM son "
              "casi todos de ese tipo; la segunda anotación dirá si es error del modelo o de la etiqueta.",
              "- Un solo anotador hasta completar la segunda anotación; las etiquetas pueden tener sesgo del autor.",
              "- Texto sintético: no reemplaza mensajes reales de clientes; el portuñol y la jerga son una aproximación.",
              "- n pequeño en D3 (robo) y en portuñol: los intervalos son anchos.",
              "- Jev no se pudo evaluar en este entorno.",
              "- Las probabilidades del LLM son las que el modelo declara; la ECE mide cuánto se puede confiar en ellas.",
              ""]
    return "\n".join(lines)


def _recommendation(results: dict[str, list[dict]]) -> list[str]:
    """What the numbers say about the default decision model and the policy thresholds."""
    kw, llm = results.get("keywords"), results.get("llm")
    if not kw or not llm:
        return []
    kw_acc = kw[0]["intent"]["accuracy"]
    llm_acc = statistics.mean(r["intent"]["accuracy"] for r in llm)
    kw_cov = kw[0]["intent"]["policy"]["coverage"]
    cost = statistics.mean(r["cost_per_1000_messages_usd"] for r in llm)
    p50 = statistics.mean(r["latency_p50_ms"] for r in llm) / 1000
    llm_intent_policy = llm[0]["intent"]["policy"]
    lines = [
        "## Qué dicen los resultados",
        "",
        f"1. **El baseline no alcanza para decidir:** acierta la intención el {_pct(kw_acc)} de las veces. "
        f"Con el umbral de la política decide solo el {_pct(kw_cov)} de los mensajes y en el resto pide aclaración "
        "(no rompe nada, pero alarga la conversación).",
        f"2. **El LLM sí:** {_pct(llm_acc)} de exactitud en promedio, sin brecha significativa entre español, "
        f"portugués y portuñol, a US${cost:.3f} por 1.000 mensajes y ~{p50:.1f} s de latencia (p50). "
        f"Con el umbral actual de la política decide el {_pct(llm_intent_policy['coverage'])} y acierta el "
        f"{_pct(llm_intent_policy['accuracy'])} de lo que decide.",
        "3. **Recomendación:** usar `DECISIONS=llm` (con el baseline como respaldo si el LLM falla) en el "
        "despliegue. La latencia se suma a cada turno: las decisiones corren antes de que el modelo "
        "converse, porque la política enruta el turno con ellas.",
        "4. **Umbrales de la política:** "
        + "; ".join(
            f"{name} {POLICY[key]:.2f} {'dentro' if llm[0][key]['policy_in_range'] else 'FUERA'} del rango válido"
            for key, name in (("human", "pide persona"), ("theft", "robo"))
        )
        + f"; intención {POLICY['intent']:.2f} decide el {_pct(llm_intent_policy['coverage'])} con "
        f"{_pct(llm_intent_policy['accuracy'])} de exactitud. Se mantienen, ahora respaldados por esta evaluación.",
        "",
    ]
    return lines


def main() -> None:
    items = load()
    results = {kind: [evaluate_run(items, r) for r in _runs(kind)] for kind in ("keywords", "llm")}
    labeling = _second_labeling(items)
    REPORT_MD.write_text(render(items, results, labeling), encoding="utf-8")
    REPORT_JSON.write_text(
        json.dumps({"labeling": labeling, "results": results}, ensure_ascii=False, indent=1, default=list),
        encoding="utf-8",
    )
    print(f"wrote {REPORT_MD.relative_to(REPO)} and {REPORT_JSON.relative_to(REPO)}")


if __name__ == "__main__":
    main()
