"""Tests for D13 — reporting-scale inference and normalisation.

The bug these guard against was invisible to the previous test suite because its
fixtures built the extraction *from* the ground-truth values, so the two sides were
trivially in the same units. Every test here deliberately puts the extraction in
DIFFERENT units from the ground truth — that is the whole point.
"""

import pytest

from semantic_firewall.evaluation.benchmark_compare import compute_metrics
from semantic_firewall.evaluation.xbrl_ground_truth import score_extraction
from semantic_firewall.extraction.scale import (
    classify_error,
    eligible_for_scoring,
    infer_document_scale,
    to_absolute,
)

# Apple FY2023 as filed: the income statement prints 383,285 under a header that
# says "(In millions...)"; XBRL tags the same fact as 383285000000.
APPLE_HEADER = (
    "CONSOLIDATED STATEMENTS OF OPERATIONS\n"
    "(In millions, except number of shares which are reflected in thousands "
    "and per share amounts)\n"
    "Net sales:\n  Products . . . . . . 298,085\n  Services . . . . . . 85,200\n"
    "Total net sales . . . . 383,285\n"
)
APPLE_TRUTH = 383_285_000_000.0


# ── Inference ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("(In millions, except per share amounts)", 1e6),
    ("(In thousands)", 1e3),
    ("($ in millions)", 1e6),
    ("Dollars in millions", 1e6),
    ("amounts in thousands", 1e3),
    ("(In billions)", 1e9),
    ("en milliers d'euros", 1e3),
    ("en millions d’euros", 1e6),   # typographic apostrophe
])
def test_scale_is_inferred_from_common_statement_headers(header, expected):
    assert infer_document_scale(header)["multiplier"] == expected


def test_absent_scale_is_reported_not_guessed():
    """No scale statement must not silently become "units" for scoring purposes."""
    sc = infer_document_scale("Total net sales 383,285\nOperating income 114,301\n")
    assert sc["multiplier"] == 1.0
    assert sc["confident"] is False
    assert eligible_for_scoring(sc) is False


def test_evidence_is_recorded_so_normalisation_is_auditable():
    sc = infer_document_scale(APPLE_HEADER)
    assert sc["confident"] is True
    assert "million" in sc["evidence"].lower()
    assert sc["position"] is not None


def test_earliest_match_wins_over_later_prose():
    """A table header must not be overridden by a stray phrase further down."""
    text = "(In thousands)\n" + "x" * 500 + "\nrevenues in millions of dollars grew\n"
    assert infer_document_scale(text)["multiplier"] == 1e3


# ── The regression itself ─────────────────────────────────────────────────────

def test_millions_extraction_matches_absolute_xbrl_after_normalisation():
    """The D13 regression: 383285 vs 383285000000 must score CORRECT, not wrong."""
    scale = infer_document_scale(APPLE_HEADER)
    res = score_extraction({"chiffre_affaires": "383285"},
                           {"chiffre_affaires": {"val": APPLE_TRUTH}},
                           scale=scale)["chiffre_affaires"]
    assert res["within_tol"] is True
    assert res["error_kind"] == "correct"
    assert res["extracted_raw"] == 383_285.0        # as printed in the filing
    assert res["extracted_abs"] == APPLE_TRUTH      # normalised for comparison


def test_without_scale_the_same_extraction_is_counted_wrong():
    """Documents the failure mode explicitly, so it cannot silently come back."""
    res = score_extraction({"chiffre_affaires": "383285"},
                           {"chiffre_affaires": {"val": APPLE_TRUTH}})["chiffre_affaires"]
    assert res["within_tol"] is False
    assert res["error_kind"] == "scale"   # classified as a unit error, not a misread


def test_compute_metrics_accuracy_is_100_with_scale_and_0_without():
    gt = {"chiffre_affaires": {"val": APPLE_TRUTH}}
    fields = {"chiffre_affaires": "383285"}
    with_scale = compute_metrics(fields, gt, scale=infer_document_scale(APPLE_HEADER))
    without = compute_metrics(fields, gt)
    assert with_scale["field_accuracy_pct"] == 100.0
    assert without["field_accuracy_pct"] == 0.0
    assert without["error_kinds"] == {"scale": 1}


def test_thousands_filing_normalises_too():
    header = "CONSOLIDATED STATEMENTS OF INCOME\n(In thousands)\nRevenue 1,234,567\n"
    res = score_extraction({"chiffre_affaires": "1234567"},
                           {"chiffre_affaires": {"val": 1_234_567_000.0}},
                           scale=infer_document_scale(header))["chiffre_affaires"]
    assert res["within_tol"] is True


# ── Error taxonomy ────────────────────────────────────────────────────────────

def test_classify_error_separates_scale_from_a_genuinely_wrong_figure():
    truth = 1_000_000.0
    assert classify_error(1_000_000.0, truth) == "correct"
    assert classify_error(1_000.0, truth) == "scale"        # off by exactly 1e3
    assert classify_error(-1_000_000.0, truth) == "sign"    # loss vs profit
    assert classify_error(0.0, truth) == "missing"
    assert classify_error(742_318.0, truth) == "wrong_value"  # wrong line/segment


def test_scale_error_is_invisible_to_the_ebitda_identity():
    """Why scale errors are excluded from the detector's label by default.

    Reporting every figure in thousands instead of millions leaves EBITDA = EBIT + D&A
    exactly satisfied, so no accounting identity can possibly catch it.
    """
    from semantic_firewall.evaluation.detector import identity_residuals
    scaled_down = {"ebit": "100", "dotations_amortissements": "20", "ebitda": "120"}
    assert identity_residuals(scaled_down, "compte_resultat")["ebitda_identity"] == 0.0


def test_to_absolute_is_a_pure_multiply():
    assert to_absolute(5.0, {"multiplier": 1e6}) == 5_000_000.0
    assert to_absolute(5.0, {}) == 5.0
