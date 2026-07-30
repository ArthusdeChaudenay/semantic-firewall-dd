"""
corrector.py — Arithmetic auto-corrector for income-statement extractions.

D1 (the finding that alone sinks the paper): in the old pipeline the corrector
overwrote EBITDA with ``ebit + d&a`` and the metric then checked EBITDA ≈ ebit + d&a
at the same 15 % tolerance — so "EBITDA coherence" was 100 % *by construction*.
The fix is architectural, not cosmetic:

  * The DETECTOR (DDTaxonomy in dd_base.py) runs on the RAW extraction, before any
    correction. Its violations and the confidence score are therefore earned, not
    tautological.
  * The CORRECTOR (this module) is a SEPARATE, optional second system. Its value is
    measured only as the precision gain it adds over the raw extraction, against
    external XBRL ground truth (experiment E6 ablates it rule-by-rule).

Design choices addressing the audit's minor defects:
  * Fixed-point application (``apply_corrections`` iterates to convergence, capped),
    so the result no longer depends on rule order and re-running is a no-op
    (idempotent). Rule order was an untested source of bugs — case 3 mutating EBIT
    then the general rule recomputing EBITDA from it.
  * Sign-aware guards: loss-making firms (negative EBIT/EBITDA) are handled, instead
    of being silently dropped by ``> 0`` tests. D&A is the only strictly non-negative
    quantity.
  * The regex backfill that used to inflate recall (D2) is a distinct, optional
    function (``regex_backfill``) so it can be disabled for the honest raw baseline
    and ablated on its own.

Every rule is a named entry in ``INCOME_STATEMENT_RULES`` so E6 can drop one at a time.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from semantic_firewall.config import EBITDA_REL_TOLERANCE
from semantic_firewall.validation.dd_base import DDTaxonomy

_f = DDTaxonomy._f

# ── Regex backfill (SEPARATE from the LLM extraction; do NOT apply to the raw
#    output used to measure recall — see D2). Kept identical to what the system
#    actually uses so the M1 baseline can be given the very same patterns (D4). ──
REGEX_BACKFILL_PATTERNS: dict[str, list[str]] = {
    "chiffre_affaires": [
        r"Net\s+sales[^\n]{0,60}?([\d,]{4,})",
        r"Total\s+net\s+revenues?[^\n]{0,60}?([\d,]{4,})",
        r"Net\s+revenues?[^\n]{0,60}?([\d,]{4,})",
        r"Total\s+revenues?[^\n]{0,60}?([\d,]{4,})",
        r"Revenues?[^\n]{0,60}?([\d,]{4,})",
    ],
    "ebit": [
        r"Income\s+from\s+operations[^\n]{0,60}?([\d,]{4,})",
        r"Operating\s+income[^\n]{0,60}?([\d,]{4,})",
        r"Operating\s+profit[^\n]{0,60}?([\d,]{4,})",
        r"Total\s+operating\s+income[^\n]{0,60}?([\d,]{4,})",
        r"Income\s+before\s+income\s+tax[^\n]{0,60}?([\d,]{4,})",
        r"Pre[-\s]tax\s+income[^\n]{0,60}?([\d,]{4,})",
        r"Earnings\s+before\s+income\s+tax[^\n]{0,60}?([\d,]{4,})",
    ],
    "dotations_amortissements": [
        r"Depreciation\s+and\s+amortization[^\n]{0,60}?([\d,]{3,})",
        r"Depreciation,\s*depletion\s+and\s+amortization[^\n]{0,60}?([\d,]{3,})",
        r"Amortization\s+of\s+content\s+assets[^\n]{0,60}?([\d,]{3,})",
        r"Depreciation[^\n]{0,60}?([\d,]{3,})",
    ],
    "resultat_net": [
        r"Net\s+income\s+attributable[^\n]{0,80}?([\d,]{4,})",
        r"Net\s+income[^\n]{0,60}?([\d,]{4,})",
        r"Net\s+earnings[^\n]{0,60}?([\d,]{4,})",
    ],
}


def regex_backfill(flat: dict, doc_text: str) -> tuple[dict, list[dict]]:
    """Fill ONLY empty fields from the document via regex. Returns (new_flat, log).

    This is an extraction augmentation, not a correction; keep it out of the raw
    LLM output when measuring recall (D2).
    """
    out = dict(flat)
    log: list[dict] = []
    for field, patterns in REGEX_BACKFILL_PATTERNS.items():
        if out.get(field, "") not in ("", None):
            continue
        for pat in patterns:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if not m:
                continue
            try:
                v = float(m.group(1).replace(",", ""))
            except (ValueError, IndexError):
                continue
            if v >= 100:
                out[field] = str(v)
                log.append({"field": field, "rule": "regex_backfill",
                            "pattern": pat, "value": v})
                break
    return out, log


# ── Correction rules ───────────────────────────────────────────────────────────
# Each rule takes the current numeric view and returns a dict {field: new_value}
# of proposed changes (empty if it does not fire). D&A is treated as ≥ 0; EBIT and
# EBITDA may be negative (loss-making firms).

def _vals(flat: dict) -> dict:
    return {
        "ca":     _f(flat.get("chiffre_affaires", "0")),
        "ebit":   _f(flat.get("ebit", "0")),
        "ebitda": _f(flat.get("ebitda", "0")),
        "da":     _f(flat.get("dotations_amortissements", "0")),
    }


def _rule_ebitda_from_parts(v: dict) -> dict:
    """EBITDA missing but EBIT and D&A present → EBITDA = EBIT + D&A (sign-aware)."""
    if v["ebitda"] == 0 and v["ebit"] != 0 and v["da"] > 0:
        return {"ebitda": v["ebit"] + v["da"]}
    return {}


def _rule_ebitda_equals_ebit(v: dict) -> dict:
    """EBITDA ≈ EBIT while D&A > 0 (LLM forgot to add D&A) → recompute."""
    if v["ebit"] != 0 and v["da"] > 0 and abs(v["ebitda"] - v["ebit"]) < 1.0:
        return {"ebitda": v["ebit"] + v["da"]}
    return {}


def _rule_ebitda_exceeds_ca(v: dict) -> dict:
    """EBITDA > CA (impossible for a normal P&L) with EBIT present → recompute."""
    if v["ca"] > 0 and v["ebitda"] > v["ca"] and v["ebit"] != 0 and v["da"] > 0:
        return {"ebitda": v["ebit"] + v["da"]}
    return {}


def _rule_ebit_below_da_big_gap(v: dict) -> dict:
    """EBIT < D&A with a large EBITDA gap → EBIT likely a sub-segment; derive EBIT.

    This is E6's first suspect for a *harmful* rule; kept but isolated and named so
    the ablation can drop it cleanly.
    """
    if (v["ebit"] > 0 and v["da"] > 0 and v["ebitda"] > 0
            and v["ebit"] < v["da"]
            and abs((v["ebit"] + v["da"]) - v["ebitda"]) / abs(v["ebitda"]) > 0.30):
        return {"ebit": v["ebitda"] - v["da"]}
    return {}


def _rule_ebitda_below_ebit(v: dict) -> dict:
    """EBITDA < EBIT is impossible when D&A ≥ 0 → recompute EBITDA."""
    if v["ebit"] != 0 and v["ebitda"] < v["ebit"] and v["da"] > 0:
        return {"ebitda": v["ebit"] + v["da"]}
    return {}


def _rule_da_exceeds_ebitda(v: dict) -> dict:
    """D&A > EBITDA with EBIT present → D&A includes non-EBITDA items; clamp D&A."""
    if v["ebit"] != 0 and v["ebitda"] > 0 and v["da"] > v["ebitda"]:
        return {"dotations_amortissements": max(v["ebitda"] - v["ebit"], 0)}
    return {}


def _rule_general_ebitda_identity(v: dict) -> dict:
    """If EBIT and D&A known and EBITDA missing or off by > tolerance → recompute."""
    if v["ebit"] != 0 and v["da"] > 0:
        computed = v["ebit"] + v["da"]
        if v["ebitda"] == 0 or abs(computed - v["ebitda"]) / max(abs(v["ebitda"]), 1) > EBITDA_REL_TOLERANCE:
            return {"ebitda": computed}
    return {}


def _rule_derive_ebit(v: dict) -> dict:
    """EBIT missing but EBITDA and D&A known → EBIT = EBITDA − D&A."""
    if v["ebit"] == 0 and v["ebitda"] != 0 and v["da"] > 0:
        return {"ebit": v["ebitda"] - v["da"]}
    return {}


# Ordered, named rule registry — E6 ablates entries by name.
INCOME_STATEMENT_RULES: list[tuple[str, Callable[[dict], dict]]] = [
    ("ebitda_from_parts",     _rule_ebitda_from_parts),
    ("ebitda_equals_ebit",    _rule_ebitda_equals_ebit),
    ("ebitda_exceeds_ca",     _rule_ebitda_exceeds_ca),
    ("ebit_below_da_big_gap", _rule_ebit_below_da_big_gap),
    ("ebitda_below_ebit",     _rule_ebitda_below_ebit),
    ("da_exceeds_ebitda",     _rule_da_exceeds_ebitda),
    ("general_ebitda_identity", _rule_general_ebitda_identity),
    ("derive_ebit",           _rule_derive_ebit),
]

_FIELD_OF_VAL = {"ebit": "ebit", "ebitda": "ebitda",
                 "dotations_amortissements": "dotations_amortissements"}


def apply_corrections(
    flat: dict,
    doc_type: str,
    doc_text: str | None = None,
    use_regex_backfill: bool = True,
    disabled_rules: set[str] | None = None,
    max_iter: int = 5,
) -> tuple[dict, list[dict]]:
    """Apply the corrector as a SEPARATE stage. Returns (corrected_flat, applied_log).

    Fixed-point iteration to convergence (≤ max_iter) makes the outcome independent
    of a single pass's rule order and idempotent under re-application.
    Only ``compte_resultat`` has arithmetic rules today; other types pass through
    (regex backfill still runs if requested and a doc_text is given).
    """
    disabled = disabled_rules or set()
    out = dict(flat)
    log: list[dict] = []

    if use_regex_backfill and doc_text is not None:
        out, bl = regex_backfill(out, doc_text)
        log.extend(bl)

    if doc_type != "compte_resultat":
        return out, log

    for _ in range(max_iter):
        changed = False
        for name, rule in INCOME_STATEMENT_RULES:
            if name in disabled:
                continue
            v = _vals(out)
            changes = rule(v)
            for field, new_val in changes.items():
                old = _f(out.get(field, "0"))
                if abs(old - new_val) > 1e-9:
                    out[field] = str(new_val)
                    log.append({"field": field, "rule": name,
                                "old": old, "new": new_val})
                    changed = True
        if not changed:
            break

    # Recompute the EBITDA margin from the (possibly corrected) values.
    v = _vals(out)
    if v["ca"] > 0 and v["ebitda"] != 0:
        marge = round(v["ebitda"] / v["ca"] * 100, 1)
        if abs(_f(out.get("marge_ebitda_pct", "0")) - marge) > 1.0:
            out["marge_ebitda_pct"] = str(marge)
            log.append({"field": "marge_ebitda_pct", "rule": "recompute_margin",
                        "new": marge})

    return out, log
