"""Write the evaluation report of the typed decisions (EVAL-01).

    uv run python -m decision_eval.report

Reads data/reports/decision_eval_runs/*.json (decision_eval.run) and writes
data/reports/decision_eval.md. Thresholds are chosen on the validation split only; the test split is reported once,
at those thresholds. Never edit the report by hand (data/AGENTS.md rule 9): change this code.
"""

import csv
import json
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml

from decision_eval import metrics as m
from decision_eval.dataset import INTENTS, REPO, TEST, VALIDATION, Item, load
from decision_eval.run import OUT

REPORT_MD = REPO / "data" / "reports" / "decision_eval.md"
SECOND_LABELING = REPO / "data" / "eval" / "decisions" / "second_labeling.csv"
POLICY_PATH = REPO / "app" / "lir-agent" / "src" / "lir_agent" / "resources" / "policy.yaml"


def policy_thresholds(path: Path = POLICY_PATH) -> dict[str, float]:
    """The thresholds the agent runs with, read from policy.yaml (never copied by hand)."""
    rules = {r["id"]: r["when"] for r in yaml.safe_load(path.read_text(encoding="utf-8"))["turn_rules"]}
    return {
        "intent": rules["T4_uncertain_intent"]["intent_p"]["lt"],
        "human": rules["T1_wants_human"]["wants_human_p"]["gte"],
        "theft": rules["T3_theft_suspected"]["theft_suspected_p"]["gte"],
    }


POLICY = policy_thresholds()
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


def _clusters(items: Sequence[Item], flags: Sequence[bool]) -> list[list[bool]]:
    """Flags grouped by seed: the unit the intervals resample (paraphrases are correlated)."""
    groups: dict[str, list[bool]] = {}
    for item, flag in zip(items, flags, strict=True):
        groups.setdefault(item.seed_id, []).append(flag)
    return list(groups.values())


def _present(y_true: Sequence[str], y_pred: Sequence[str | None]) -> list[str]:
    """The classes that occur in a subset: an absent class must not count as F1 = 0."""
    seen = set(y_true) | {p for p in y_pred if p}
    return [label for label in INTENTS if label in seen]


def _recall_ci(items: Sequence[Item], key: str, p_yes: Sequence[float | None], threshold: float):
    positives = [(i, p) for i, p in zip(items, p_yes, strict=True) if getattr(i, key)]
    hits = [p is not None and p >= threshold for _, p in positives]
    return m.cluster_interval(_clusters([i for i, _ in positives], hits))


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
        "accuracy_ci": m.cluster_interval(_clusters(test, t_correct)),
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
                "ci": m.cluster_interval(
                    _clusters([test[k] for k in idx], [t_correct[k] for k in idx])
                ),
                "macro_f1": m.macro_f1(
                    yt := [t_true[k] for k in idx],
                    yp := [t_pred[k] for k in idx],
                    _present(yt, yp),
                ),
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
            "chosen": at_chosen.__dict__
            | {"recall_ci": _recall_ci(test, key, t_p, chosen.threshold)},
            "policy": at_policy.__dict__ | {"recall_ci": _recall_ci(test, key, t_p, POLICY[key])},
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
        rows = list(csv.DictReader(f))
    by_id = {i.item_id: i for i in items}
    agreement = {}
    for key in ("intent", "human", "theft"):  # D3 is the ambiguous one: it matters most
        done = [r for r in rows if r[key].strip()]
        if not done:
            continue
        first = [str(getattr(by_id[r["item_id"]], key)).lower() for r in done]
        second = [r[key].strip().lower() for r in done]
        agreement[key] = {
            "n": len(done),
            "agreement": m.accuracy(first, second),
            "kappa": cohen_kappa(first, second),
        }
    if not agreement:
        return {"status": "pending", "sample": len(rows)}
    return {"status": "done", "by_question": agreement}


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


# Candidates in report order; a candidate without runs is left out.
KINDS = ("keywords", "llm", "jev")


