"""Metrics for the typed decisions (EVAL-01). Pure functions over plain lists: no I/O.

Bases §4 asks to justify representations, metrics, thresholds and splits. Accuracy alone
hides what a threshold does, so next to macro-F1 every decision reports its calibration
(ECE) and its coverage/accuracy trade-off: how much can be decided automatically, and how
well, at each threshold.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval of a proportion k/n (sound for small n and rates near 0 or 1)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def cluster_interval(clusters: Sequence[Sequence[bool]], z: float = 1.96) -> tuple[float, float]:
    """95% interval of a pooled proportion when items come in correlated clusters.

    The paraphrases of one seed are not independent observations, so a Wilson interval over
    items is too narrow. This one is conservative: the pooled proportion with an effective
    sample size of one per cluster (seed). Unlike a bootstrap, it stays honest at 0% or 100%
    (4 seeds all right give [51-100], not [100-100]).
    """
    groups = [list(g) for g in clusters if g]
    if not groups:
        return (0.0, 1.0)
    n = len(groups)
    p = sum(sum(g) for g in groups) / sum(len(g) for g in groups)
    return wilson_p(p, n, z)


def wilson_p(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval for a proportion p observed on n units."""
    if n == 0:
        return (0.0, 1.0)
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class ClassScore:
    label: str
    precision: float
    recall: float
    f1: float
    support: int


def per_class(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str]
) -> list[ClassScore]:
    """Precision, recall and F1 per class. A missing prediction (None) is always wrong."""
    scores = []
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(y_true, y_pred, strict=True))
        fp = sum(t != label and p == label for t, p in zip(y_true, y_pred, strict=True))
        fn = sum(t == label and p != label for t, p in zip(y_true, y_pred, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores.append(ClassScore(label, precision, recall, f1, tp + fn))
    return scores


def macro_f1(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str]
) -> float:
    """Unweighted mean F1 over the classes: a rare class counts as much as a frequent one."""
    scores = per_class(y_true, y_pred, labels)
    return sum(s.f1 for s in scores) / len(scores) if scores else 0.0


def accuracy(y_true: Sequence[object], y_pred: Sequence[object]) -> float:
    pairs = list(zip(y_true, y_pred, strict=True))
    return sum(t == p for t, p in pairs) / len(pairs) if pairs else 0.0


def confusion(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Rows: true class; columns: predicted class (and "none" when the model failed)."""
    columns = [*labels, "none"]
    table = {t: dict.fromkeys(columns, 0) for t in labels}
    for t, p in zip(y_true, y_pred, strict=True):
        table[t][p if p in labels else "none"] += 1
    return table


def ece(confidences: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error: how far stated confidence is from observed accuracy.

    0 is perfectly calibrated. For a choice, the confidence is the chosen option's
    probability; for yes/no, it is the probability of the predicted answer.
    """
    n = len(confidences)
    if n == 0:
        return 0.0
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [
            i
            for i, c in enumerate(confidences)
            if lo < c <= hi or (b == 0 and c == 0.0)
        ]
        if not idx:
            continue
        mean_conf = sum(confidences[i] for i in idx) / len(idx)
        mean_acc = sum(correct[i] for i in idx) / len(idx)
        total += len(idx) / n * abs(mean_conf - mean_acc)
    return total


def reliability(
    confidences: Sequence[float], correct: Sequence[bool], bins: int = 5
) -> list[tuple[str, int, float, float]]:
    """(bin, n, mean confidence, accuracy) per non-empty bin: the reliability diagram as a table."""
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [
            i for i, c in enumerate(confidences) if lo < c <= hi or (b == 0 and c == 0.0)
        ]
        if idx:
            rows.append(
                (
                    f"{lo:.1f}-{hi:.1f}",
                    len(idx),
                    sum(confidences[i] for i in idx) / len(idx),
                    sum(correct[i] for i in idx) / len(idx),
                )
            )
    return rows


@dataclass(frozen=True)
class CoveragePoint:
    threshold: float
    coverage: float  # share decided automatically (confidence >= threshold)
    accuracy: float  # accuracy among those decided
    decided: int


def coverage_curve(
    confidences: Sequence[float],
    correct: Sequence[bool],
    thresholds: Sequence[float] = tuple(t / 20 for t in range(0, 20)),
) -> list[CoveragePoint]:
    """Selective accuracy: below the threshold the agent asks instead of deciding."""
    n = len(confidences)
    points = []
    for t in thresholds:
        idx = [i for i, c in enumerate(confidences) if c >= t]
        acc = sum(correct[i] for i in idx) / len(idx) if idx else 1.0
        points.append(CoveragePoint(t, len(idx) / n if n else 0.0, acc, len(idx)))
    return points


def pick_selective_threshold(
    curve: Sequence[CoveragePoint], target_accuracy: float
) -> CoveragePoint | None:
    """The lowest threshold whose selective accuracy meets the target (most coverage)."""
    meeting = [p for p in curve if p.decided and p.accuracy >= target_accuracy]
    return min(meeting, key=lambda p: p.threshold) if meeting else None


@dataclass(frozen=True)
class BinaryScore:
    threshold: float
    precision: float
    recall: float
    f1: float
    positives: int
    tp: int


def binary_at(
    y_true: Sequence[bool], prob_yes: Sequence[float | None], threshold: float
) -> BinaryScore:
    """Yes/no metrics when "yes" means probability >= threshold. A failure (None) is "no"."""
    pred = [p is not None and p >= threshold for p in prob_yes]
    tp = sum(t and p for t, p in zip(y_true, pred, strict=True))
    fp = sum((not t) and p for t, p in zip(y_true, pred, strict=True))
    fn = sum(t and not p for t, p in zip(y_true, pred, strict=True))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return BinaryScore(threshold, precision, recall, f1, tp + fn, tp)


def pick_recall_threshold(
    y_true: Sequence[bool],
    prob_yes: Sequence[float | None],
    min_recall: float,
    thresholds: Sequence[float] = tuple(t / 20 for t in range(1, 20)),
) -> BinaryScore:
    """The highest threshold that still catches `min_recall` of the positives.

    Asymmetric costs (proposal §5.2): missing a customer who asks for a person, or who was
    robbed, is worse than routing one too many to a human. Falls back to the lowest
    threshold when none reaches the recall.
    """
    scores = [binary_at(y_true, prob_yes, t) for t in thresholds]
    meeting = [s for s in scores if s.recall >= min_recall]
    return max(meeting, key=lambda s: s.threshold) if meeting else scores[0]


def recall_range(
    y_true: Sequence[bool],
    prob_yes: Sequence[float | None],
    min_recall: float,
    thresholds: Sequence[float] = tuple(t / 20 for t in range(1, 20)),
) -> tuple[float, float] | None:
    """The thresholds that keep `min_recall`, as (lowest, highest); None if none does.

    A range, not a point: when a model separates the classes perfectly on validation, any
    point inside is as good, and the edges are the least robust choice.
    """
    meeting = [t for t in thresholds if binary_at(y_true, prob_yes, t).recall >= min_recall]
    return (min(meeting), max(meeting)) if meeting else None
