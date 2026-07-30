"""
Tests for the E1 detector residuals and the numpy metrics (ROC-AUC / PR-AUC /
precision@k / ECE), including behaviour under ties and degenerate label sets.
"""

import math

from semantic_firewall.evaluation import metrics
from semantic_firewall.evaluation.detector import (
    document_anomaly_score,
    identity_residuals,
)


def test_residual_zero_when_identity_holds():
    ok = {"ebit": "960000", "dotations_amortissements": "320000", "ebitda": "1280000"}
    r = identity_residuals(ok, "compte_resultat")
    assert r["ebitda_identity"] < 1e-9


def test_residual_positive_when_violated():
    bad = {"ebit": "960000", "dotations_amortissements": "320000", "ebitda": "960000"}
    assert document_anomaly_score(bad, "compte_resultat") > 0.1


def test_roc_auc_perfect_and_random():
    assert metrics.roc_auc([0.1, 0.2, 0.9, 0.8], [0, 0, 1, 1]) == 1.0
    assert metrics.roc_auc([0.9, 0.8, 0.1, 0.2], [0, 0, 1, 1]) == 0.0
    # ties give 0.5
    assert abs(metrics.roc_auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) - 0.5) < 1e-9


def test_roc_auc_undefined_single_class():
    assert math.isnan(metrics.roc_auc([0.1, 0.2], [0, 0]))


def test_pr_auc_and_precision_at_k():
    s = [0.9, 0.8, 0.3, 0.2]
    y = [1, 1, 0, 0]
    assert abs(metrics.pr_auc(s, y) - 1.0) < 1e-9
    assert metrics.precision_at_k(s, y, 2) == 1.0


def test_confusion_and_ece():
    c = metrics.confusion([0.9, 0.1, 0.8, 0.2], [1, 0, 1, 0], threshold=0.5)
    assert c["tp"] == 2 and c["tn"] == 2 and c["fp"] == 0 and c["fn"] == 0
    assert c["f1"] == 1.0
    # perfectly calibrated -> ECE 0
    ece = metrics.expected_calibration_error([1.0, 1.0, 0.0, 0.0], [1, 1, 0, 0], n_bins=5)
    assert ece < 1e-9


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
    sys.exit(0)
