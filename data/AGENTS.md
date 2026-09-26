# Reglas para agentes (Claude Code, Codex, etc.) dentro de data/

1. **`raw/` es de solo lectura.** Nunca modificar, renombrar ni borrar archivos ahí.
2. **Desarrollar sobre `samples/`**, no sobre las tablas completas (transactions: 5M filas, digital_events: 10M). Usar las tablas completas solo en pipelines explícitos.
3. **No enviar filas crudas a APIs de modelos externos.** Aunque el dataset es sintético, se trata como datos de clientes: agregar, anonimizar o usar muestras mínimas.
4. **No imprimir ni escribir credenciales** (AWS keys, `.env`). No leer el PDF del diccionario para sacar credenciales.
5. **No tocar `eval/`** para ajustar prompts o modelos: es el conjunto held-out. Agregar casos solo cuando se pida y registrarlos en `eval/README.md`.
6. **Validar contra `contracts/`** antes de escribir en `staging/` o `curated/`. Si el esquema no coincide, reportarlo; no "arreglar" el contrato en silencio.
7. **Todo derivado se regenera con código.** No editar a mano archivos de `staging/`, `curated/` ni `samples/`.
8. Cada nuevo dataset derivado registra su linaje en `manifests/`.
9. **`reports/` se genera con código** (`python -m pipelines`). No editar los .md a mano: cambiar el generador.
10. Antes de afirmar una diferencia entre grupos, reportar n y significancia (ver `_spread` en `pipelines/insights.py`).
