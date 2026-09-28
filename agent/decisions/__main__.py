"""Smoke test: `python -m agent.decisions [--model jev|keywords|default] [--one]`.

Frases inventadas por el equipo (sin datos de clientes).
"""
from __future__ import annotations

import argparse
import sys

from . import DecisionError, KeywordDecisionModel, build_default, jev_from_env
from .questions import TURN_QUESTIONS

CASES = [
    ("es-AR", "Che, me aparece un cobro raro de un súper que nunca fui, ¿qué onda?"),
    ("es-MX", "Me cobraron dos veces la misma compra en Oxxo, quiero que me regresen una"),
    ("pt-BR", "Tem uma compra no meu cartão que eu não fiz, acho que clonaram meu cartão"),
    ("pt-BR", "Quero falar com um atendente agora, por favor"),
    ("es-CO", "Quiero que me suban el cupo de la tarjeta de crédito"),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["jev", "keywords", "default"], default="default")
    ap.add_argument("--one", action="store_true", help="solo la primera frase")
    args = ap.parse_args()

    if args.model == "jev":
        model = jev_from_env()
        if model is None:
            print("Faltan CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN (ver agent/README.md)")
            return 1
    elif args.model == "keywords":
        model = KeywordDecisionModel()
    else:
        model = build_default()

    print(f"modelo: {model.name}")
    for lang, text in CASES[:1] if args.one else CASES:
        print(f"\n[{lang}] {text}")
        try:
            r = model.decide(text, TURN_QUESTIONS)
        except DecisionError as e:
            print(f"  ERROR ({type(e).__name__}): {e}")
            return 2
        print(f"  {r.model}  {r.latency_ms:.0f} ms  tokens={r.input_tokens}")
        for key, a in r.answers.items():
            print(f"  {key}: {a.value}  p={a.probability:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
