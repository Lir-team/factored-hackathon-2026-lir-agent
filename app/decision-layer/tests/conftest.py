"""Shared fakes: no test touches the network."""

import json

import pytest

from decision_layer import JevClient

# Response shape from https://developers.cloudflare.com/ai/models/typesafe/jev/
JEV_OK = {
    "result": {
        "model": "jev-1.13.0",
        "answers": {
            "intencion": {
                "type": "choice",
                "choice": "cargo_no_reconocido",
                "confidence": 0.8,
                "probabilities": {
                    "cargo_no_reconocido": 0.82,
                    "cobro_indebido": 0.1,
                    "consulta_movimiento": 0.04,
                    "otra_queja": 0.02,
                    "fuera_de_alcance": 0.02,
                },
            },
            "pide_humano": {"type": "noul", "noul": 0.07},
            "sospecha_robo": {"type": "noul", "noul": 0.91},
        },
        "usage": {"input_tokens": 426, "output_tokens": 73},
    },
    "success": True,
}

BILLING = {
    "errors": [
        {"message": "Insufficient balance; add money to your gateway or use BYOK", "code": 2021}
    ],
    "success": False,
}


class FakeTransport:
    """Returns the queued (status, payload) responses and records each request body."""

    def __init__(self, *responses: tuple[int, dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


@pytest.fixture
def jev_with():
    def make(*responses: tuple[int, dict]) -> tuple[JevClient, FakeTransport]:
        transport = FakeTransport(*responses)
        return JevClient("acc", "tok", transport=transport, backoff_s=0), transport

    return make
