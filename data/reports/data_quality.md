# Scorecard de calidad de datos (staging)

Generado por `python -m pipelines.quality` — 2026-09-26T14:48:54+00:00. Reglas en `contracts/quality_rules.yaml`.

## Resumen por dimensión

| Dimensión | Reglas OK | Total |
|---|---|---|
| accuracy | 0 | 7 |
| completeness | 3 | 6 |
| consistency | 11 | 16 |
| integrity | 4 | 4 |
| timeliness | 4 | 4 |
| uniqueness | 2 | 3 |
| validity | 7 | 8 |

## Reglas que fallan

| Regla | Tabla | Dimensión | Uso | Resultado | Umbral | Por qué importa |
|---|---|---|---|---|---|---|
| `cus_adult_at_registration` | customers | validity | agent | 2.07% (3,106/150,000) | ≤ 0% |  |
| `cus_document_type_matches_country` | customers | consistency | agent | 49.94% (74,907/150,000) | ≤ 1% | DNI es argentino, CC/CE colombianos, CURP mexicano. Un documento incoherente invalida la verificación de identidad. |
| `prd_number_unique` | products | uniqueness | agent | 6.0 | {"min": null, "max": 0} |  |
| `prd_local_currency_mx` | products | accuracy | agent | 50.10% (200,398/400,000) | ≤ 50% | Ningún producto de un cliente mexicano está en MXN. Saldos en moneda inverosímil = respuesta incorrecta al cliente. |
| `prd_credit_limit_only_credit` | products | consistency | agent | 1.66% (6,655/400,000) | ≤ 1% |  |
| `int_contact_reason_granular` | call_center_interactions | accuracy | analytics | 100.00% (686,296/686,296) | ≤ 50% | contact_reason debería detallar el motivo, pero es una copia de reason_category: solo hay 6 motivos. |
| `int_duration_complete` | call_center_interactions | completeness | analytics | 14.02% (96,234/686,296) | ≤ 5% |  |
| `trn_no_placeholders` | call_transcripts | accuracy | ml | 100.00% (171,321/171,321) | ≤ 1% | Texto con {monto} {moneda} sin rellenar no es habla real. |
| `trn_text_diversity` | call_transcripts | accuracy | ml | 0.000245 | {"min": 0.1, "max": null} | Representatividad para IA: si pocas frases únicas se repiten miles de veces, el texto no representa la demanda real. |
| `trn_intent_label_informative` | call_transcripts | accuracy | ml | 100.00% (171,321/171,321) | ≤ 50% | Etiqueta candidata para el clasificador de intención. Si es constante, no sirve como label. |
| `trn_topic_not_copied` | call_transcripts | accuracy | ml | 100.00% (171,321/171,321) | ≤ 50% | Si main_topics es una copia de la categoría de la interacción, no fue extraído del texto. |
| `srv_nps_category_derivable` | satisfaction_surveys | consistency | analytics | 1.54% (3,274/212,759) | ≤ 0% |  |
| `cmp_origin_interaction_linked` | complaints | completeness | analytics | 50.32% (33,761/67,095) | ≤ 50% | Sin este vínculo no se puede trazar llamada -> queja (linaje del caso). |
| `cmp_product_belongs_to_customer` | complaints | consistency | agent | 66.43% (44,570/67,095) | ≤ 0% |  |
| `cmp_amount_currency_pair` | complaints | consistency | agent | 3.14% (2,105/67,095) | ≤ 0% |  |
| `cmp_resolved_has_date` | complaints | completeness | analytics | 1.15% (772/67,095) | ≤ 1% |  |
| `cmp_description_informative` | complaints | accuracy | ml | 7.5e-05 | {"min": 0.1, "max": null} | Descripción libre como fuente de intención/NLP. |

## Reglas que pasan

- `cus_pk_unique` (uniqueness)
- `cus_document_unique` (uniqueness)
- `cus_contactable` (completeness)
- `cus_credit_score_range` (validity)
- `cus_country_domain` (validity)
- `cus_country_matches_branch` (consistency)
- `cus_accent_matches_country` (consistency)
- `prd_customer_fk` (integrity)
- `prd_dpd_only_credit` (consistency)
- `trx_fk_product` (integrity)
- `trx_owner_matches_product` (consistency)
- `trx_currency_matches_product` (consistency)
- `trx_amount_positive` (validity)
- `trx_amount_usd_complete` (completeness)
- `trx_country_domain` (validity)
- `trx_late_arrival` (timeliness)
- `trx_fraud_score_range` (validity)
- `int_customer_fk` (integrity)
- `int_sentiment_label_matches_score` (consistency)
- `int_wait_complete` (completeness)
- `int_transcript_flag_consistent` (consistency)
- `int_accent_matches_customer` (consistency)
- `int_late_arrival` (timeliness)
- `trn_interaction_fk` (integrity)
- `trn_customer_matches_interaction` (consistency)
- `srv_score_range` (validity)
- `srv_after_interaction` (timeliness)
- `srv_customer_matches_interaction` (consistency)
- `cmp_resolution_after_creation` (validity)
- `cmp_resolution_days_consistent` (consistency)
- `cmp_late_arrival` (timeliness)
