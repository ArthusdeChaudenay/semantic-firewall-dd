"""
metrics.py — Evaluation metrics for the unsupervised error detector (E1).

The research question (audit E1) is: *does an accounting-identity violation predict
that an extracted value is wrong?* Answering it requires ranking metrics against
XBRL-derived error labels, not the old pass-rate. All implemented in numpy so the
package needs no scikit-learn; if sklearn is installed the results match it.

  * roc_auc(scores, labels)      — area under ROC.
  * pr_auc(scores, labels)       — area under precision-recall (average precision).
  * precision_at_k(scores, labels, k)
  * expected_calibration_error(probs, labels, n_bins) — for score_confiance calibration.
  * confusion(scores, labels, threshold) — TP/FP/FN/TN + precision/recall/F1.

Convention: higher score = more likely POSITIVE (= erroneous / anomalous).
"""

from __future__ import annotations

import numpy as np


def _prep(scores, labels):
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if s.shape != y.shape:
        raise ValueError("scores and labels must have the same shape")
    return s, y


def roc_auc(scores, labels) -> float:
    s, y = _prep(scores, labels)
    P, N = int(y.sum()), int((y == 0).sum())
    if P == 0 or N == 0:
        return float("nan")  # undefined; caller must report the class imbalance
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    # average ranks for ties
    sorted_s = s[order]
    i = 0
    r = 1
    while i < len(sorted_s):
        j = i
        while j + 1 < len(sorted_s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        avg = (r + (r + (j - i))) / 2.0
        ranks[order[i:j + 1]] = avg
        r += (j - i + 1)
        i = j + 1
    sum_pos = ranks[y == 1].sum()
    return float((sum_pos - P * (P + 1) / 2) / (P * N))


def pr_auc(scores, labels) -> float:
    """Average precision (area under precision-recall curve)."""
    s, y = _prep(scores, labels)
    if y.sum() == 0:
        return float("nan")
    order = np.argsort(-s, kind="mergesort")
    y = y[order]
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / y.sum()
    # sum precision at each positive (step integration over recall)
    ap = 0.0
    prev_recall = 0.0
    for i in range(len(y)):
        if y[i] == 1:
            ap += precision[i] * (recall[i] - prev_recall)
            prev_recall = recall[i]
    return float(ap)


def precision_at_k(scores, labels, k: int) -> float:
    s, y = _prep(scores, labels)
    k = min(k, len(s))
    if k == 0:
        return float("nan")
    idx = np.argsort(-s, kind="mergesort")[:k]
    return float(y[idx].mean())


def expected_calibration_error(probs, labels, n_bins: int = 10) -> float:
    """ECE — how well a probability/confidence score is calibrated.

    Use with score_confiance rescaled to [0,1] and labels = "value is correct".
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=int)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        mask = (p > lo) & (p <= hi) if b > 0 else (p >= lo) & (p <= hi)
        if mask.sum() == 0:
            continue
        conf = p[mask].mean()
        acc = y[mask].mean()
        ece += (mask.sum() / len(p)) * abs(acc - conf)
    return float(ece)


def confusion(scores, labels, threshold: float) -> dict:
    s, y = _prep(scores, labels)
    pred = (s >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


def summarize(scores, labels, k: int = 10) -> dict:
    """Headline detector metrics in one call."""
    s, y = _prep(scores, labels)
    return {
        "n": int(len(y)),
        "n_positive": int(y.sum()),
        "roc_auc": roc_auc(s, y),
        "pr_auc": pr_auc(s, y),
        f"precision_at_{k}": precision_at_k(s, y, k),
    }
