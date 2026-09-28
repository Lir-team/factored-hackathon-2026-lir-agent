# agent/

Código del agente. Por ahora contiene la **capa de decisiones tipadas** descrita en la §5 de
[`docs/propuesta-opcion-1-disputas.md`](../docs/propuesta-opcion-1-disputas.md).

Solo usa la librería estándar de Python (>= 3.10); no requiere instalar nada.

```
agent/
├── config.py              # lee variables de entorno, luego .env y data/.env (nunca las imprime)
└── decisions/
    ├── base.py            # contrato: Noul / Choice -> Answer con probabilidad; DecisionModel
    ├── questions.py       # decisiones D1–D4 de la propuesta
    ├── jev.py             # cliente de Jev vía Cloudflare Workers AI (reintentos acotados, errores tipados)
    ├── keywords.py        # baseline determinístico ES/PT
    ├── chain.py           # cadena de respaldo: Jev -> baseline -> error (el agente deriva)
    └── __main__.py        # smoke test
```

## Uso

```python
from agent.decisions import build_default
from agent.decisions.questions import TURN_QUESTIONS

model = build_default()            # Jev si JEV_ENABLED=1, si no (o si falla) el baseline
result = model.decide("No reconozco este cargo", TURN_QUESTIONS)
result.answers["intencion"].value, result.answers["intencion"].probability
```

`state` es **solo texto del cliente** (y, para D4, nombres de comercio). Nunca IDs, documentos, saldos,
montos ni fechas de la cuenta: salen del perímetro (§5.4 y regla 3 de `data/AGENTS.md`).

## Conectar Jev

Estado actual: **desconectado**. Las credenciales funcionan, pero Cloudflare responde
`402 Insufficient balance` porque Jev se cobra desde el saldo de AI Gateway (no entra en el cupo gratis
de Workers AI).

1. Cargar saldo: dashboard de Cloudflare → **AI → AI Gateway → Credits Available → Manage → Top-up credits**
   (mínimo US$10 + 5%). Dejar **desactivada** la recarga automática.
   Alternativa: BYOK con una clave de TypeSafe.
2. En `.env` o `data/.env` (ambos en `.gitignore`; ver [`.env.example`](../.env.example)):
   ```
   CLOUDFLARE_ACCOUNT_ID=...
   CLOUDFLARE_API_TOKEN=...   # permiso Account → Workers AI
   JEV_ENABLED=1
   ```
3. Probar:
   ```bash
   python -m agent.decisions --model jev --one   # una frase: confirma saldo y credenciales
   python -m agent.decisions                     # 5 frases ES/PT con la cadena por defecto
   ```

## Tests

Sin red (el transporte HTTP se reemplaza por uno falso):

```bash
python -m unittest discover -s agent/tests -t .
```
