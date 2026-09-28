# Propuesta: "No reconozco este cargo" — aclarar antes de disputar

> **Estado: propuesta para decidir en equipo.** Desarrolla la opción A de
> [`data/reports/insights.md` §6](../data/reports/insights.md#6-opciones-para-decidir),
> enmarcada dentro del intake de quejas (opción B) para no depender de una inferencia.
> Las citas en inglés son textuales del enunciado *Factored AI & Data Hackathon 2026 — Problem Statement*
> (en adelante, **[Bases]**). Las cifras vienen de `insights.md` y son **mediciones offline sobre datos sintéticos**.

## 1. Resumen

Un agente que atiende en ES y PT el caso **"no reconozco un cargo"**. Primero identifica la transacción
concreta a partir de la descripción vaga del cliente. Después reúne evidencia verificable (duplicados,
comercio habitual, país/ciudad, sesión digital, estado de la transacción) y aplica una **política
determinística fuera del modelo**. Con eso hace una de tres cosas:

1. **explica** el cargo, si la evidencia lo aclara;
2. **abre la disputa**, si es elegible, tras confirmación del cliente;
3. **deriva a un humano** (fraude probable, monto alto, datos faltantes), con un expediente de traspaso.

El objetivo de negocio es **resolver antes de que el caso se convierta en un reclamo formal que queda abierto**.
El objetivo técnico es demostrar las seis competencias que pide [Bases] sobre un único flujo profundo.

## 2. Por qué este flujo: evidencia y bases

[Bases, *Scope*] pide elegir un flujo coherente y nombra explícitamente este:

> "Select a coherent workflow, such as account or payment inquiries, card-service support,
> **transaction-dispute intake**, or credit-product information and eligibility support."

y aclara que la profundidad pesa más que la cantidad:

> "depth, demonstrated behavior, and engineering judgment determine the score; implementing more
> workflows does not earn an automatic bonus."

[Bases, *What your solution should demonstrate* §1] exige que el problema esté respaldado por datos:

> "Analyze contact reasons, relevant demand patterns, data quality, and operational constraints.
> Use this evidence to prioritize the workflow and define the intended customer and business outcomes."

### Hechos medidos (de `insights.md`)

| Hecho | Cifra | Fuente |
|---|---|---|
| Queja es el motivo con más contactos no resueltos | 41.2% del no resuelto; FCR 43.6% | §1 |
| En reclamos formales (PQR), las disputas de cargos son el mayor bloque | 40.6% de los PQR con subcategoría (`unrecognized_charge` + `improper_fee`; 36.5% si se cuentan los sin subcategoría) | §3 |
| Esas disputas siguen abiertas | ~75% abiertas; mediana 15–16 días | §3 |
| Datos para las herramientas (`transactions`, `products`, `customers`) | Integridad dueño↔producto y moneda↔producto al 100% | §5 |
| Agentes humanos que hablan portugués | 129 de 1,200 (10.8%) | §2 |

### Lo que **no** podemos afirmar

- Que las llamadas de "Queja" sean disputas. Llamadas y PQR **no se pueden vincular**
  (0.4% vs 0.36% en la ventana placebo; `insights.md` §4). No multiplicamos el 41.2% por el 40.6%.
- Por eso el agente entra por el **intake de quejas** (justificado solo con hechos medidos) y profundiza en
  disputas de cargos, la subcategoría más grande del lado PQR y la que tiene datos aptos para verificar.

## 3. Jobs to be done

Formato: *Cuando [situación], quiero [motivación], para [resultado esperado].* Cada job se asocia al
requisito de [Bases] que lo respalda y a cómo lo mediremos.

### 3.1 Cliente (job principal)

| # | Job | Tipo | Requisito [Bases] | Cómo se mide |
|---|---|---|---|---|
| C1 | Cuando veo un cargo que no reconozco, quiero saber **qué es exactamente** sin tener que encontrarlo yo en el extracto, para quedarme tranquilo o actuar rápido. | Funcional | §2 "ground factual responses in permitted account, transaction, or policy information" | Recall@k / MRR de la identificación de la transacción; tasa de resolución segura |
| C2 | Cuando el cargo sí es un error o un fraude, quiero **iniciar la disputa en la misma conversación** y saber qué va a pasar y cuándo, para no repetir mi historia en otro canal. | Funcional | §3 "Define … which actions require confirmation"; §2 "report only actions whose outcomes the system has verified" | Disputas abiertas con confirmación explícita y resultado verificado por la herramienta |
| C3 | Cuando escribo en portugués, o mezclo idiomas, quiero que me entiendan igual de bien, para no recibir un servicio peor por mi idioma. | Funcional / equidad | *Scope*: "Demonstrate interactions in Spanish and Portuguese"; *Evaluation*: "Compare relevant service outcomes by language" | Métricas por idioma con n e intervalos; análisis de brechas |
| C4 | Cuando creo que me robaron, quiero sentir que **alguien competente se hace cargo**, para no sentirme solo frente al banco. | Emocional | §3 "when it must abstain or transfer to a human" | Calidad de escalamiento: traspasos perdidos e innecesarios |
| C5 | Cuando hablo con el banco, quiero que nadie más pueda ver ni tocar mis datos, para confiar en el canal. | Emocional / seguridad | *Data boundaries*: "a national ID or customer number alone does not prove identity" | Resultados inseguros (divulgaciones o acciones no autorizadas) con conteo y denominador |

### 3.2 Agente humano (backoffice / fraude)

| # | Job | Requisito [Bases] | Cómo se mide |
|---|---|---|---|
| H1 | Cuando me llega un caso derivado, quiero recibir **el pedido, los hechos verificados, las acciones hechas, la evidencia y lo que falta resolver**, para no volver a preguntarle todo al cliente. | §3 "Provide the human agent with the request, verified facts, actions taken, supporting evidence, and unresolved questions." | Completitud del expediente contra una lista de campos obligatorios (chequeo determinístico) |
| H2 | Cuando el cliente habla portugués y yo no, quiero el expediente traducido y marcado como traducción automática, para atenderlo sin esperar a uno de los 129 agentes que hablan PT. | Restricción operativa medida (`insights.md` §2); *Scope*: "report limitations in … language coverage" | Casos PT derivados con expediente utilizable; limitaciones reportadas |
| H3 | Cuando audito un caso, quiero ver **qué fuentes, reglas y registros** llevaron a cada decisión, para justificarlo sin depender del razonamiento oculto del modelo. | §6 "hidden model chain-of-thought is not an audit artifact" | Cada decisión con trazas: tool calls, versión de la regla, evidencia |

### 3.3 Banco: operación, riesgo y cumplimiento

| # | Job | Requisito [Bases] | Cómo se mide |
|---|---|---|---|
| B1 | Cuando llega un "no reconozco", quiero resolver en el primer contacto lo que es explicable, para bajar el backlog de reclamos formales abiertos. | Intro: "measure whether your approach improves service quality and operational efficiency" | Resolución automatizada segura vs baseline; contención reportada **por separado** ("Containment alone does not demonstrate that the problem was solved") |
| B2 | Cuando el agente actúa, quiero que **los permisos y la política se apliquen en código**, no en el prompt, para que una conversación manipulada no pueda saltarlos. | §3 "Enforce permissions and policy outside model-generated prose" | Suite de ataques: inyección de prompt directa e indirecta, sesión expirada, acceso a otro cliente |
| B3 | Cuando despliegue, quiero conocer costo, latencia y límites de capacidad, para decidir si esto escala. | *Evaluation*: "End-to-end p50/p95 latency and cost per attempted case and per successful automated resolution" | p50/p95, costo por caso intentado y por resolución exitosa ("not defined" si no hay resoluciones) |
| B4 | Cuando los datos cambian (llegadas tardías, duplicados), quiero que las herramientas respondan con datos correctos y frescos, para no contestarle al cliente con información vieja. | §4 "repeatable data preparation with contracts, quality checks, lineage, and an update/freshness policy" | Pipeline existente (`data/pipelines`) + fixture de actualización etiquetado ("If only static data is supplied, demonstrate update correctness with a clearly labeled test fixture") |

## 4. Solución propuesta

```
Cliente (ES/PT)
   │
   ▼
[Sesión autenticada de prueba] ──► customer_id de la SESIÓN (nunca el del texto)
   │
   ▼
[LLM conversacional] ── entiende, pide aclaración y redacta. NO decide ni autoriza.
   │   tool calls
   ▼
[Capa de herramientas con permisos por sesión]
   ├─ find_candidate_transactions(desc) → ranking   ◄── componente aprendido
   ├─ get_evidence(txn_id)              → hechos verificables
   ├─ policy.evaluate(evidence)         → EXPLICAR | DISPUTAR | DERIVAR   (determinístico, versionado)
   ├─ open_dispute(txn_id, confirm=True)→ id + estado verificado (mock documentado)
   └─ handoff(packet)                   → expediente para humano
   │
   ▼
[Trazas y registro de ejecución] → auditoría, métricas, costo, latencia
```

### 4.1 Qué es IA y qué es determinístico

[Bases, *Think beyond the demo*]: "Justify where AI is appropriate, where deterministic logic is preferable".

| Pieza | Enfoque | Por qué |
|---|---|---|
| Entender la descripción del cliente, pedir aclaración, redactar en ES/PT | LLM | Lenguaje libre, multilingüe y ambiguo |
| Encontrar la transacción que el cliente describe | **Aprendido: ranking/recuperación** | Coincidencia difusa ("como 50 lucas", "el martes", "en el súper"). Se compara contra un baseline de filtros |
| Autenticación, alcance por cliente, permisos | Código | [Bases]: "Enforce access … in the service or tool layer" |
| Elegibilidad de la disputa, umbrales de derivación | Reglas versionadas (política sintética declarada) | Auditable y no negociable por conversación |
| Señales de evidencia (duplicado, país, sesión, estado) | Consultas SQL/código | Son hechos, no juicios |

### 4.2 Señales de evidencia candidatas

Todas salen de columnas existentes en los contratos. **Falta verificar** en `samples/` que la
distribución sintética las haga informativas. Las que no lo sean se reemplazarán por un fixture
etiquetado como sintético, según [Bases, *Data boundaries*]: "Identify which inputs are real,
de-identified, synthetic, or team-generated".

| Señal | Columnas | Lectura |
|---|---|---|
| Cargo duplicado | `transactions.amount`, `merchant_name`, `transaction_date` | Mismo comercio y monto en una ventana corta |
| Comercio habitual | historial de `merchant_name` del cliente | "Ya pagaste aquí N veces" |
| Estado de la transacción | `transaction_status` (Approved/Declined/Pending/Reversed) | Un Pending o Reversed se explica, no se disputa |
| Ubicación | `transaction_country/city`, `customers.country/city` | Fuera del país del cliente → riesgo |
| Sesión digital cercana | `digital_events.session_id`, `event_type=Login`, `ip_country` | Hubo actividad del propio cliente cerca del cargo |
| Riesgo | `fraud_score`, `is_fraud` | Umbral de derivación a fraude. `is_fraud` **solo** como etiqueta de evaluación, nunca como feature en línea (fuga) |

### 4.3 Tres casos obligatorios

[Bases, *Scope*]: "Include a normal resolution path, an ambiguous or unsupported request, and a case requiring human intervention."

| Caso | Ejemplo | Resultado esperado |
|---|---|---|
| Normal | "No reconozco un cobro de 12.990 del día 3" → comercio habitual, aprobado, sin señales de riesgo | Explicar con evidencia citada; sin disputa |
| Ambiguo | "Me cobraron algo raro la semana pasada" → 4 candidatos | Pedir aclaración (monto, comercio, fecha) antes de actuar |
| No soportado | "Quiero un aumento de cupo" | Declarar el alcance y derivar al canal correcto |
| Humano | Cargo alto en otro país, `fraud_score` alto, sin sesión del cliente | No se promete nada; se deriva a fraude con expediente |

## 5. Componente aprendido y rigor de ML

[Bases §4]: "Evaluate at least one learned component against an appropriate baseline. Use valid labels
or relevance judgments, prevent leakage, and justify representations, metrics, thresholds, and evaluation splits."

- **Tarea:** dado (descripción del cliente, historial del cliente), ordenar sus transacciones candidatas.
- **Baseline:** filtros determinísticos de monto ± tolerancia y fecha ± ventana, ordenados por cercanía.
- **Propuesto:** extracción estructurada con LLM (monto, fecha relativa, tipo de comercio) + scoring híbrido
  (léxico/embeddings sobre `merchant_name`/`merchant_category` + cercanía numérica y temporal), con un
  umbral de confianza calibrado para **pedir aclaración** en lugar de adivinar.
- **Etiquetas:** juicios de relevancia generados por el equipo. Se elige una transacción real del sample como
  objetivo y se escriben descripciones ES/PT de distinta vaguedad. Los textos del dataset no sirven
  (`insights.md` §5: `detected_intents` constante y descripciones de plantilla).
- **Fuga:** split por **cliente**, no por frase. Las paráfrasis de una misma transacción objetivo quedan en el
  mismo lado. `eval/` es held-out y no se usa para ajustar prompts (`data/AGENTS.md` regla 5).
- **Métricas:** Recall@1/@3, MRR y tasa de aclaración. Precisión cuando el agente no pide aclaración.
- **Análisis de errores** por idioma, vaguedad y tamaño del historial del cliente.

## 6. Evaluación de extremo a extremo

[Bases, *Evaluation evidence*]: "Compare your baseline and proposed system on the same held-out workload.
Report the number and mix of cases, label quality, model and prompt versions, and repeated-run variability".

| Métrica ([Bases]) | Definición en este flujo |
|---|---|
| Resolución automatizada segura | Caso elegible que termina en el resultado correcto (explicar / disputar) sin humano, sobre **todos** los casos en alcance, junto con la tasa de automatización intentada |
| Contención | Termina sin traspaso. Se reporta aparte y nunca como éxito por sí sola |
| Calidad de escalamiento | Traspasos perdidos e innecesarios + completitud del expediente |
| Resultados inseguros | Divulgación a otro cliente, disputa abierta sin confirmación, acción no verificada. Conteo / denominador |
| Eficiencia | p50/p95 de extremo a extremo; costo por caso intentado y por resolución exitosa, con supuestos de precio |
| Equidad | Todo lo anterior por idioma (ES/PT) y por segmento autorizado, con n y significancia (`_spread` en `insights.py`) |

**Casos de falla obligatorios** ([Bases §5]): datos incorrectos o faltantes, sesión expirada, intento de
acceso no autorizado, inyección de prompt (incluida la **indirecta**, vía texto en `merchant_name`),
fallas de herramientas con reintentos acotados y ambigüedad multilingüe.

**Juez LLM** (si se usa): rúbrica documentada y validada contra una muestra etiquetada por humanos o de
forma determinística, como exige [Bases].

## 7. Camino a producción (qué mostramos y qué queda pendiente)

[Bases §6]: "Demonstrate tracing, bounded retries, safe fallback, and reproducible setup. Explain capacity
limits, monitoring, access controls, data retention, and the remaining deployment work."

- **Demostramos:** trazas por conversación, reintentos acotados con fallback a derivación, setup reproducible
  y política versionada.
- **Documentamos como pendiente:** integración con un core bancario real, un proveedor de identidad real,
  retención de datos según la regulación de cada país y monitoreo en vivo. [Bases, *Scope*]: "an honest
  account of the work required before deployment".
- **Fuera de alcance, por las bases:** "No live lending decisions or movement of money is required or
  authorized by this challenge." `open_dispute` es un mock con contrato documentado.

## 8. Riesgos y limitaciones conocidas

1. **Justificación por inferencia:** se mitiga entrando por el intake de quejas (§2).
2. **Datos sintéticos uniformes** (`insights.md` §8): las señales de evidencia pueden no discriminar. Se
   verifica en `samples/` antes de construir; si no discriminan, se usan fixtures etiquetados.
3. **No hay texto en portugués en el dataset:** todo lo PT lo genera el equipo y se declara así.
4. **Calidad de `customers`:** `cus_document_type_matches_country` falla en 49.9%. No usamos el documento como
   prueba de identidad (la sesión de prueba es la identidad), en línea con [Bases].
5. **`was_resolved` es dudoso** como etiqueta (`insights.md` §1). El baseline humano se reporta con esa salvedad.

## 9. Decisiones que necesitamos del equipo

- [ ] ¿Aprobamos este flujo (A dentro de B) como foco único?
- [ ] ¿Qué política de disputa sintética usamos (plazo, montos, estados elegibles), y quién la redacta?
- [ ] ¿Tamaño del set etiquetado ES/PT y quién etiqueta? (propuesta inicial: ≥300 descripciones, ≥30% PT)
- [ ] ¿Qué LLM y qué presupuesto de costo por caso?
- [ ] ¿Consultamos a los organizadores si `complaints.origin_interaction_id` vacío es intencional?
