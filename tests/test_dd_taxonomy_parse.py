"""
Real unit tests for DDTaxonomy._f — the amount parser the audit singled out as the
best code in the repo, with near-zero coverage. Covers accounting negatives,
FR/EN decimal ambiguity, textual multipliers, non-breaking spaces, currency/%.
"""

from semantic_firewall.validation.dd_base import DDTaxonomy

_f = DDTaxonomy._f


def test_plain_integers_and_thousands_separators():
    assert _f("22476") == 22476.0
    assert _f("22,476") == 22476.0          # EN thousands
    assert _f("1,234,567") == 1234567.0


def test_accounting_negative_parentheses():
    assert _f("(22,476)") == -22476.0
    assert _f("(1 234,56)") == -1234.56


def test_fr_en_decimal_ambiguity():
    assert _f("1,234.56") == 1234.56        # EN: comma=thousands, dot=decimal
    assert _f("1.234,56") == 1234.56        # FR: dot=thousands, comma=decimal
    assert _f("1,5") == 1.5                 # FR decimal comma


def test_textual_and_suffix_multipliers():
    assert _f("22.5 million") == 22_500_000.0
    assert _f("1.5B") == 1_500_000_000.0
    assert _f("22.5bn") == 22_500_000_000.0
    assert _f("3mm") == 3_000_000.0
    assert _f("2k") == 2000.0


def test_currency_symbols_percent_and_nbsp():
    assert _f("$1,000") == 1000.0
    assert _f("€ 2.000,50") == 2000.50
    assert _f("40%") == 40.0
    assert _f("1\xa0200\xa0000") == 1200000.0   # non-breaking spaces


def test_garbage_and_none_default_to_zero():
    assert _f(None) == 0.0
    assert _f("") == 0.0
    assert _f("n/a") == 0.0
    assert _f("abc") == 0.0


def test_identity_detector_flags_forgotten_da():
    # EBIT + D&A != EBITDA  ->  FAIL (this must NOT be auto-passed; that was D1)
    data = {"chiffre_affaires": "3200000", "ebit": "960000",
            "dotations_amortissements": "320000", "ebitda": "960000"}
    res = DDTaxonomy.check_ebitda_consistency(data)
    assert res["statut"] == "FAIL"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
    sys.exit(0)
