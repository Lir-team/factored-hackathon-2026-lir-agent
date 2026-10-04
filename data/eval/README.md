# eval/ (held-out)

Never tune prompts, keywords or models on these files (`data/AGENTS.md` rule 5). Cases are
added only when asked, and registered here.

| Set | What | Added |
|---|---|---|
| `decisions/seeds.yaml` | 320 customer messages (80 seeds x 4 paraphrases; ES MX/CO/AR, PT-BR, portuñol) labeled for the typed decisions D1 intent, D2 wants a person, D3 theft. Team-generated synthetic text. Split by seed into validation (thresholds only) and test (reported once). Evaluated by `app/evals/decision_eval` into `reports/decision_eval.md` | 2026-10-04, EVAL-01 |
| `decisions/second_labeling.csv` | Blind sample (40 messages) for a second annotator: fill `intent`, `human`, `theft` without looking at `seeds.yaml`; the report then computes the agreement (Cohen's kappa) | 2026-10-04, EVAL-01 |

`cases/` and `labels/` are reserved for end-to-end held-out scenarios.
