"""Tests for D14 / D15 — ground-truth hygiene, offline (no network).

D14: ``nearest_fiscal_year`` resolves to the closest *available* annual period, so a
document labelled FY2026 can silently pick up FY2025 facts. A one-year offset on
revenue blows past every tolerance and looks exactly like an extraction error, so
such records must be excluded by default and the exclusion must be reported.

D15: XBRL only contains what the filer tagged. Banks do not tag
``OperatingIncomeLoss``; many filers disclose D&A only in the cash-flow statement.
The effective n therefore differs per field and must be published, or the evidence
behind the EBITDA identity is overstated.
"""

import json

from semantic_firewall.evaluation.benchmark_ground_truth import coverage_report, load

_RECORDS = {
    "apple": {
        "id": "apple", "ticker": "AAPL", "labelled_year": 2025, "chosen_fy": 2025,
        "year_mismatch": False, "year_offset": 0,
        "fields": {
            "chiffre_affaires": {"val": 4.16e11, "concept": "Revenues", "derived": False},
            "ebit": {"val": 1.33e11, "concept": "OperatingIncomeLoss", "derived": False},
            "dotations_amortissements": {"val": 1.17e10, "concept": "DDA", "derived": False},
            "ebitda": {"val": 1.45e11, "concept": "DERIVED(...)", "derived": True},
        },
    },
    "alphabet": {   # labelled 2026 but only FY2025 facts exist → must be excluded
        "id": "alphabet", "ticker": "GOOGL", "labelled_year": 2026, "chosen_fy": 2025,
        "year_mismatch": True, "year_offset": -1,
        "fields": {
            "chiffre_affaires": {"val": 4.03e11, "concept": "Revenues", "derived": False},
            "ebit": {"val": 1.29e11, "concept": "OperatingIncomeLoss", "derived": False},
        },
    },
    "wellsfargo": {  # a bank: no OperatingIncomeLoss, hence no derived EBITDA
        "id": "wellsfargo", "ticker": "WFC", "labelled_year": 2025, "chosen_fy": 2025,
        "year_mismatch": False, "year_offset": 0,
        "fields": {
            "chiffre_affaires": {"val": 8.2e10, "concept": "Revenues", "derived": False},
        },
    },
}


def _write(tmp_path, records=None):
    p = tmp_path / "gt.json"
    p.write_text(json.dumps(records if records is not None else _RECORDS), encoding="utf-8")
    return p


# ── D14 ───────────────────────────────────────────────────────────────────────

def test_year_mismatched_records_are_excluded_by_default(tmp_path):
    kept, report = load(_write(tmp_path))
    assert set(kept) == {"apple", "wellsfargo"}
    assert report["excluded_year_mismatch"] == 1
    assert report["excluded_ids"] == ["alphabet"]


def test_exclusion_is_never_silent(tmp_path):
    _, report = load(_write(tmp_path))
    assert report["loaded"] == 2
    assert "excluded_ids" in report


def test_mismatches_can_be_kept_explicitly_for_a_sensitivity_run(tmp_path):
    kept, report = load(_write(tmp_path), require_year_match=False)
    assert set(kept) == {"apple", "alphabet", "wellsfargo"}
    assert report["excluded_year_mismatch"] == 0


def test_missing_manifest_reports_absence_rather_than_crashing(tmp_path):
    kept, report = load(tmp_path / "does_not_exist.json")
    assert kept == {}
    assert report["present"] is False


# ── D15 ───────────────────────────────────────────────────────────────────────

def test_coverage_is_reported_per_field_not_as_one_corpus_size():
    rep = coverage_report(_RECORDS)
    assert rep["n_documents"] == 3
    per = rep["per_field"]
    # Revenue is tagged by everyone; EBIT is not (banks); EBITDA is rarer still.
    assert per["chiffre_affaires"]["n"] == 3
    assert per["ebit"]["n"] == 2
    assert per["ebitda"]["n"] == 1
    assert per["ebit"]["pct_of_corpus"] == 66.7


def test_derived_fields_are_flagged_because_the_label_is_coupled():
    """A detector built on EBITDA = EBIT + D&A is partly coupled to a *derived*
    EBITDA label. The count must be visible so the caveat can be stated."""
    per = coverage_report(_RECORDS)["per_field"]
    assert per["ebitda"]["n_derived"] == 1
    assert per["chiffre_affaires"]["n_derived"] == 0


def test_coverage_on_empty_corpus_is_zero_not_a_division_error():
    assert coverage_report({}) == {"n_documents": 0, "per_field": {}}
