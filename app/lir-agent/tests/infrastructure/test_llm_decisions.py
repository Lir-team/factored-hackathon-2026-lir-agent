"""LLM decision model: typed questions in, validated answers out; any doubt is a DecisionError."""

import json
from types import SimpleNamespace

import pytest
from decision_layer import ChainDecisionModel, DecisionError, KeywordDecisionModel
from decision_layer.questions import TURN_QUESTIONS

from lir_agent.infrastructure.decisions import LlmDecisionModel

GOOD = {
    "intencion": {"choice": "cargo_no_reconocido", "probabilities": {
        "cargo_no_reconocido": 0.9, "cobro_indebido": 0.05, "consulta_movimiento": 0.03,
        "otra_queja": 0.01, "fuera_de_alcance": 0.01}},
    "pide_humano": {"probability": 0.1},
    "sospecha_robo": {"probability": 0.8},
}


def fake_completion(payload, calls=None):
    def completion(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        message = SimpleNamespace(content=content)
        usage = SimpleNamespace(prompt_tokens=321)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    return completion


def model(payload, calls=None) -> LlmDecisionModel:
    return LlmDecisionModel(model="openai/test", completion=fake_completion(payload, calls))


def test_valid_answer_is_parsed():
    result = model(GOOD).decide("No reconozco un cargo, creo que me clonaron", TURN_QUESTIONS)
    assert result.answers["intencion"].value == "cargo_no_reconocido"
    assert result.answers["intencion"].probability == pytest.approx(0.9)
    assert result.answers["sospecha_robo"].value is True
    assert result.answers["pide_humano"].value is False
    assert result.input_tokens == 321
    assert result.model == "llm:openai/test"


def test_only_the_customer_text_and_question_texts_are_sent():
    calls = []
    model(GOOD, calls).decide("No reconozco un cargo", TURN_QUESTIONS)
    sent = json.dumps(calls[0]["messages"], ensure_ascii=False)
    assert "No reconozco un cargo" in sent
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        {k: v for k, v in GOOD.items() if k != "pide_humano"},  # a question unanswered
        {**GOOD, "intencion": {"choice": "inventada", "probabilities": {}}},  # unknown option
        {**GOOD, "sospecha_robo": {"probability": 1.7}},  # out of range
    ],
)
def test_any_doubtful_answer_is_a_decision_error(payload):
    with pytest.raises(DecisionError):
        model(payload).decide("hola", TURN_QUESTIONS)


def test_provider_failure_is_a_decision_error():
    def broken(**_):
        raise TimeoutError("slow")

    with pytest.raises(DecisionError):
        LlmDecisionModel(model="openai/test", completion=broken).decide("hola", TURN_QUESTIONS)


def test_chain_falls_back_to_the_baseline():
    chain = ChainDecisionModel([model("not json"), KeywordDecisionModel()])
    result = chain.decide("Quero falar com um atendente", TURN_QUESTIONS)
    assert result.model == "keywords-v1"
    assert result.answers["pide_humano"].value is True


def test_reasoning_effort_is_passed_only_when_set():
    calls = []
    LlmDecisionModel("openai/test", completion=fake_completion(GOOD, calls)).decide("hola", TURN_QUESTIONS)
    LlmDecisionModel(
        "openai/test", reasoning_effort="none", completion=fake_completion(GOOD, calls)
    ).decide("hola", TURN_QUESTIONS)
    assert "reasoning_effort" not in calls[0]
    assert calls[1]["reasoning_effort"] == "none"
    assert "temperature" not in calls[0]  # reasoning models reject a forced temperature
