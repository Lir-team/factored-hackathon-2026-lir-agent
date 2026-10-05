# Evaluación de las decisiones tipadas (EVAL-01)

> Generado por `uv run python -m decision_eval.report` (app/evals) — 2026-10-05T20:16:57+00:00. No editar a mano.
> Bases §4: *"Evaluate at least one learned component against an appropriate baseline. Use valid labels or relevance judgments, prevent leakage, and justify representations, metrics, thresholds, and evaluation splits."*

## Qué se evalúa

Las decisiones que el agente toma en cada turno, antes de conversar: **D1** intención (5 clases), **D2** ¿pide una persona?, **D3** ¿sospecha de robo? La política (`policy.yaml`) actúa sobre sus probabilidades con umbrales.

| Candidato | Qué es |
|---|---|
| Baseline | Reglas por palabras clave ES/PT (`decision_layer.keywords`) |
| LLM | El mismo modelo del agente con salida estructurada (`llm:openai/gpt-6-luna`), sin fallback |
| Jev | TypeSafe AI a través de OpenRouter (`llm:openrouter/typesafe/jev-router`), con el mismo adaptador de salida estructurada que el LLM, sin fallback |

## Datos, etiquetas y fuga

- **320 mensajes** de 80 semillas (4 paráfrasis cada una): 216 en español (MX/CO/AR), 80 en portugués (BR), 24 en portuñol. Incluye negaciones ("no quiero hablar con nadie"), robo figurado ("me están robando con la comisión") y jerga.
- **Procedencia:** texto sintético generado por el equipo para esta evaluación (`data/eval/decisions/seeds.yaml`); no son datos de clientes ni salen del dataset, cuyos textos son plantillas (`insights.md` §5).
- **Etiquetas:** un anotador, siguiendo las definiciones de `decision_layer/questions.py`. **Segunda anotación ciega pendiente:** `data/eval/decisions/second_labeling.csv` (40 mensajes) para que otra persona etiquete sin ver las etiquetas; este reporte calcula el acuerdo (kappa) cuando se complete.
- **Split sin fuga por semilla:** todas las paráfrasis de una semilla quedan del mismo lado; estratificado por intención. **Validación** (160) se usa solo para elegir umbrales; **test** (160) se reporta una vez.

## Resultados en test

### D1 intención

| Candidato | Corridas | Exactitud [IC 95%] | Macro-F1 | ECE | Umbral elegido en validación | Cobertura / exactitud en test con ese umbral |
|---|---|---|---|---|---|---|
| keywords | 1 | 0.444 [30-60] | 0.410 | 0.034 | 0.25 | 31.2% / 94.0% |
| llm | 3 | 0.975 ± 0.006 [86-99] | 0.973 ± 0.006 | 0.047 ± 0.010 | 0.00 | 100.0% / 96.9% |
| jev | 3 | 0.971 ± 0.018 [88-100] | 0.969 ± 0.017 | 0.063 ± 0.008 | 0.00 | 100.0% / 98.1% |

*Umbral elegido:* el más bajo con el que, en validación, lo decidido automáticamente acierta al menos el 95.0%; por debajo, el agente pide aclaración (regla T4). Exactitud de la primera corrida; ± es la desviación entre corridas. Los IC 95% son de Wilson con **una observación por semilla**, no por mensaje: las 4 paráfrasis de una semilla no son independientes, así que el intervalo es conservador.

**keywords: F1 por clase (corrida 1)**

| Clase | Precisión | Recall | F1 | n |
|---|---|---|---|---|
| cargo_no_reconocido | 0.32 | 1.00 | 0.48 | 40 |
| cobro_indebido | 1.00 | 0.41 | 0.58 | 32 |
| consulta_movimiento | 1.00 | 0.19 | 0.32 | 32 |
| otra_queja | 0.71 | 0.21 | 0.32 | 24 |
| fuera_de_alcance | 0.88 | 0.22 | 0.35 | 32 |

| Política actual (umbral 0.50) | Cobertura | Exactitud de lo decidido |
|---|---|---|
| keywords | 31.2% | 94.0% |

Calibración (diagrama de confiabilidad):

| Confianza | n | Confianza media | Exactitud |
|---|---|---|---|
| 0.0-0.2 | 110 | 0.20 | 0.22 |
| 0.4-0.6 | 1 | 0.50 | 1.00 |
| 0.8-1.0 | 49 | 1.00 | 0.94 |

