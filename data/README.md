# data/

Espacio de datos del proyecto. Sigue un flujo por capas (raw → staging → curated):
cada capa se regenera desde la anterior con código versionado, nunca a mano.

```
data/
├── raw/             # Copia inmutable de S3 (bronze). Nunca se edita.       [gitignored]
├── staging/         # Tipado, deduplicado, validado contra contratos (silver) [gitignored]
├── curated/         # Tablas analíticas y features listas para modelar (gold) [gitignored]
├── samples/         # Muestras chicas y deterministas para desarrollo/agentes [gitignored]
├── contracts/       # Esquemas YAML por tabla + relaciones FK + changelog     [versionado]
├── manifests/       # Linaje: qué se descargó/procesó, cuándo, checksums      [versionado]
├── knowledge_base/  # Políticas bancarias sintéticas para grounding (RAG)     [versionado]
├── eval/            # Casos held-out y etiquetas para evaluar el sistema      [versionado]
└── fixtures/        # Fixtures chicos para tests (late arrivals, dups, etc.)  [versionado]
```

Código Python y salidas:

```
├── pipelines/       # Paquete Python del pipeline (ver abajo)
├── reports/         # Reportes generados: data_quality.md, insights.md          [versionado]
├── notebooks/       # Exploración. Lógica reutilizable → mover a pipelines/
└── requirements.txt
```

## Cómo correrlo

```bash
cd data
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env                                     # completar credenciales S3 (ver diccionario de datos)

python -m pipelines --ingest    # todo: ingest -> staging -> quality -> curated -> insights (~4 min)
python -m pipelines             # sin descargar (~1.5 min)
```

| Paso | Módulo | Entrada → salida | Principio aplicado |
|---|---|---|---|
| Ingesta | `pipelines/ingest.py` | S3 → `raw/` + `manifests/ingest/` | Incremental por ETag (late arrivals), linaje |
| Staging | `pipelines/staging.py` | `raw/` → `staging/` + `manifests/staging/` | Contratos (tipos), glosario (semántica), dedupe por PK, sin descartes silenciosos |
| Calidad | `pipelines/quality.py` | `staging/` → `reports/data_quality.md` + `manifests/quality/` | 7 dimensiones, umbrales por uso (fitness for use), observabilidad histórica |
| Curated | `pipelines/curated.py` | `staging/` → `curated/` + `manifests/curated/` | Datos como producto, linaje de entradas |
| Evidencia | `pipelines/insights.py` + `figures.py` | `curated/` → `reports/insights.md` + `reports/figures/` | Números y gráficos calculados en código, significancia antes de afirmar disparidades, medido vs inferido separado |

Contratos y semántica en `contracts/`: `<tabla>.yaml` (esquema), `relationships.yaml` (FKs),
`glossary.yaml` (mapeos de valores + definiciones de negocio), `quality_rules.yaml` (reglas, umbrales, owners).

## Capas

| Capa | Contenido | Formato | Regla |
|---|---|---|---|
| `raw/` | Archivos tal cual vienen de S3, misma estructura de particiones | CSV original | Solo escritura por el script de ingesta. Inmutable. |
| `staging/` | Una tabla por fuente: tipos según contrato, valores canonicalizados, dedupe por PK, `_source_file` por fila | Parquet | Idempotente: re-ejecutar produce el mismo resultado. |
| `curated/` | Joins, agregados, features y datasets de entrenamiento con split temporal | Parquet | Cada tabla documenta sus entradas en `manifests/`. |
| `samples/` | Subconjuntos por `customer_id` (seed fija) coherentes entre tablas | Parquet/CSV | Lo que usan notebooks rápidos y agentes en desarrollo. |

## Origen de los datos

Requisito del desafío: identificar qué es real, sintético o generado por el equipo.

| Carpeta | Origen |
|---|---|
| `raw/`, `staging/`, `curated/`, `samples/` | **Sintético**: provisto por los organizadores (LATAM Bank v1.0.0) |
| `knowledge_base/` | **Generado por el equipo** (políticas sintéticas, no reales) |
| `eval/`, `fixtures/` | **Generado por el equipo** (incluye casos en portugués, que el dataset no tiene) |

## Calidad de datos: declarada vs observada

El proveedor declara problemas que en v1.0.0 **no aparecen**, y hay otros que no declara.
Detalle y cifras en `reports/data_quality.md`.

| Declarado por el proveedor | Observado |
|---|---|
| ~2% duplicados | 0 duplicados por PK, por clave de negocio ni casi-duplicados. 6 `product_number` repetidos |
| ~5% nulos | Sí, además de nulos estructurales (p. ej. `credit_limit` en productos no crediticios) |
| Llegadas tardías | No: `process_date` siempre es el día del evento o el anterior |
| Evolución de esquema | No: una sola cabecera por tabla en todas las particiones. Sí hay **drift semántico**: valores en español y no documentados (`Retención`, `Muy Positivo`) |
| FKs huérfanas | 0 en todas las relaciones |
| Filas: 800k interacciones, 80k quejas, 5M transacciones | ~14% menos: 686k / 67k / 4.4M |

No declarados y relevantes para el agente:
- Transcripciones y descripciones de quejas son **plantillas** (42 frases de cliente en 171k transcripciones, 5 descripciones en 67k quejas) y no se relacionan con la categoría.
- `detected_intents` es constante (`consulta_general`). **No sirve como etiqueta.**
- Clientes de México: 100% con DNI y 0 productos en MXN.
- `complaints.affected_product_id` nunca pertenece al cliente que reclama; `origin_interaction_id` siempre está vacío.

## Política de frescura y actualización

- Los datos son estáticos (snapshot 2023-06-17 → 2026-06-17). La ingesta es **batch incremental por partición**.
- Cada paso escribe un manifiesto en `manifests/<paso>/` (fuente, fecha, filas de entrada y salida, fallas de casteo, valores sin mapear y versión de contrato).
- La corrección de las actualizaciones (late arrivals, upserts) se demuestra con `fixtures/`, que están etiquetados como datos de prueba.

## Evaluación y fugas de información (leakage)

- Los splits de train/val/test son **temporales** (por `process_date`) y, cuando aplique, **agrupados por `customer_id`**.
- Los casos de `eval/` son held-out: nunca se usan para ajustar prompts, umbrales ni modelos.
- Campos generados por el sistema origen (`detected_intents`, `detected_sentiment`, `is_fraud`, etc.) se tratan como **etiquetas débiles** hasta validarlos manualmente con una muestra.

## Credenciales

Las credenciales de S3 están en el PDF del diccionario de datos, que **no se sube al repo**.
Configurarlas localmente en `data/.env` (gitignored, plantilla en `.env.example`). Nunca escribirlas en código, notebooks ni prompts.
