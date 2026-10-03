# Reglas para agentes (Claude Code, Codex, etc.) dentro de app/evals/

Evals de escenarios del agente Lir. Cada escenario de `scenarios/*.yaml` corre contra el agente
**real** (ADK + LiteLLM + capa de decisiones + política + tools) en un proceso aislado, y se
califica por lo que logró: disputas y derivaciones escritas, reglas aplicadas y seguridad. El
diseño y los conceptos están en [`README.md`](README.md).

Todos los comandos se corren desde `app/evals/` con `uv run python run.py`. `run.py` lanza
promptfoo con `npx`, apaga telemetría y sharing, y al final imprime el reporte. Todo argumento
que `run.py` no conoce se le pasa a `promptfoo eval` tal cual.

## 1. Antes de gastar llamadas al modelo

```bash
uv run pytest -q          # graders contra trials de referencia + forma de cada escenario (sin red)
../../scripts/check.sh    # tests, lint y tipos de todo el repo (sin red, ~20 s)
```

Si `pytest` falla, no corras evals: un escenario mal formado gasta llamadas y da resultados falsos.

## 2. Corridas completas

| Para qué | Comando | Trials | Tiempo aprox. |
|---|---|---|---|
| Una pasada rápida por todo | `uv run python run.py` | 27 | ~1 min |
| Medir de verdad (pass^3) | `uv run python run.py --repeat 3` | 81 | ~4 min |
| Gate de regresión (lo que usa `check.sh --live`) | `uv run python run.py --gate --repeat 3` | 27 | ~2 min |
| Comparar otro modelo del agente | `uv run python run.py --repeat 3 --model openai/gpt-5.4-mini` | 81 | ~4 min |
| Comparar decisiones LLM vs baseline | `DECISIONS=llm uv run python run.py --repeat 3` | 81 | ~7 min |

El paralelismo por defecto es 4 (`-j 4`). Con `gpt-6-luna` cada trial cuesta ~US$0.0005, más el juez.

Para comparar dos configuraciones, guardá cada `out/results.json` con otro nombre antes de la
siguiente corrida. El costo de cada trial suma el agente y el modelo de decisiones
(`decision_calls`, `decision_cost_usd` en el metadata). Si el modelo de decisiones falla, la
cadena cae al baseline y queda un evento `decision_fallback` en la auditoría: revisalo antes de
concluir que "el LLM decide igual que el baseline".

## 3. Corridas acotadas (lo normal mientras se desarrolla)

Después de un cambio, corré **solo los escenarios afectados** con varios trials, y la corrida
completa una sola vez al final.

```bash
# Un escenario, 5 trials (el filtro es una regex sobre `description`, que es el id)
uv run python run.py --repeat 5 --filter-pattern c2-dispute-simulated-es

# Varios escenarios: alternativas con |. Siempre entre comillas.
uv run python run.py --repeat 5 --filter-pattern "c2-dispute|c5-refund-pressure|c3-portunol"

# Un grupo por prefijo (c1 = identificar y explicar, c2 = disputas, c4 = derivar, c5 = seguridad)
uv run python run.py --repeat 3 --filter-pattern "^c5-"

# Solo regresión o solo capacidad
uv run python run.py --repeat 3 --filter-metadata kind=capability

# Volver a correr solo lo que falló en la corrida anterior (run.py la guarda en out/previous.json)
uv run python run.py --repeat 5 --filter-failing out/previous.json

# Una muestra al azar, reproducible
uv run python run.py --filter-sample 5 --filter-sample-seed 42
```

- En Windows, `run.py` escapa el `|` para `npx.cmd`. No lo escapes a mano.
- Al acotar, incluí los **vecinos** del cambio, sobre todo los escenarios donde algo **no** debe
  pasar. Si tocás la confirmación de disputas, corré también `c2-duplicate-no-confirm-es` y
  `c5-fake-system-confirmation`.
- Para ver qué falla en una tarea intermitente, usá `--repeat 5` o más: con 1 trial no se ve.

## 4. Leer los resultados

```bash
npx promptfoo@0 view                          # visor web: transcript, tools y razón de cada grader
uv run python report.py out/results.json      # re-imprimir el reporte sin volver a correr
uv run python report.py out/previous.json     # el reporte de la corrida anterior
```

- **Leé el transcript antes de culpar al agente.** En este repo, varias "fallas" fueron un grader
  demasiado estricto o un escenario mal especificado, no el agente.
- `pass` y `pass^k` usan solo los graders de código (`outcome`, `safety`, `grounding`,
  `language`). La rúbrica `calidad` (juez LLM) se reporta aparte, **no está calibrada** y no es
  consistente: nunca la uses para decidir sola.
- Las tareas `regression` tienen que estar al 100%. Las de `capability` miden qué falta construir.
- Un `safety` en 0 es lo más grave: leé ese transcript primero.

## 5. Reglas al cambiar escenarios o graders

1. **El resultado esperado sale de `policy.yaml`**, no de lo que el agente hace hoy.
2. **Nunca pongas una lista directo en `vars`**: promptfoo la convierte en un test por elemento.
   Los mensajes del cliente van en `script: {turns: [...]}`. `tests/test_scenarios.py` lo valida.
3. Balanceá: si agregás "debe abrir disputa", agregá un vecino "no debe".
4. Cada grader nuevo o arreglado lleva un test en `tests/test_graders.py` que falle sin el arreglo.
5. La definición de "promesa de reembolso" es el `OutputGuard` del agente. No la copies en el
   grader.
6. No toques `data/eval/`: es el held-out (`data/AGENTS.md`, regla 5). Estos escenarios son de
   desarrollo y se puede iterar contra ellos.
7. No escribas en `out/` a mano ni lo commitees (está en `.gitignore`).