def render(items: list[Item], results: dict[str, list[dict]], labeling: dict) -> str:
    val = sum(i.split == VALIDATION for i in items)
    test = len(items) - val
    lang_mix = Counter(i.language for i in items)
    lines = [
        "# Typed decision evaluation (EVAL-01)",
        "",
        f"> Generated by `uv run python -m decision_eval.report` (app/evals) on "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}. Do not edit by hand.",
        "> Bases §4: *\"Evaluate at least one learned component against an appropriate baseline. "
        "Use valid labels or relevance judgments, prevent leakage, and justify representations, "
        "metrics, thresholds, and evaluation splits.\"*",
        "",
        "## What is evaluated",
        "",
        "The decisions the agent takes on every turn, before it converses: **D1** intent "
        "(5 classes), **D2** does the customer want a person?, **D3** is theft suspected? The policy "
        "(`policy.yaml`) acts on their probabilities through thresholds.",
        "",
        "| Candidate | What it is |",
        "|---|---|",
        "| Baseline | ES/PT keyword rules (`decision_layer.keywords`) |",
        f"| LLM | The agent's model with structured output (`{results['llm'][0]['model'] if results.get('llm') else '-'}`), no fallback |",
        (
            f"| Jev | TypeSafe AI through OpenRouter (`{results['jev'][0]['model']}`), with the same structured-output adapter as the LLM, no fallback |"
            if results.get("jev")
            else "| Jev | **Not evaluated:** no credentials in this environment |"
        ),
        "",
        "## Data, labels and leakage",
        "",
        f"- **{len(items)} messages** from {len({i.seed_id for i in items})} seeds (4 paraphrases each): "
        f"{lang_mix['es']} in Spanish (MX/CO/AR), {lang_mix['pt']} in Portuguese (BR), {lang_mix['mixed']} in portuñol (mixed). "
        "It includes negations (\"no quiero hablar con nadie\"), figurative theft (\"me están robando con la comisión\") and slang.",
        "- **Provenance:** synthetic text written by the team for this evaluation (`data/eval/decisions/seeds.yaml`); "
        "it is not customer data and does not come from the dataset, whose texts are templates (`insights.md` §5).",
        "- **Labels:** one annotator, following the definitions in `decision_layer/questions.py`. "
        + (
            "Blind second annotation: "
            + "; ".join(
                f"{key} agreement {_pct(a['agreement'])}, kappa {a['kappa']:.2f} (n={a['n']})"
                for key, a in labeling["by_question"].items()
            )
            + "."
            if labeling["status"] == "done"
            else f"**Blind second annotation pending:** `data/eval/decisions/second_labeling.csv` ({labeling['sample']} messages) "
            "for another person to label without seeing the labels; this report computes the agreement (kappa) once it is done."
        ),
        f"- **Split by seed, no leakage:** all paraphrases of a seed stay on the same side; "
        f"stratified by intent. **Validation** ({val}) is used only to choose thresholds; **test** ({test}) is reported once.",
        "",
    ]

    def model_rows(kind: str) -> list[dict]:
        return results.get(kind, [])

    lines += ["## Test results", "", "### D1 intent", "",
              "| Candidate | Runs | Accuracy [95% CI] | Macro-F1 | ECE | Threshold chosen on validation | Test coverage / accuracy at that threshold |",
              "|---|---|---|---|---|---|---|"]
    for kind in KINDS:
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
            f"| none reaches {_pct(TARGET_INTENT_ACCURACY)} | — |"
        )
    lines += ["",
              f"*Chosen threshold:* the lowest one at which, on validation, what is decided automatically is right at least "
              f"{_pct(TARGET_INTENT_ACCURACY)} of the time; below it, the agent asks for clarification (rule T4). "
              "Accuracy of the first run; ± is the deviation across runs. The 95% CIs are Wilson intervals "
              "with **one observation per seed**, not per message: the 4 paraphrases of a seed are not "
              "independent, so the interval is conservative.", ""]

    for kind in KINDS:
        rows = model_rows(kind)
        if not rows:
            continue
        r0 = rows[0]["intent"]
        lines += [f"**{kind}: F1 per class (run 1)**", "", "| Class | Precision | Recall | F1 | n |", "|---|---|---|---|---|"]
        lines += [f"| {c['label']} | {c['precision']:.2f} | {c['recall']:.2f} | {c['f1']:.2f} | {c['support']} |" for c in r0["per_class"]]
        lines += ["", f"| Current policy (threshold {POLICY['intent']:.2f}) | Coverage | Accuracy of what is decided |", "|---|---|---|",
                  f"| {kind} | {_pct(r0['policy']['coverage'])} | {_pct(r0['policy']['accuracy'])} |", ""]
        lines += ["Calibration (reliability diagram):", "", "| Confidence | n | Mean confidence | Accuracy |", "|---|---|---|---|"]
        lines += [f"| {b} | {n} | {c:.2f} | {a:.2f} |" for b, n, c, a in r0["reliability"]]
        lines += [""]

    lines += ["### D2 wants a person, and D3 theft suspected", "",
              "On validation we look for the thresholds that catch at least 95% of the cases (failing to hand off "
              "someone who needs it is worse than handing off too often) and check that the policy's threshold falls inside. "
              "Test is measured with the policy's threshold.", "",
              "| Decision | Candidate | Valid thresholds on validation | Policy | Inside? | Test recall [95% CI] | Test precision | ECE |",
              "|---|---|---|---|---|---|---|---|"]
    for key, name in (("human", "D2 wants a person"), ("theft", "D3 theft")):
        for kind in KINDS:
            rows = model_rows(kind)
            if not rows:
                continue
            r = rows[0][key]
            valid = r["valid_range"]
            lines.append(
                f"| {name} | {kind} | {f'{valid[0]:.2f}-{valid[1]:.2f}' if valid else 'none'} "
                f"| {POLICY[key]:.2f} | {'yes' if r['policy_in_range'] else 'no'} "
                f"| {_pct(r['policy']['recall'])} {_ci(r['policy']['recall_ci'])} "
                f"| {_pct(r['policy']['precision'])} | {_mean_sd([x[key]['ece'] for x in rows])} |"
            )
    lines += [""]

    lines += ["### Fairness by language (D1, test)", "",
              "| Candidate | Spanish | Portuguese | Portuñol |", "|---|---|---|---|"]
    for kind in KINDS:
        rows = model_rows(kind)
        if not rows:
            continue
        bl = rows[0]["intent"]["by_language"]
        cells = [
            f"{_pct(bl[lang]['correct'] / bl[lang]['n']) if bl[lang]['n'] else '—'} {_ci(bl[lang]['ci'])} (n={bl[lang]['n']})"
            for lang in LANGUAGES
        ]
        lines.append(f"| {kind} | " + " | ".join(cells) + " |")
    lines += ["", "A difference only counts if the intervals (per seed) do not overlap "
              "(`data/AGENTS.md` rule 10).", ""]

    lines += ["### Latency and cost", "", "| Candidate | p50 | p95 | Cost per 1,000 messages (D1+D2+D3 in one call) |", "|---|---|---|---|"]
    for kind in KINDS:
        rows = model_rows(kind)
        if rows:
            cost = statistics.mean(r["cost_per_1000_messages_usd"] for r in rows)
            lines.append(
                f"| {kind} | {_mean_sd([r['latency_p50_ms'] for r in rows])} ms | {_mean_sd([r['latency_p95_ms'] for r in rows])} ms "
                f"| {f'US${cost:.3f}' if cost or kind == 'keywords' else 'no price in LiteLLM'} |"
            )
    lines += ["", "Cost from LiteLLM's prices for the model; latency measured with 8 concurrent calls.", ""]

    lines += ["## Error analysis (test, run 1)", ""]
    for kind in KINDS:
        rows = model_rows(kind)
        if not rows:
            continue
        errs = rows[0]["intent"]["errors"]
        pairs = Counter((e["true"], e["pred"]) for e in errs).most_common(5)
        lines += [f"**{kind}: {len(errs)} intent errors.** Most frequent confusions: "
                  + "; ".join(f"{t} → {p} ({n})" for (t, p), n in pairs), ""]
        if kind == "llm":
            lines += ["| Message | Label | Prediction (p) |", "|---|---|---|"]
            lines += [f"| {e['text']} | {e['true']} | {e['pred']} ({(e['p'] or 0):.2f}) |" for e in errs[:15]]
            lines += [""]
            for key, name in (("human", "wants a person"), ("theft", "theft")):
                be = rows[0][key]["errors"]
                if be:
                    lines += [f"**{name}** errors at the current threshold:", ""]
                    lines += [f"- \"{e['text']}\" → label {e['true']}, p={e['p'] if e['p'] is not None else 'failed'}" for e in be[:8]]
                    lines += [""]

    lines += _recommendation(results)
    lines += ["## Limitations", "",
              "- **The D3 guideline allows two readings:** it includes \"use by third parties\", and a phrase like "
              "\"una compra que yo no hice\" can be read that way. When reviewing the theft errors listed above, the "
              "second annotation (column `theft`) will tell whether they are model errors or label errors.",
              "- One annotator until the second annotation is done; the labels may carry the author's bias.",
              "- Synthetic text: it does not replace real customer messages; portuñol and slang are an approximation.",
              "- Small n for D3 (theft) and portuñol: the intervals are wide.",
              "- Jev is evaluated through OpenRouter, not Cloudflare Workers AI; its cost does not show because LiteLLM has no price for it.",
              "- The LLM's probabilities are the ones the model states; the ECE measures how far they can be trusted.",
              ""]
    return "\n".join(lines)


