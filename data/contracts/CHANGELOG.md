# Changelog de contratos

El dataset declara *schema evolution*: cualquier cambio de esquema detectado en
`raw/` se registra aquí antes de actualizar el YAML correspondiente.

## 1.0.0 — 2026-09-26
- Contratos iniciales de las 13 tablas generados desde
  `LATAM_Bank_Complete_Data_Dictionary.pdf` (dataset v1.0.0).
- `relationships.yaml` con las 24 relaciones FK documentadas.

## 1.1.0 — 2026-09-26
- `glossary.yaml`: mapeos de valores crudos (español / variantes) a códigos canónicos y definiciones de negocio.
  Documenta drift semántico respecto del diccionario: `Retención`, `Muy Positivo`, `Pasaporte`, `México`/`Mexico`.
- `quality_rules.yaml`: 48 reglas en 7 dimensiones, con umbral por uso (agent / analytics / ml) y owners por data product.
