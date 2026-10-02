# Propuesta: "No reconozco este cargo" — aclarar antes de disputar

> **Estado: propuesta para decidir en equipo.** Desarrolla la opción A de
> [`data/reports/insights.md` §6](../data/reports/insights.md#6-opciones-para-decidir),
> enmarcada dentro del intake de quejas (opción B) para no depender de una inferencia.
> Las citas en inglés son textuales del enunciado *Factored AI & Data Hackathon 2026 — Problem Statement*
> (en adelante, **[Bases]**). Las cifras vienen de `insights.md` y son **mediciones offline sobre datos sintéticos**.

## 1. Resumen

### La idea en una frase

> Cuando un cliente dice "no reconozco este cargo", el agente **encuentra el cargo, lo aclara con evidencia
> y solo abre una disputa cuando corresponde**. Tres piezas se reparten el trabajo:
> **el LLM conversa, Jev decide rápido y con probabilidad, y el código autoriza.**

| Pieza | Qué hace | Qué **no** hace |
|---|---|---|
| **LLM** (conversación) | Entiende al cliente en ES/PT, extrae monto, fecha y comercio, pide aclaraciones y redacta la respuesta citando la evidencia | No decide el ruteo, no autoriza, no inventa reglas |
| **Jev** (decisiones tipadas, [§5](#5-jev-la-capa-de-decisiones-rápidas)) | En cada turno responde preguntas cerradas con probabilidad calibrada: intención, ¿pide humano?, ¿sospecha de robo?, ¿qué comercio describe? | No mira montos ni fechas, no filtra ataques, no decide la elegibilidad |
| **Código** (herramientas + política) | Autentica, limita cada consulta al cliente de la sesión, filtra por monto y fecha, calcula la evidencia y aplica la política de disputa versionada | No conversa |

La apuesta técnica: separar **decidir** de **conversar** hace que cada decisión del agente sea medible
(probabilidad, umbral, exactitud), barata (~100 ms, US$0.042 por millón de tokens) y auditable,
en lugar de quedar enterrada en la prosa del LLM. Esto responde directamente a
[Bases]: "Make explicit trade-offs across autonomy, accuracy, latency, cost, and human oversight".

### El flujo

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
[LLM conversacional] ── entiende, extrae monto/fecha/comercio, pide aclaración y redacta. NO decide ni autoriza.
   │                        ▲
   │                        │ decisiones tipadas con probabilidad (en paralelo, ~100 ms)
   │                  [Jev] ── intención · ¿pide humano? · ¿sospecha de robo? · ¿qué comercio?
   │                        │   confianza < umbral → aclarar o derivar
   │   tool calls
   ▼
[Capa de herramientas con permisos por sesión]
   ├─ find_candidate_transactions(slots) → candidatos por monto/fecha (código) + comercio (Jev)
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
| Extraer monto y fecha ("como 50 lucas", "el martes") | LLM → validado por código | Jev no es confiable con números ni fechas; el código normaliza y valida |
| Intención, ¿pide humano?, ¿sospecha de robo?, ¿qué comercio describe? | **Jev** (pretrained, evaluado) | Decisiones cerradas con probabilidad calibrada, rápidas y baratas: permiten umbrales explícitos |
| Filtrar transacciones por monto y fecha | Código | Aritmética exacta, sin modelo |
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

## 5. Jev: la capa de decisiones rápidas

### 5.1 Qué es y por qué lo usamos

[Jev](https://en.wikipedia.org/wiki/Jev_%28AI_model%29) es un modelo de TypeSafe AI, en acceso anticipado desde
el 15-sep-2026. **El equipo ya tiene acceso.** **No genera texto:** recibe un *estado* (texto o pares nombre-valor) y preguntas tipadas, y
devuelve respuestas con probabilidad ([docs de Cloudflare](https://developers.cloudflare.com/ai/models/typesafe/jev/)):

| Tipo de pregunta | Devuelve | Uso en este flujo |
|---|---|---|
| `noul` (sí/no) | probabilidad 0–1 | ¿pide un humano?, ¿expresa sospecha de robo?, ¿confirma lo que le mostramos? |
| `choice` | distribución sobre opciones | intención; qué comercio candidato describe |
| `score` | valor sobre una escala descrita | (no se usa en el MVP) |

Por qué encaja con [Bases]:

- **Umbrales explícitos y justificables.** [Bases §4] pide "justify … thresholds". Con una probabilidad por
  decisión, el umbral se elige en validación y se reporta la curva cobertura/exactitud, en vez de confiar en
  una respuesta en prosa.
- **Latencia y costo.** Se reportan 70–500 ms por llamada, preguntas evaluadas en paralelo y US$0.042 por millón
  de tokens de entrada (salida gratis). [Bases, *Evaluation*] pide costo por caso y p50/p95.
- **Separa decidir de conversar.** Cada decisión queda en la traza como `(pregunta, respuesta, probabilidad,
  umbral, versión)`. Eso es el registro de ejecución que [Bases §6] acepta como auditoría, a diferencia del
  "hidden model chain-of-thought".

### 5.2 Las decisiones que toma Jev en cada turno

Todas se hacen **sobre el texto del cliente** (y, en D4, los nombres de comercio candidatos), en una sola
llamada paralela:

| # | Pregunta | Tipo | Si la confianza es baja |
|---|---|---|---|
| D1 | Intención: `cargo_no_reconocido` · `cobro_indebido` · `consulta_movimiento` · `otra_queja` · `fuera_de_alcance` | choice | Pedir aclaración; si persiste, derivar |
| D2 | ¿El cliente pide explícitamente hablar con una persona? | noul | Ante la duda, ofrecer la derivación |
| D3 | ¿El cliente expresa que le robaron la tarjeta o los datos? | noul | Ante la duda, tratar como posible fraude (derivar) |
| D4 | ¿Cuál de estos comercios candidatos describe el cliente? (`merchant_name` + `merchant_category`, **sin montos ni fechas**) | choice | Mostrar los candidatos y preguntar |

Umbrales **asimétricos** según el costo del error: en D2 y D3 un falso negativo (no derivar a quien lo necesita)
es peor que un falso positivo, así que el umbral para derivar es bajo.

### 5.3 Lo que Jev **no** hace, y por qué

La propia documentación declara que Jev "no es bueno con números, fechas ni 'contenido adversarial'"
([Simon Willison, 21-sep-2026](https://simonwillison.net/2026/Sep/21/jev/)). Por eso:

- **Montos y fechas:** los extrae el LLM y los valida y filtra el código. Jev nunca compara montos.
- **Inyección de prompt:** la defensa es estructural. Los permisos están en la capa de herramientas, el texto de
  los datos se trata como datos y las acciones requieren confirmación. No dependemos de un clasificador.
- **Elegibilidad de la disputa:** es política determinística. Jev solo enruta.

### 5.4 Datos que salen del perímetro

Jev es una API externa. [Bases, *Data boundaries*]: "Do not include private customer records … in external model
requests". Regla 3 de `data/AGENTS.md`: no enviar filas crudas.

- **Se envía:** el mensaje del cliente y, para D4, solo nombre y categoría de los comercios candidatos.
- **No se envía:** IDs, documento, saldos, montos ni fechas de la cuenta.
- El proveedor declara "zero data retention" vía Cloudflare. Lo documentamos como afirmación del proveedor, no
  como algo verificado por nosotros.

### 5.5 Evaluación: el componente aprendido de la entrega

[Bases §4]: "Evaluate at least one learned component against an appropriate baseline. Use valid labels
or relevance judgments, prevent leakage, and justify representations, metrics, thresholds, and evaluation splits."
[Bases, *Architecture freedom*]: para soluciones con modelos preentrenados, el rigor se demuestra con
"component selection, relevance or intent labels, representations, leakage prevention, held-out evaluation, and error analysis".

Se evalúa **D1 (intención)** como componente principal, y D4 (comercio) como secundario, con tres candidatos
sobre el mismo set held-out:

| Candidato | Qué es |
|---|---|
| **Baseline** | Reglas por palabras clave ES/PT |
| **Jev** | Preguntas `choice`/`noul`, con umbral elegido en validación |
| **LLM** | El mismo LLM del agente, con salida estructurada |
| **Laya** (opcional, §5.7) | Modelo abierto de decisiones tipadas (checkpoint multilingüe), ajustado con nuestras etiquetas y calibrado. Corre en nuestro hardware |

- **Métricas:** macro-F1 por clase, **calibración** (ECE, diagrama de confiabilidad), curva cobertura/exactitud
  (cuánto automatizamos con cada umbral), p50/p95 y costo por 1.000 decisiones. Varias corridas del LLM para
  reportar variabilidad (Jev y las reglas son deterministas o casi).
- **Etiquetas:** set generado y etiquetado por el equipo en ES y PT, incluyendo portuñol, jerga regional
  (MX/CO/AR) y casos fuera de alcance. Los textos del dataset no sirven (`insights.md` §5). Doble etiquetado de
  una muestra para medir el acuerdo entre anotadores.
- **Fuga:** split por **plantilla semilla**: todas las paráfrasis de una misma semilla quedan del mismo lado.
  El umbral se fija en validación y se reporta **una sola vez** en test. `eval/` no se usa para ajustar
  (`data/AGENTS.md` regla 5).
- **Análisis de errores** por idioma, país/jerga y clase. [Bases] pide investigar disparidades por idioma.
  El soporte de portugués de Jev no está documentado, así que **se mide**, no se asume.

### 5.6 Si Jev falla

Las decisiones D1–D4 viven detrás de una interfaz `DecisionModel` con la misma firma
(pregunta tipada → respuesta + probabilidad). Si Jev falla, supera el tiempo límite o se agota la cuota
(es un servicio en acceso anticipado, sin SLA publicado), se usa el LLM con salida estructurada. Si también falla, el agente deriva al humano (safe fallback, [Bases §6]).
La comparación de §5.5 funciona igual con cualquiera de los tres.

### 5.7 Opción abierta: Laya

Jev es propietario: no publica pesos ni permite correrlo en nuestra máquina
([Failproof AI](https://befailproof.ai/jev/is-jev-open-source/)). La alternativa abierta es
**Laya**, de Convai Innovations, publicada el 18-sep-2026. Hace el mismo trabajo (preguntas `choice`, `score`
y sí/no con probabilidad, sin generar texto), con **pesos abiertos bajo Apache 2.0**, y corre en nuestro
hardware. La versión en inglés es un encoder ModernBERT-large de 421M parámetros, y hay un checkpoint
multilingüe basado en mmBERT
([Flowtivity](https://flowtivity.ai/blog/laya-open-source-jev-alternative/)).

Lo que dicen las evaluaciones independientes, y lo que implica para nosotros:

| Hallazgo publicado | Implicancia |
|---|---|
| **Sin ajuste no sirve:** 0.362 de exactitud en el benchmark de decisiones tipadas, por debajo de la clase mayoritaria (0.461); ajustado llega a 0.766 ([BestHub](https://www.besthub.dev/articles/open-source-decision-model-laya-vs-jev-speed-wins-zero-shot-fails-befced0a2228)) | Hay que **ajustarlo con nuestras etiquetas**. Su notebook usa ~30k preguntas y 4–5 h en dos T4 gratuitas de Kaggle; nosotros tendremos cientos. Es el principal riesgo |
| **Calibración:** ECE de 0.466 sin ajustar, 0.081 tras reajustar la temperatura; en el checkpoint multilingüe "cannot be trusted until calibrated" | La calibración es trabajo nuestro (temperature scaling en validación) y se reporta |
| **Idiomas:** checkpoint multilingüe de más de 100 idiomas; 45 de 51 evaluados superan 3× el azar. No se detalla ES/PT | Usamos el multilingüe y **medimos** ES vs PT |
| **Muchas opciones:** cae con más de 20 opciones (Banking77: 0.425 vs 0.870 de Jev) | Nuestras decisiones tienen 2 a 5 opciones: dentro de su zona buena |
| **Hardware:** necesita GPU; en CPU se reporta una mediana de 49.4 s por predicción | Evaluación en GPU (Kaggle/Colab). En CPU no sirve para el camino en vivo |
| **Contexto:** 1024 tokens en el multilingüe | Alcanza para un mensaje del cliente, no para toda la conversación |

**Por qué igual vale la pena como candidato:**

- **Privacidad:** corre local, así que **ningún dato sale del perímetro** (§5.4). Es el argumento más fuerte ante
  [Bases, *Data boundaries*].
- **Muestra rigor de ML propio:** ajuste, calibración y control de fuga los hacemos nosotros. Las bases lo
  reconocen: "A model-training pipeline is one way to provide that evidence".
- **Fallback sin dependencias externas:** implementa el mismo `DecisionModel` (§5.6), sin cuota ni SLA de terceros.
- **Deja un trade-off claro en el informe:** Jev (listo sin ajuste, externo) vs Laya (ajustado por nosotros,
  privado, gratis) vs LLM (flexible, más lento y caro) vs reglas (baseline).

**Riesgo de fuga específico:** si ampliamos el set de entrenamiento con paráfrasis generadas, estas se
generan **solo desde semillas del split de entrenamiento**. Validación y test no se tocan.

*Descartado:* [OpenJev](https://github.com/kyegomez/open-jev) (Apache 2.0) reconstruye la arquitectura, pero
viene con pesos aleatorios ("random weights"), tiene 6 commits y no publica benchmarks.

Es opcional: entra si el MVP con Jev y el LLM está listo antes del día 6.

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
6. **Jev es nuevo y externo:** servicio en acceso anticipado sin SLA publicado, cifras de rendimiento publicadas por el fabricante y soporte de PT sin documentar. Mitigación: interfaz intercambiable (§5.6) y medición propia (§5.5).

## 9. Decisiones que necesitamos del equipo

- [ ] ¿Aprobamos este flujo (A dentro de B) como foco único?
- [ ] ¿Qué política de disputa sintética usamos (plazo, montos, estados elegibles), y quién la redacta?
- [ ] ¿Tamaño del set etiquetado ES/PT y quién etiqueta? (propuesta inicial: ≥300 descripciones, ≥30% PT)
- [ ] ¿Qué LLM y qué presupuesto de costo por caso?
- [x] Acceso a Jev: el equipo ya lo tiene.
- [ ] ¿Límites de cuota/rate de nuestra cuenta de Jev? (define el tamaño de las corridas de evaluación)
- [ ] ¿Incluimos Laya (§5.7)? Requiere GPU (Kaggle/Colab) para ajustar y evaluar
- [ ] ¿Consultamos a los organizadores si `complaints.origin_interaction_id` vacío es intencional?
