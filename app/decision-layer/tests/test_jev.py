"""Tests for the Jev client."""

import copy

import pytest
from conftest import BILLING, JEV_OK

from decision_layer import DecisionError, JevAuthError, JevBillingError
from decision_layer.jev import to_wire
from decision_layer.questions import TURN_QUESTIONS, d4_merchant


def test_parses_typed_answers(jev_with):
    jev, _ = jev_with((200, JEV_OK))

    result = jev.decide("no reconozco un cargo", TURN_QUESTIONS)

    assert result.answers["intencion"].value == "cargo_no_reconocido"
    assert result.answers["intencion"].probability == pytest.approx(0.82)
    assert result.answers["pide_humano"].value is False
    assert result.answers["sospecha_robo"].value is True
    assert result.input_tokens == 426
    assert result.model == "jev-1.13.0"


def test_request_follows_jev_payload(jev_with):
    jev, transport = jev_with((200, JEV_OK))

    jev.decide("hola", TURN_QUESTIONS)

    sent = transport.calls[0]
    assert sent["model"] == "typesafe/jev"
    assert sent["input"]["state"] == "hola"
    assert sent["input"]["questions"]["pide_humano"]["type"] == "noul"
    assert "fuera_de_alcance" in sent["input"]["questions"]["intencion"]["criteria"]


def test_billing_error_is_not_retried(jev_with):
    jev, transport = jev_with((402, BILLING))

    with pytest.raises(JevBillingError, match="Insufficient balance"):
        jev.decide("hola", TURN_QUESTIONS)
    assert len(transport.calls) == 1


def test_auth_error_is_not_retried(jev_with):
    jev, transport = jev_with((401, {"errors": [{"code": 10000, "message": "Auth error"}]}))

    with pytest.raises(JevAuthError):
        jev.decide("hola", TURN_QUESTIONS)
    assert len(transport.calls) == 1


def test_retries_are_bounded_and_can_recover(jev_with):
    jev, transport = jev_with((503, {}), (503, {}), (200, JEV_OK))

    result = jev.decide("hola", TURN_QUESTIONS)

    assert len(transport.calls) == 3
    assert result.model == "jev-1.13.0"


def test_gives_up_after_max_attempts(jev_with):
    jev, transport = jev_with((503, {}), (503, {}), (503, {}))

    with pytest.raises(DecisionError, match="after 3 attempts"):
        jev.decide("hola", TURN_QUESTIONS)
    assert len(transport.calls) == 3


def test_unknown_option_is_an_error(jev_with):
    bad = copy.deepcopy(JEV_OK)
    bad["result"]["answers"]["intencion"]["choice"] = "aprobar_credito"
    jev, _ = jev_with((200, bad))

    with pytest.raises(DecisionError, match="unknown option"):
        jev.decide("hola", TURN_QUESTIONS)


def test_missing_answer_is_an_error(jev_with):
    partial = copy.deepcopy(JEV_OK)
    del partial["result"]["answers"]["sospecha_robo"]
    jev, _ = jev_with((200, partial))

    with pytest.raises(DecisionError, match="missing"):
        jev.decide("hola", TURN_QUESTIONS)


def test_d4_sends_only_merchant_names():
    wire = to_wire({"comercio": d4_merchant({"c1": "Supermercado Norte (Food)"})})

    assert set(wire["comercio"]["criteria"]) == {"c1", "ninguno"}
