"""
anchoring.py — Textual-anchor hallucination detector.

Moved out of dd_eval.py and WIRED INTO the pipeline (fixes D6, which flagged this
— the highest-value component — as dead, unreachable code).

Idea: every extracted amount must appear in the document *near* its accounting
anchor label (e.g. "Total assets"), not merely somewhere in the file. An amount
that the LLM emits but that is absent from the anchor window is a candidate
hallucination — a second, label-free error signal independent of the arithmetic
identities.

Two D6 corrections vs. the original:
  1. The self-neutralising "else search the whole document" fallback is REMOVED.
     That fallback destroyed exactly the localised signal the test exists to
     measure (an amount found anywhere in a 200k-char 10-K almost always "passes").
  2. The anchor window (lines before/after the label) is a parameter, defaulting
     to config.ANCHOR_WINDOW_*, so E5 can sweep it (±0, ±2, ±5, ±10).
"""

from __future__ import annotations

from semantic_firewall.config import ANCHOR_WINDOW_AFTER, ANCHOR_WINDOW_BEFORE
from semantic_firewall.validation.dd_base import DDTaxonomy

# Financial fields to check per document type.
_TRANSCRIPTION_FIELDS = {
    "bilan":           ["actif_total", "passif_total", "capitaux_propres"],
    "compte_resultat": ["chiffre_affaires", "ebit"],
    "captable":        ["valorisation_pre_money", "valorisation_post_money"],
}

# Accounting anchor labels expected on the line carrying the amount (FR + EN).
_ANCHOR_KEYWORDS: dict[str, list[str]] = {
    "actif_total":            ["total actif", "total de l actif", "total assets"],
    "passif_total":           ["total passif", "total du passif",
                               "total liabilities and", "total liabilities stockholders"],
    "capitaux_propres":       ["capitaux propres", "total capitaux propres", "fonds propres",
                               "stockholders equity", "shareholders equity", "total equity"],
    "chiffre_affaires":       ["chiffre d affaires", "ca net", "revenus nets",
                               "net sales", "total net sales", "net revenues",
                               "total revenues", "total net revenues",
                               "total revenue", "net revenue"],
    "ebitda":                 ["ebitda", "excedent brut", "ebe",
                               "adjusted ebitda", "operating ebitda"],
    "ebit":                   ["ebit", "resultat d exploitation", "resultat operationnel",
                               "operating income", "income from operations", "operating profit"],
    "valorisation_pre_money": ["pre-money", "pre money", "valorisation pre"],
    "valorisation_post_money": ["post-money", "post money", "valorisation post"],
}


def _amount_present_in_text(amount: float, text: str) -> bool:
    n = int(abs(amount))
    if n == 0:
        return True
    NBSP = " "
    NNBSP = " "
    sep = "{:,}".format(n)
    variants: set = {
        str(n),
        sep.replace(",", " "),
        sep.replace(",", NBSP),
        sep.replace(",", NNBSP),
        sep.replace(",", "."),
        sep,
        "{:.2f}".format(amount),
    }
    if n >= 1_000 and n % 1_000 == 0:
        k = n // 1_000
        variants.update({str(k) + "K", str(k) + "k", str(k) + " K"})
    if n >= 1_000_000 and n % 1_000_000 == 0:
        mm = n // 1_000_000
        variants.update({str(mm) + "M", str(mm) + " M", "{:.1f}M".format(mm)})
    normalized = text.replace(NBSP, " ").replace(NNBSP, " ")
    return any(v in normalized for v in variants)


def _find_anchor_lines(lines: list[str], keywords: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines)
            if any(kw in line.lower() for kw in keywords)]


def check_transcription_divergence(
    flat: dict,
    raw_text: str,
    doc_type: str,
    window_before: int = ANCHOR_WINDOW_BEFORE,
    window_after: int = ANCHOR_WINDOW_AFTER,
) -> dict:
    """Verify each key amount appears within [−window_before, +window_after] lines
    of its accounting anchor. No whole-document fallback (D6).

    Returns a check dict with statut PASS/FAIL and, additionally, the per-field
    outcomes so E5 can score it as a detector against XBRL ground truth.
    """
    fields = _TRANSCRIPTION_FIELDS.get(doc_type, [])
    if not fields:
        return {"statut": "PASS", "message": "Aucun champ financier cle a verifier.",
                "per_field": {}}

    lines = raw_text.splitlines()
    absents: list[str] = []
    per_field: dict[str, bool] = {}  # True = anchored (ok), False = not anchored (suspect)

    for field in fields:
        val = flat.get(field)
        if not val or str(val).strip() in ("", "null", "None"):
            continue
        num = DDTaxonomy._f(val)
        if num == 0:
            continue

        anchors = _ANCHOR_KEYWORDS.get(field, [])
        anchor_idxs = _find_anchor_lines(lines, anchors) if anchors else []

        if not anchor_idxs:
            # No anchor label at all: only assert the amount exists literally.
            ok = _amount_present_in_text(num, raw_text)
            per_field[field] = ok
            if not ok:
                absents.append(f"{field}={int(num)} (libelle absent)")
            continue

        # Anchor label present: the amount MUST be in the localised window.
        found = any(
            _amount_present_in_text(
                num, "\n".join(lines[max(0, idx - window_before): idx + window_after + 1])
            )
            for idx in anchor_idxs
        )
        per_field[field] = found
        if not found:
            absents.append(f"{field}={int(num)}")

    if not absents:
        return {"statut": "PASS",
                "message": "Montants cles presents en contexte localise.",
                "per_field": per_field}
    return {
        "statut": "FAIL",
        "message": ("Montants absents du contexte localise (possible hallucination LLM) : "
                    + ", ".join(absents)),
        "per_field": per_field,
    }