**llm: F1 por clase (corrida 1)**

| Clase | Precisión | Recall | F1 | n |
|---|---|---|---|---|
| cargo_no_reconocido | 1.00 | 0.95 | 0.97 | 40 |
| cobro_indebido | 1.00 | 1.00 | 1.00 | 32 |
| consulta_movimiento | 0.97 | 1.00 | 0.98 | 32 |
| otra_queja | 0.86 | 1.00 | 0.92 | 24 |
| fuera_de_alcance | 1.00 | 0.91 | 0.95 | 32 |

| Política actual (umbral 0.50) | Cobertura | Exactitud de lo decidido |
|---|---|---|
| llm | 100.0% | 96.9% |

Calibración (diagrama de confiabilidad):

| Confianza | n | Confianza media | Exactitud |
|---|---|---|---|
| 0.4-0.6 | 2 | 0.56 | 0.00 |
| 0.6-0.8 | 7 | 0.74 | 0.86 |
| 0.8-1.0 | 151 | 0.95 | 0.99 |

**jev: F1 por clase (corrida 1)**

| Clase | Precisión | Recall | F1 | n |
|---|---|---|---|---|
| cargo_no_reconocido | 0.98 | 1.00 | 0.99 | 40 |
| cobro_indebido | 1.00 | 0.97 | 0.98 | 32 |
| consulta_movimiento | 1.00 | 1.00 | 1.00 | 32 |
| otra_queja | 0.92 | 1.00 | 0.96 | 24 |
| fuera_de_alcance | 1.00 | 0.94 | 0.97 | 32 |

| Política actual (umbral 0.50) | Cobertura | Exactitud de lo decidido |
|---|---|---|
| jev | 100.0% | 98.1% |

Calibración (diagrama de confiabilidad):

| Confianza | n | Confianza media | Exactitud |
|---|---|---|---|
| 0.4-0.6 | 2 | 0.59 | 0.50 |
| 0.6-0.8 | 15 | 0.74 | 0.93 |
| 0.8-1.0 | 143 | 0.94 | 0.99 |

### D2 ¿pide una persona? y D3 ¿sospecha de robo?

En validación se buscan los umbrales que detectan al menos el 95% de los casos (no derivar a quien lo necesita es peor que derivar de más) y se verifica que el de la política caiga dentro. Test se mide con el umbral de la política.

| Decisión | Candidato | Umbrales válidos en validación | Política | ¿Dentro? | Recall en test [IC 95%] | Precisión en test | ECE |
|---|---|---|---|---|---|---|---|
| D2 pide persona | keywords | ninguno | 0.50 | no | 67.9% [33-90] | 90.5% | 0.069 |
| D2 pide persona | llm | 0.05-0.95 | 0.50 | sí | 100.0% [65-100] | 100.0% | 0.009 ± 0.001 |
| D2 pide persona | jev | 0.05-0.95 | 0.50 | sí | 100.0% [65-100] | 100.0% | 0.013 ± 0.001 |
| D3 robo | keywords | ninguno | 0.30 | no | 68.8% [26-93] | 100.0% | 0.031 |
| D3 robo | llm | 0.05-0.80 | 0.30 | sí | 100.0% [51-100] | 69.6% | 0.027 ± 0.007 |
| D3 robo | jev | 0.05-0.70 | 0.30 | sí | 100.0% [51-100] | 80.0% | 0.029 ± 0.005 |

### Equidad por idioma (D1, test)

| Candidato | Español | Portugués | Portuñol |
|---|---|---|---|
| keywords | 41.7% [25-60] (n=108) | 45.5% [21-72] (n=44) | 75.0% [20-97] (n=8) |
| llm | 96.3% [82-99] (n=108) | 97.7% [71-100] (n=44) | 100.0% [34-100] (n=8) |
| jev | 97.2% [83-100] (n=108) | 100.0% [74-100] (n=44) | 100.0% [34-100] (n=8) |

Una diferencia solo cuenta si los intervalos (por semilla) no se solapan (`data/AGENTS.md` regla 10).

### Latencia y costo

| Candidato | p50 | p95 | Costo por 1.000 mensajes (D1+D2+D3 en una llamada) |
|---|---|---|---|
| keywords | 0.015 ms | 0.025 ms | US$0.000 |
| llm | 2073.710 ± 56.140 ms | 3436.583 ± 558.475 ms | US$0.122 |
| jev | 3069.149 ± 37.548 ms | 5840.689 ± 376.905 ms | sin precio en LiteLLM |

