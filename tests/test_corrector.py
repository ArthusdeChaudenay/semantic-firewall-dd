"""
Tests for the SEPARATED corrector (D1): idempotence, order-insensitivity,
loss-making firms, and the key architectural guarantee — the detector runs on the
RAW extraction, so the EBITDA identity is NOT auto-passed by the corrector.
"""

from semantic_firewall.validation.corrector import (
    INCOME_STATEMENT_RULES,
    apply_corrections,
)
from semantic_firewall.validation.dd_base import DDTaxonomy

_f = DDTaxonomy._f


def test_idempotent_fixed_point():
    flat = {"chiffre_affaires": "3200000", "ebit": "960000",
            "dotations_amortissements": "320000", "ebitda": "960000"}
    once, _ = apply_corrections(flat, "compte_resultat")
    twice, log2 = apply_corrections(once, "compte_resultat")
    assert once == twice          # re-running changes nothing
    assert log2 == []             # ...and applies no rule


def test_loss_making_firm_not_dropped():
    # Negative EBIT must still yield EBITDA = EBIT + D&A, not be ignored.
    flat = {"chiffre_affaires": "1000", "ebit": "-200",
            "dotations_amortissements": "50", "ebitda": ""}
    out, _ = apply_corrections(flat, "compte_resultat")
    assert abs(_f(out["ebitda"]) - (-150.0)) < 1e-6


def test_order_insensitivity_of_fixed_point():
    # Shuffle the rule registry; the fixed point must be identical.
    import random
    flat = {"chiffre_affaires": "3200000", "ebit": "300000",
            "dotations_amortissements": "700000", "ebitda": "500000"}
    baseline, _ = apply_corrections(flat, "compte_resultat")

    import semantic_firewall.validation.corrector as C
    original = list(C.INCOME_STATEMENT_RULES)
    try:
        rng = random.Random(0)
        shuffled = original[:]
        rng.shuffle(shuffled)
        C.INCOME_STATEMENT_RULES = shuffled
        reordered, _ = apply_corrections(flat, "compte_resultat")
    finally:
        C.INCOME_STATEMENT_RULES = original
    # Fixed point on the EBITDA identity is order-independent.
    assert abs(_f(reordered["ebitda"]) - _f(baseline["ebitda"])) < 1.0


def test_detector_is_measured_on_raw_not_after_correction():
    # The whole point of D1: a wrong raw EBITDA is a DETECTED violation, even though
    # the corrector could rewrite it. Detector on raw must FAIL here.
    raw = {"chiffre_affaires": "3200000", "ebit": "960000",
           "dotations_amortissements": "320000", "ebitda": "960000"}
    assert DDTaxonomy.check_ebitda_consistency(raw)["statut"] == "FAIL"
    corrected, log = apply_corrections(raw, "compte_resultat")
    assert DDTaxonomy.check_ebitda_consistency(corrected)["statut"] == "PASS"
    assert any(l["rule"] == "general_ebitda_identity" or l["field"] == "ebitda" for l in log)


def test_ablation_disabling_a_rule_changes_output():
    flat = {"chiffre_affaires": "3200000", "ebit": "", "ebitda": "1280000",
            "dotations_amortissements": "320000"}
    full, _ = apply_corrections(flat, "compte_resultat")
    ablated, _ = apply_corrections(flat, "compte_resultat",
                                   disabled_rules={"derive_ebit"})
    assert _f(full["ebit"]) != 0.0
    assert _f(ablated.get("ebit", "0")) == 0.0


def test_rule_registry_names_unique():
    names = [n for n, _ in INCOME_STATEMENT_RULES]
    assert len(names) == len(set(names))


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
    sys.exit(0)
