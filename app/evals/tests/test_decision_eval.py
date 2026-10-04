"""EVAL-01: the metrics and the leak-free split of the typed-decision evaluation."""

import pytest

from decision_eval import metrics as m
from decision_eval.dataset import INTENTS, TEST, VALIDATION, load
from decision_eval.report import cohen_kappa


def test_the_split_never_separates_paraphrases_and_keeps_every_class():
    items = load()
    split_by_seed: dict[str, set[str]] = {}
    for item in items:
        split_by_seed.setdefault(item.seed_id, set()).add(item.split)
    assert all(len(splits) == 1 for splits in split_by_seed.values())
    for split in (VALIDATION, TEST):
        assert {i.intent for i in items if i.split == split} == set(INTENTS)


def test_the_set_is_large_enough_and_a_third_portuguese():
    items = load()
    assert len(items) >= 300
    assert sum(i.language in ("pt", "mixed") for i in items) / len(items) >= 0.30


def test_macro_f1_weights_classes_equally():
    y_true = ["a", "a", "a", "b"]
    y_pred = ["a", "a", "a", "a"]  # the rare class is never found
    assert m.accuracy(y_true, y_pred) == 0.75
    assert m.macro_f1(y_true, y_pred, ["a", "b"]) == pytest.approx((6 / 7 + 0) / 2)


def test_a_failed_prediction_counts_as_wrong():
    assert m.macro_f1(["a", "b"], [None, "b"], ["a", "b"]) == pytest.approx(0.5)
    assert m.confusion(["a"], [None], ["a"])["a"]["none"] == 1


def test_ece_is_zero_when_confidence_matches_accuracy_and_large_when_overconfident():
    assert m.ece([0.75] * 4, [True, True, True, False]) == pytest.approx(0.0)
    assert m.ece([1.0] * 4, [True, False, False, False]) == pytest.approx(0.75)


def test_the_selective_threshold_is_the_most_coverage_that_meets_the_target():
    conf = [0.9, 0.8, 0.6, 0.4]
    correct = [True, True, False, True]
    curve = m.coverage_curve(conf, correct, [0.0, 0.5, 0.7])
    chosen = m.pick_selective_threshold(curve, 0.95)
    assert chosen is not None and (chosen.threshold, chosen.coverage) == (0.7, 0.5)
    assert m.pick_selective_threshold(curve, 1.01) is None


def test_the_recall_threshold_keeps_the_positives():
    y_true = [True, True, False, False]
    p_yes = [0.9, 0.35, 0.4, 0.1]
    chosen = m.pick_recall_threshold(y_true, p_yes, 1.0, [0.2, 0.3, 0.5])
    assert chosen.threshold == 0.3 and chosen.recall == 1.0
    assert m.binary_at(y_true, [None, 0.9, 0.0, 0.0], 0.5).recall == 0.5  # failure = "no"


def test_wilson_interval_stays_inside_zero_and_one():
    low, high = m.wilson(10, 10)
    assert 0.6 < low < 1.0 and high == pytest.approx(1.0)
    assert m.wilson(0, 0) == (0.0, 1.0)


def test_kappa_discounts_chance_agreement():
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == pytest.approx(1.0)
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)


def test_the_recall_range_is_every_threshold_that_keeps_the_positives():
    y_true = [True, True, False]
    p_yes = [0.9, 0.6, 0.2]
    assert m.recall_range(y_true, p_yes, 1.0, [0.1, 0.3, 0.5, 0.7]) == (0.1, 0.5)
    assert m.recall_range(y_true, [0.1, 0.1, 0.1], 1.0, [0.5]) is None
