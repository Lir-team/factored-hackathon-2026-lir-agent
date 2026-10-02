"""Tests for the fallback chain."""

import pytest
from conftest import BILLING

from decision_layer import ChainDecisionModel, DecisionError, KeywordDecisionModel
from decision_layer.questions import TURN_QUESTIONS


def test_falls_back_to_baseline_when_jev_fails(jev_with):
    jev, _ = jev_with((402, BILLING))
    chain = ChainDecisionModel([jev, KeywordDecisionModel()])

    result = chain.decide("no reconozco un cargo", TURN_QUESTIONS)

    assert result.model == "keywords-v1"
    assert chain.failures[0][0] == "jev"


def test_raises_when_every_model_fails(jev_with):
    jev, _ = jev_with((402, BILLING))
    chain = ChainDecisionModel([jev])

    with pytest.raises(DecisionError, match="No model could decide"):
        chain.decide("hola", TURN_QUESTIONS)


def test_needs_at_least_one_model():
    with pytest.raises(ValueError):
        ChainDecisionModel([])
