"""scale.py — D13: reporting-scale inference, so extractions are comparable to XBRL.

The defect this fixes (audit D13). SEC XBRL reports facts in **absolute USD**
(``416161000000``). Financial-statement *tables*, on the other hand, are printed in
thousands or millions (``416,161``), and our extraction prompt explicitly says
"copy the value EXACTLY as shown; do NOT multiply". Comparing the two directly
therefore fails for **every field of every document** — silently, because the code
runs and returns numbers. It is a worse failure than a crash.

Design rule, and the reason this module exists at all:

    The scale MUST be inferred from the DOCUMENT, never from the ground truth.

Snapping an extraction to whichever power of 1000 lands nearest the XBRL value would
leak the label into the prediction and recreate the D1 tautology in a new place. So
``infer_document_scale`` reads only the filing text, and records the evidence string
it matched on, so every normalisation is auditable.

Because scale is inferred independently, a *scale error* becomes a measurable error
mode of its own (``classify_error``) — and an operationally important one: reporting
EBITDA in thousands instead of millions is off by 1000x, is invisible to every
accounting identity (all terms scale together), and would pass a naive review.
"""

from __future__ import annotations

import re

# Ordered most-specific first. Each pattern is anchored on the parenthetical or
# lead-in that statement headers conventionally use, e.g.
#   "(In millions, except per share amounts)"      "$ in thousands"
#   "(Dollars in millions)"                        "en milliers d'euros"
_SCALE_PATTERNS: list[tuple[str, float, str]] = [
    (r"\(?\s*(?:in|dollars\s+in|amounts\s+in|\$\s*in)\s+billions", 1e9, "billions"),
    (r"\(?\s*(?:in|dollars\s+in|amounts\s+in|\$\s*in)\s+millions", 1e6, "millions"),
    (r"\(?\s*(?:in|dollars\s+in|amounts\s+in|\$\s*in)\s+thousands", 1e3, "thousands"),
    (r"\bin\s+millions\s+of\s+(?:U\.?S\.?\s*)?dollars", 1e6, "millions"),
    (r"\bin\s+thousands\s+of\s+(?:U\.?S\.?\s*)?dollars", 1e3, "thousands"),
    (r"\bmillions\s+of\s+dollars\b", 1e6, "millions"),
    (r"\bthousands\s+of\s+dollars\b", 1e3, "thousands"),
    # French filings (BALO / IFRS annual reports)
    (r"\ben\s+milliards\s+d['’]euros", 1e9, "milliards"),
    (r"\ben\s+millions\s+d['’]euros", 1e6, "millions"),
    (r"\ben\s+milliers\s+d['’]euros", 1e3, "milliers"),
    (r"\bK\s*€|\bkEUR\b", 1e3, "milliers"),
    (r"\bM\s*€|\bmEUR\b", 1e6, "millions"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), mult, label) for p, mult, label in _SCALE_PATTERNS]

# Statement headers state the scale near the table, not in the risk-factor prose.
# Scanning a bounded prefix keeps a stray "billions of dollars" in Item 1A from
# overriding the actual table header.
DEFAULT_SCAN_CHARS: int = 400_000


def infer_document_scale(text: str, scan_chars: int = DEFAULT_SCAN_CHARS) -> dict:
    """Infer the reporting scale of a filing from its own text.

    Returns ``{"multiplier", "label", "evidence", "position", "confident"}``.
    ``confident`` is False when no scale statement was found, in which case the
    multiplier defaults to 1.0 (units) — callers should treat such documents as
    ineligible for exact-value scoring rather than assume units (see
    ``eligible_for_scoring``).
    """
    window = text[:scan_chars]
    best: tuple[int, float, str, str] | None = None
    for rx, mult, label in _COMPILED:
        m = rx.search(window)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), mult, label, m.group(0).strip())
    if best is None:
        return {"multiplier": 1.0, "label": "units", "evidence": None,
                "position": None, "confident": False}
    pos, mult, label, evidence = best
    return {"multiplier": mult, "label": label, "evidence": evidence,
            "position": pos, "confident": True}


def eligible_for_scoring(scale: dict) -> bool:
    """Whether a document's scale is known well enough to score values against XBRL.

    Documents with no scale statement are reported as a gap, never silently scored
    as if they were in units (that would fabricate a 1e6 error).
    """
    return bool(scale.get("confident"))


def to_absolute(value: float, scale: dict) -> float:
    """Convert a value read off a statement table into absolute currency units."""
    return value * float(scale.get("multiplier", 1.0))


# Ratios that indicate a pure scale error rather than a wrong figure. Includes
# 1e2 because some filings tabulate in hundreds of thousands or mix units.
_SCALE_RATIOS: tuple[float, ...] = (1e2, 1e3, 1e4, 1e6, 1e9)


def classify_error(extracted_abs: float, truth: float, rel_tol: float = 0.01) -> str:
    """Label the *kind* of discrepancy between a normalised extraction and truth.

    Returns one of:
      ``correct``       within tolerance;
      ``missing``       nothing extracted;
      ``sign``          right magnitude, wrong sign (loss reported as profit);
      ``scale``         off by a clean power-of-ten factor — a unit error, not a
                        reading error, and invisible to accounting identities;
      ``wrong_value``   genuinely the wrong number (wrong line, wrong year, wrong
                        segment) — the error mode the identity detector targets.
    """
    if truth == 0:
        return "correct" if abs(extracted_abs) < 1.0 else "wrong_value"
    if extracted_abs == 0:
        return "missing"
    if abs(extracted_abs - truth) <= max(abs(truth) * rel_tol, 1.0):
        return "correct"
    if abs(abs(extracted_abs) - abs(truth)) <= max(abs(truth) * rel_tol, 1.0):
        return "sign"
    ratio = abs(truth) / abs(extracted_abs)
    for r in _SCALE_RATIOS:
        if abs(ratio - r) / r <= 0.02 or abs(ratio - 1.0 / r) * r <= 0.02:
            return "scale"
    return "wrong_value"
