"""
D2 regression tests: the benchmark metric must measure CORRECTNESS against ground
truth, not the old non-null "recall", and must never invent a number without GT.
Offline — uses an inline ground-truth dict (no network).
"""

from semantic_firewall.evaluation.benchmark_compare import compute_metrics

# Minimal XBRL-shaped ground truth ({field: {"val": ...}}).
GT = {
    "chiffre_affaires":         {"val": 1000.0},
    "ebit":                     {"val": 200.0},
    "ebitda":                   {"val": 260.0},
    "dotations_amortissements": {"val": 60.0},
    "resultat_net":             {"val": 150.0},
}


def test_perfect_extraction_scores_100():
    fields = {k: v["val"] for k, v in GT.items()}
    m = compute_metrics(fields, GT)
    assert m["field_accuracy_pct"] == 100.0
    assert m["fields_correct"] == m["fields_scored"] == 5


def test_wrong_value_is_penalised_and_differs_from_coverage():
    fields = {k: v["val"] for k, v in GT.items()}
    fields["chiffre_affaires"] = 1100.0     # +10% → wrong
    m = compute_metrics(fields, GT)
    assert m["field_accuracy_pct"] == 80.0  # 4/5 correct
    assert m["coverage_pct"] == 100.0       # still fully "covered" — the old lie


def test_non_null_but_wrong_is_not_credited():
    # The old recall would have scored this 100% (all fields non-null); accuracy must not.
    fields = {k: v["val"] * 2 for k, v in GT.items()}  # every value doubled
    m = compute_metrics(fields, GT)
    assert m["coverage_pct"] == 100.0
    assert m["field_accuracy_pct"] == 0.0


def test_no_ground_truth_yields_none_not_a_fake_number():
    fields = {k: v["val"] for k, v in GT.items()}
    m = compute_metrics(fields, None)
    assert m["field_accuracy_pct"] is None
    assert m["coverage_pct"] == 100.0       # coverage is still computable


def test_tolerance_is_explicit_and_applied():
    fields = {k: v["val"] for k, v in GT.items()}
    fields["ebit"] = 200.0 * 1.05           # +5%
    # within a 10% tolerance → correct; within 1% → wrong. (Above the ±1.0 floor.)
    assert compute_metrics(fields, GT, rel_tol=0.10)["field_accuracy_pct"] == 100.0
    assert compute_metrics(fields, GT, rel_tol=0.01)["field_accuracy_pct"] == 80.0


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
    sys.exit(0)
