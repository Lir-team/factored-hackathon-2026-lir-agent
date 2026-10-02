"""Deterministic ES/PT keyword baseline.

The baseline that Jev, Laya and the LLM are evaluated against. It only knows the
questions registered in KEYWORDS; any other question raises DecisionError.
"""

import time
import unicodedata
from collections.abc import Mapping

from decision_layer.base import Answer, Choice, DecisionError, DecisionResult, Noul, Question

# question -> option -> keywords (lowercase, no accents). For Noul the option is "true".
KEYWORDS: dict[str, dict[str, list[str]]] = {
    "intencion": {
        "cargo_no_reconocido": [
            "no reconozco", "no hice", "nunca fui", "no fui yo", "cobro raro",
            "nao reconheco", "nao fiz", "compra estranha", "nao fui eu",
        ],
        "cobro_indebido": [
            "dos veces", "doble", "comision", "cobraron de mas", "monto incorrecto",
            "duas vezes", "cobrado a mais", "taxa indevida", "tarifa",
        ],
        "consulta_movimiento": [
            "saldo", "movimiento", "estado del pago", "cuando se acredita",
            "extrato", "movimentacao", "status do pagamento",
        ],
        "otra_queja": [
            "mala atencion", "sucursal", "la app no",
            "pessimo atendimento", "agencia", "o aplicativo nao",
        ],
        "fuera_de_alcance": [
            "cupo", "credito", "prestamo", "abrir una cuenta",
            "limite", "emprestimo", "abrir uma conta",
        ],
    },
    "pide_humano": {
        "true": [
            "persona", "humano", "asesor", "agente", "operador",
            "pessoa", "atendente", "falar com alguem",
        ]
    },
    "sospecha_robo": {
        "true": [
            "robaron", "robo", "clonaron", "clonada", "hackearon",
            "roubaram", "roubo", "clonaram", "clonado", "hackearam",
        ]
    },
    # Confirmation of a pending dispute (asked by adk-agent). Explicit phrases only: a bare "sí"
    # cannot be matched safely by substring, so the baseline misses it and the agent asks again.
    "confirma": {
        "true": [
            "confirmo", "confirmado", "dale", "adelante", "de acuerdo", "hazlo",
            "abre la disputa", "procede", "isso mesmo", "pode abrir", "pode seguir",
        ]
    },
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class KeywordDecisionModel:
    name = "keywords-v1"

    def decide(self, state: str, questions: Mapping[str, Question]) -> DecisionResult:
        t0 = time.perf_counter()
        text = normalize(state)
        answers = {}
        for key, q in questions.items():
            rules = KEYWORDS.get(key)
            if rules is None:
                raise DecisionError(f"The baseline has no rules for {key!r}")
            if isinstance(q, Noul):
                hit = any(k in text for k in rules["true"])
                answers[key] = Answer(value=hit, probability=1.0 if hit else 0.0)
            elif isinstance(q, Choice):
                answers[key] = _choice(text, q, rules)
        return DecisionResult(
            answers=answers, model=self.name, latency_ms=(time.perf_counter() - t0) * 1000
        )


def _choice(text: str, q: Choice, rules: Mapping[str, list[str]]) -> Answer:
    hits = {opt: sum(k in text for k in rules.get(opt, [])) for opt in q.options}
    total = sum(hits.values())
    if total == 0:
        # No signal: uniform distribution. Low confidence forces a clarification or a handoff.
        probs = {opt: 1 / len(q.options) for opt in q.options}
    else:
        probs = {opt: h / total for opt, h in hits.items()}
    best = max(probs, key=probs.__getitem__)
    return Answer(value=best, probability=probs[best], probabilities=probs)