Costo con los precios de LiteLLM para el modelo; latencia medida con 8 llamadas concurrentes.

## Análisis de errores (test, corrida 1)

**keywords: 89 errores de intención.** Confusiones más frecuentes: consulta_movimiento → cargo_no_reconocido (25); fuera_de_alcance → cargo_no_reconocido (23); cobro_indebido → cargo_no_reconocido (19); otra_queja → cargo_no_reconocido (19); fuera_de_alcance → otra_queja (2)

**llm: 5 errores de intención.** Confusiones más frecuentes: fuera_de_alcance → otra_queja (3); cargo_no_reconocido → otra_queja (1); cargo_no_reconocido → consulta_movimiento (1)

| Mensaje | Etiqueta | Predicción (p) |
|---|---|---|
| Estão usando meus dados, preciso falar com alguém do banco | cargo_no_reconocido | otra_queja (0.64) |
| No necesito un ejecutivo, nada más quiero saber qué es este cobro raro | cargo_no_reconocido | consulta_movimiento (0.56) |
| ¿A qué hora abre la sucursal del centro? | fuera_de_alcance | otra_queja (0.82) |
| ¿La sucursal abre los sábados? | fuera_de_alcance | otra_queja (0.83) |
| ¿Cuál es el horario de atención en ventanilla? | fuera_de_alcance | otra_queja (0.55) |

Errores de **robo** con el umbral actual:

- "Parce, me aparece una compra en la tarjeta que yo no hice" → etiqueta False, p=0.7
- "Me sale un débito de 80.000 que no es mío" → etiqueta False, p=0.3
- "No sé qué es esa compra que me aparece del martes, yo no fui" → etiqueta False, p=0.72
- "Oi, tengo una compra no cartão que eu não fiz" → etiqueta False, p=0.72
- "Me aparece uma cobrança que yo no hice" → etiqueta False, p=0.3
- "Hay un débito estranho que não é meu" → etiqueta False, p=0.35
- "Me aparece una compra que no es mía, necesito un asesor" → etiqueta False, p=0.68

**jev: 3 errores de intención.** Confusiones más frecuentes: fuera_de_alcance → otra_queja (2); cobro_indebido → cargo_no_reconocido (1)

## Qué dicen los resultados

1. **Baseline:** acierta la intención el 44.4% [30-60]; con el umbral de la política decide el 31.2% de los mensajes y en el resto pide aclaración. Entre idiomas: sin diferencia significativa.
2. **LLM:** 97.5% de exactitud en promedio [86-99], a US$0.122 por 1.000 mensajes y ~2.1 s de latencia (p50); con el umbral de la política decide el 100.0% y acierta el 96.9%. Entre idiomas: sin diferencia significativa.
3. **Recomendación:** usar `DECISIONS=llm` (con el baseline como respaldo si el LLM falla): su exactitud supera a la del baseline con intervalos que no se solapan. La latencia se suma a cada turno, porque la política enruta el turno con estas decisiones antes de que el modelo converse.
4. **Umbrales de la política:** pide persona 0.50 dentro del rango válido; robo 0.30 dentro del rango válido; intención 0.50 acierta el 96.9% de lo que decide. Con el LLM, los umbrales actuales quedan respaldados por esta evaluación.
5. **Jev:** 97.1% de exactitud en promedio [88-100], ~3.1 s (p50); con el umbral de la política decide el 100.0% y acierta el 98.1%. Frente al LLM: los intervalos se solapan, así que con estos datos son equivalentes. Umbrales de la política con Jev: pide persona dentro; robo dentro.

## Limitaciones

- **La guía de D3 admite dos lecturas:** incluye "uso por terceros", y una frase como "una compra que yo no hice" puede leerse así. Al revisar los errores de robo listados arriba, la segunda anotación (columna `theft`) dirá si son errores del modelo o de la etiqueta.
- Un solo anotador hasta completar la segunda anotación; las etiquetas pueden tener sesgo del autor.
- Texto sintético: no reemplaza mensajes reales de clientes; el portuñol y la jerga son una aproximación.
- n pequeño en D3 (robo) y en portuñol: los intervalos son anchos.
- Jev se evalúa a través de OpenRouter, no de Cloudflare Workers AI; su costo no aparece porque LiteLLM no tiene su precio.
- Las probabilidades del LLM son las que el modelo declara; la ECE mide cuánto se puede confiar en ellas.
