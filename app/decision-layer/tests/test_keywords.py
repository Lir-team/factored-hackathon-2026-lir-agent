"""Tests for the keyword baseline."""

import pytest

from decision_layer import DecisionError, KeywordDecisionModel, Noul
from decision_layer.questions import TURN_QUESTIONS


@pytest.fixture
def model():
    return KeywordDecisionModel()


def test_spanish_unrecognized_charge_with_theft(model):
    result = model.decide("No reconozco este cobro, me clonaron la tarjeta", TURN_QUESTIONS)

    assert result.answers["intencion"].value == "cargo_no_reconocido"
    assert result.answers["sospecha_robo"].value is True


def test_portuguese_request_for_a_human(model):
    result = model.decide("Quero falar com um atendente", TURN_QUESTIONS)

    assert result.answers["pide_humano"].value is True


def test_accents_are_ignored(model):
    result = model.decide("Não reconheço essa compra", TURN_QUESTIONS)

    assert result.answers["intencion"].value == "cargo_no_reconocido"


def test_no_signal_gives_uniform_low_confidence(model):
    result = model.decide("hola buenas tardes", TURN_QUESTIONS)

    assert result.answers["intencion"].probability == pytest.approx(1 / 5)


def test_unknown_question_is_an_error(model):
    with pytest.raises(DecisionError):
        model.decide("hola", {"otra": Noul("¿?", true="sí", false="no")})
