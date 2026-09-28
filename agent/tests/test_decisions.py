"""Tests sin red: `python -m unittest discover -s agent/tests -t .`"""
import json
import unittest

from agent.decisions import (
    ChainDecisionModel, DecisionError, JevBillingError, JevClient, KeywordDecisionModel,
)
from agent.decisions.jev import JevAuthError, to_wire
from agent.decisions.questions import TURN_QUESTIONS, d4_comercio

# Forma de la respuesta según https://developers.cloudflare.com/ai/models/typesafe/jev/
JEV_OK = {
    "result": {
        "model": "jev-1.13.0",
        "answers": {
            "intencion": {"type": "choice", "choice": "cargo_no_reconocido", "confidence": 0.8,
                          "probabilities": {"cargo_no_reconocido": 0.82, "cobro_indebido": 0.1,
                                            "consulta_movimiento": 0.04, "otra_queja": 0.02,
                                            "fuera_de_alcance": 0.02}},
            "pide_humano": {"type": "noul", "noul": 0.07},
            "sospecha_robo": {"type": "noul", "noul": 0.91},
        },
        "usage": {"input_tokens": 426, "output_tokens": 73},
    },
    "success": True,
}
BILLING = {"errors": [{"message": "Insufficient balance; add money to your gateway or use BYOK",
                       "code": 2021}], "success": False}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


def jev(transport):
    return JevClient("acc", "tok", transport=transport, backoff_s=0)


class JevClientTest(unittest.TestCase):
    def test_parsea_respuesta(self):
        r = jev(FakeTransport((200, JEV_OK))).decide("no reconozco un cargo", TURN_QUESTIONS)
        self.assertEqual(r.answers["intencion"].value, "cargo_no_reconocido")
        self.assertAlmostEqual(r.answers["intencion"].probability, 0.82)
        self.assertFalse(r.answers["pide_humano"].value)
        self.assertTrue(r.answers["sospecha_robo"].value)
        self.assertEqual(r.input_tokens, 426)

    def test_request_usa_formato_de_jev(self):
        t = FakeTransport((200, JEV_OK))
        jev(t).decide("hola", TURN_QUESTIONS)
        sent = t.calls[0]
        self.assertEqual(sent["model"], "typesafe/jev")
        self.assertEqual(sent["input"]["questions"]["pide_humano"]["type"], "noul")
        self.assertIn("fuera_de_alcance", sent["input"]["questions"]["intencion"]["criteria"])

    def test_402_no_reintenta(self):
        t = FakeTransport((402, BILLING))
        with self.assertRaises(JevBillingError):
            jev(t).decide("hola", TURN_QUESTIONS)
        self.assertEqual(len(t.calls), 1)

    def test_401_no_reintenta(self):
        t = FakeTransport((401, {"errors": [{"code": 10000, "message": "Authentication error"}]}))
        with self.assertRaises(JevAuthError):
            jev(t).decide("hola", TURN_QUESTIONS)
        self.assertEqual(len(t.calls), 1)

    def test_reintentos_acotados(self):
        t = FakeTransport((503, {}), (503, {}), (200, JEV_OK))
        r = jev(t).decide("hola", TURN_QUESTIONS)
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(r.model, "jev-1.13.0")

    def test_agota_reintentos(self):
        t = FakeTransport((503, {}), (503, {}), (503, {}))
        with self.assertRaises(DecisionError):
            jev(t).decide("hola", TURN_QUESTIONS)
        self.assertEqual(len(t.calls), 3)

    def test_opcion_desconocida_es_error(self):
        bad = json.loads(json.dumps(JEV_OK))
        bad["result"]["answers"]["intencion"]["choice"] = "aprobar_credito"
        with self.assertRaises(DecisionError):
            jev(FakeTransport((200, bad))).decide("hola", TURN_QUESTIONS)

    def test_d4_sin_numeros(self):
        wire = to_wire({"comercio": d4_comercio({"c1": "Supermercado Norte (Food)"})})
        self.assertEqual(set(wire["comercio"]["criteria"]), {"c1", "ninguno"})


class KeywordsTest(unittest.TestCase):
    def setUp(self):
        self.m = KeywordDecisionModel()

    def test_es_y_pt(self):
        es = self.m.decide("No reconozco este cobro, me clonaron la tarjeta", TURN_QUESTIONS)
        self.assertEqual(es.answers["intencion"].value, "cargo_no_reconocido")
        self.assertTrue(es.answers["sospecha_robo"].value)
        pt = self.m.decide("Quero falar com um atendente", TURN_QUESTIONS)
        self.assertTrue(pt.answers["pide_humano"].value)

    def test_sin_senal_confianza_baja(self):
        r = self.m.decide("hola buenas tardes", TURN_QUESTIONS)
        self.assertAlmostEqual(r.answers["intencion"].probability, 1 / 5)


class ChainTest(unittest.TestCase):
    def test_cae_al_baseline_si_jev_falla(self):
        chain = ChainDecisionModel([jev(FakeTransport((402, BILLING))), KeywordDecisionModel()])
        r = chain.decide("no reconozco un cargo", TURN_QUESTIONS)
        self.assertEqual(r.model, "keywords-v1")
        self.assertEqual(chain.failures[0][0], "jev")

    def test_todos_fallan(self):
        chain = ChainDecisionModel([jev(FakeTransport((402, BILLING)))])
        with self.assertRaises(DecisionError):
            chain.decide("hola", TURN_QUESTIONS)


if __name__ == "__main__":
    unittest.main()
