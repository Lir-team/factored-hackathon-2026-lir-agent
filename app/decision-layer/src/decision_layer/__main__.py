"""Smoke test: `uv run python -m decision_layer [--model jev|keywords|default] [--one]`.

The sentences are written by the team (no customer data).
"""

import argparse
import sys

from decision_layer import DecisionError, KeywordDecisionModel, build_default, jev_from_env
from decision_layer.questions import TURN_QUESTIONS

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["jev", "keywords", "default"], default="default")
    parser.add_argument("--one", action="store_true", help="only the first sentence")
    args = parser.parse_args()

    if args.model == "jev":
        model = jev_from_env()
        if model is None:
            print("Missing CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN (see README.md)")
            return 1
    elif args.model == "keywords":
        model = KeywordDecisionModel()
    else:
        model = build_default()

    print(f"model: {model.name}")
    for lang, text in CASES[:1] if args.one else CASES:
        print(f"\n[{lang}] {text}")
        try:
            result = model.decide(text, TURN_QUESTIONS)
        except DecisionError as e:
            print(f"  ERROR ({type(e).__name__}): {e}")
            return 2
        print(f"  {result.model}  {result.latency_ms:.0f} ms  tokens={result.input_tokens}")
        for key, answer in result.answers.items():
            print(f"  {key}: {answer.value}  p={answer.probability:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
