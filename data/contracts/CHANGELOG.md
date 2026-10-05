# Contracts changelog

The dataset declares *schema evolution*: any schema change detected in
`raw/` is recorded here before updating the corresponding YAML.

## 1.0.0 — 2026-09-26
- Initial contracts for the 13 tables, generated from
  `LATAM_Bank_Complete_Data_Dictionary.pdf` (dataset v1.0.0).
- `relationships.yaml` with the 24 documented FK relationships.

## 1.1.0 — 2026-09-26
- `glossary.yaml`: mappings from raw values (Spanish / variants) to canonical codes, and business definitions.
  Documents semantic drift from the dictionary: `Retención`, `Muy Positivo`, `Pasaporte`, `México`/`Mexico`.
- `quality_rules.yaml`: 48 rules across 7 dimensions, with a threshold per use (agent / analytics / ml) and owners per data product.
