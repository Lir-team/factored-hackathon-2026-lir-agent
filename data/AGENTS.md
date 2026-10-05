# Rules for agents (Claude Code, Codex, etc.) inside data/

1. **`raw/` is read-only.** Never modify, rename or delete files there.
2. **Develop on `samples/`**, not on the full tables (transactions: 5M rows, digital_events: 10M). Use the full tables only in explicit pipelines.
3. **Do not send raw rows to external model APIs.** Even though the dataset is synthetic, treat it as customer data: aggregate, anonymize or use minimal samples.
4. **Do not print or write credentials** (AWS keys, `.env`). Do not read the dictionary PDF to extract credentials.
5. **Do not touch `eval/`** to tune prompts or models: it is the held-out set. Add cases only when asked and record them in `eval/README.md`.
6. **Validate against `contracts/`** before writing to `staging/` or `curated/`. If the schema does not match, report it; do not silently "fix" the contract.
7. **Everything derived is regenerated with code.** Do not hand-edit files in `staging/`, `curated/` or `samples/`.
8. Each new derived dataset records its lineage in `manifests/`.
9. **`reports/` is generated with code** (`python -m pipelines`). Do not edit the .md files by hand: change the generator.
10. Before claiming a difference between groups, report n and significance (see `_spread` in `pipelines/insights.py`).