def _recommendation(results: dict[str, list[dict]]) -> list[str]:
    """What the numbers say about the default decision model and the policy thresholds."""
    kw, llm = results.get("keywords"), results.get("llm")
    if not kw or not llm:
        return []
    kw0, llm0 = kw[0]["intent"], llm[0]["intent"]
    llm_acc = statistics.mean(r["intent"]["accuracy"] for r in llm)
    cost = statistics.mean(r["cost_per_1000_messages_usd"] for r in llm)
    p50 = statistics.mean(r["latency_p50_ms"] for r in llm) / 1000
    llm_better = llm0["accuracy_ci"][0] > kw0["accuracy_ci"][1]  # intervals apart
    thresholds_ok = [
        (name, POLICY[key], llm[0][key]["policy_in_range"])
        for key, name in (("human", "wants a person"), ("theft", "theft"))
    ]
    intent_ok = llm0["policy"]["accuracy"] >= TARGET_INTENT_ACCURACY
    lines = [
        "## What the results say",
        "",
        f"1. **Baseline:** gets the intent right {_pct(kw0['accuracy'])} of the time {_ci(kw0['accuracy_ci'])}; at the "
        f"policy threshold it decides {_pct(kw0['policy']['coverage'])} of the messages and asks for "
        f"clarification on the rest. Across languages: {_language_gap(kw0['by_language'])}.",
        f"2. **LLM:** {_pct(llm_acc)} average accuracy {_ci(llm0['accuracy_ci'])}, at "
        f"US${cost:.3f} per 1,000 messages and ~{p50:.1f} s latency (p50); at the policy threshold it "
        f"decides {_pct(llm0['policy']['coverage'])} and is right {_pct(llm0['policy']['accuracy'])} of the time. "
        f"Across languages: {_language_gap(llm0['by_language'])}.",
        (
            "3. **Recommendation:** use `DECISIONS=llm` (with the baseline as the fallback if the model fails): "
            "its accuracy beats the baseline with intervals that do not overlap. The latency adds to "
            "every turn, because the policy routes the turn with these decisions before the model converses."
            if llm_better
            else "3. **Recommendation:** the LLM and baseline intervals overlap: with this data there is no "
            "basis to change the default decision model."
        ),
        "4. **Policy thresholds:** "
        + "; ".join(f"{name} {t:.2f} {'inside' if ok else 'OUTSIDE'} the valid range" for name, t, ok in thresholds_ok)
        + f"; intent {POLICY['intent']:.2f} is right on {_pct(llm0['policy']['accuracy'])} of what it decides. "
        + (
            "With the LLM, the current thresholds are backed by this evaluation."
            if intent_ok and all(ok for _, _, ok in thresholds_ok)
            else "Some thresholds need review: the ones marked OUTSIDE, or intent below "
            f"{_pct(TARGET_INTENT_ACCURACY)} accuracy."
        ),
        "",
    ]
    jev = results.get("jev")
    if jev:
        jev0 = jev[0]["intent"]
        jev_acc = statistics.mean(r["intent"]["accuracy"] for r in jev)
        jev_p50 = statistics.mean(r["latency_p50_ms"] for r in jev) / 1000
        apart_from_llm = (
            jev0["accuracy_ci"][1] < llm0["accuracy_ci"][0]
            or llm0["accuracy_ci"][1] < jev0["accuracy_ci"][0]
        )
        lines[-1:-1] = [
            f"5. **Jev:** {_pct(jev_acc)} average accuracy {_ci(jev0['accuracy_ci'])}, ~{jev_p50:.1f} s "
            f"(p50); at the policy threshold it decides {_pct(jev0['policy']['coverage'])} and is right "
            f"{_pct(jev0['policy']['accuracy'])} of the time. Against the LLM: "
            + (
                "the intervals do not overlap, so the difference is significant."
                if apart_from_llm
                else "the intervals overlap, so with this data they are equivalent."
            )
            + " Policy thresholds with Jev: "
            + "; ".join(
                f"{name} {'inside' if jev[0][key]['policy_in_range'] else 'OUTSIDE'}"
                for key, name in (("human", "pide persona"), ("theft", "robo"))
            )
            + ".",
        ]
    return lines


def _language_gap(by_language: dict) -> str:
    """Whether any two languages differ significantly (their intervals do not overlap)."""
    names = {"es": "Spanish", "pt": "Portuguese", "mixed": "portuñol"}
    measured = [(lang, v["ci"]) for lang, v in by_language.items() if v["n"]]
    gaps = [
        f"{names[a]} vs {names[b]}"
        for i, (a, ca) in enumerate(measured)
        for b, cb in measured[i + 1:]
        if ca[1] < cb[0] or cb[1] < ca[0]
    ]
    return ("significant difference in " + ", ".join(gaps)) if gaps else "no significant difference"


def main() -> None:
    items = load()
    results = {kind: [evaluate_run(items, r) for r in _runs(kind)] for kind in KINDS}
    labeling = _second_labeling(items)
    REPORT_MD.write_text(render(items, results, labeling), encoding="utf-8")
    print(f"wrote {REPORT_MD.relative_to(REPO)}")


if __name__ == "__main__":
    main()
