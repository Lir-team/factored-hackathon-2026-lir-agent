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


CONFIRMA = {"confirma": Noul("Does the customer agree?", true="Yes", false="No")}


@pytest.mark.parametrize(
    "reply",
    [
        "Sí, por favor.", "si", "Sim, pode ser", "Claro", "ok, gracias", "Sí, confirmo", "dale",
        "Sí, por favor. Quiero disputar uno de los dos cargos.",
        "Quero contestar essa cobrança",
    ],
)
def test_a_short_plain_yes_confirms(model, reply):
    assert model.decide(reply, CONFIRMA).answers["confirma"].value is True


@pytest.mark.parametrize(
    "reply",
    [
        "Sí, pero espera",
        "No sé si quiero",
        "si quieres revisalo",
        "Déjame pensarlo, todavía no hagas nada",
        "no",
        "Sim, mas ainda não",
        "Sí, ¿y cuánto tarda?",
        "No quiero disputar nada",
        "Quiero disputarlo, pero espera a mañana",
        "Confirmo que no quiero abrir la disputa",
    ],
)
def test_a_yes_with_anything_else_does_not_confirm(model, reply):
    assert model.decide(reply, CONFIRMA).answers["confirma"].value is False


REJECTS = {
    "rechaza_explicacion": Noul(
        "Does the customer still reject the charge?", true="Yes", false="No"
    )
}


@pytest.mark.parametrize(
    "reply",
    [
        "Igual no lo reconozco, yo nunca compré ahí",
        "No estoy de acuerdo, quiero disputar ese cargo",
        "Continuo sem reconhecer, não fui eu",
    ],
)
def test_a_customer_who_still_rejects_the_charge(model, reply):
    assert model.decide(reply, REJECTS).answers["rechaza_explicacion"].value is True


@pytest.mark.parametrize("reply", ["Ah, ya me acordé, gracias", "Ok, era mi suscripción"])
def test_a_customer_who_accepts_the_explanation(model, reply):
    assert model.decide(reply, REJECTS).answers["rechaza_explicacion"].value is False
