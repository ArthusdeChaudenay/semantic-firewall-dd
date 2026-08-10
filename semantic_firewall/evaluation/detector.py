"""
detector.py — Experiment E1: the unsupervised error detector, evaluated honestly.

Freeze the extraction. Compute accounting-identity VIOLATIONS on the raw output,
before any auto-correction. Then, with XBRL labels (E0), answer the real research
question:

    Does an accounting-identity violation predict that a value is wrong?

Unlike DDTaxonomy (which returns PASS/FAIL), this module returns a CONTINUOUS
violation magnitude per identity, so it can be ranked (ROC-AUC / PR-AUC / P@k) and
its score can be calibrated against the "value is wrong" label.

This is the paper's contribution. The old "correct, then verify with the same
formula" loop is not — it scored 100 % by construction (D1).
"""

from __future__ import annotations

from semantic_firewall.validation.dd_base import DDTaxonomy

_f = DDTaxonomy._f


def identity_residuals(flat: dict, doc_type: str) -> dict:
    """Continuous, scale-free residuals for each hard identity (higher = worse).

    Computed on the RAW extraction. A residual of 0 means the identity holds
    exactly; values are relative so they are comparable across companies.
    """
    r: dict[str, float] = {}

    if doc_type == "bilan":
        actif = _f(flat.get("actif_total"))
        passif = _f(flat.get("passif_total"))
        if actif or passif:
            r["balance_equilibrium"] = abs(actif - passif) / max(abs(actif), abs(passif), 1.0)
        immo = _f(flat.get("actif_immobilise"))
        circ = _f(flat.get("actif_circulant"))
        treso = _f(flat.get("tresorerie"))
        if actif:
            r["asset_decomposition"] = abs((immo + circ + treso) - actif) / max(abs(actif), 1.0)

    elif doc_type == "compte_resultat":
        ebit = _f(flat.get("ebit"))
        da = _f(flat.get("dotations_amortissements"))
        ebitda = _f(flat.get("ebitda"))
        if ebitda != 0 and da != 0:
            r["ebitda_identity"] = abs((ebit + da) - ebitda) / max(abs(ebitda), 1.0)
        ca = _f(flat.get("chiffre_affaires"))
        marge = _f(flat.get("marge_ebitda_pct"))
        if ca != 0 and marge != 0:
            r["ebitda_margin"] = abs((ebitda / ca * 100) - marge) / max(abs(marge), 1.0)

    elif doc_type == "captable":
        pre = _f(flat.get("valorisation_pre_money"))
        levee = _f(flat.get("montant_levee"))
        post = _f(flat.get("valorisation_post_money"))
        if post != 0 and levee != 0:
            r["post_money"] = abs((pre + levee) - post) / max(abs(post), 1.0)
        actions = _f(flat.get("total_actions"))
        prix = _f(flat.get("prix_par_action"))
        if actions != 0 and prix != 0 and post != 0:
            r["price_per_share"] = abs((post / actions) - prix) / max(abs(prix), 1.0)

    return r


def document_anomaly_score(flat: dict, doc_type: str) -> float:
    """A single document-level suspicion score = max identity residual.

    Max (not mean) because one violated identity is enough to prove an error.
    """
    res = identity_residuals(flat, doc_type)
    return max(res.values()) if res else 0.0


def build_detection_dataset(records: list[dict], gt_index: dict, rel_tol: float = 0.01,
                            exclude_scale_errors: bool = True):
    """Assemble (scores, labels) for E1 from raw extractions + XBRL ground truth.

    records:  [{"cik", "fy", "doc_type", "raw_fields", "scale"}...]
    gt_index: {(cik, fy): {field: {"val": ...}}} from xbrl_ground_truth.

    label = 1 if ANY field of that document is wrong beyond tolerance vs XBRL.
    score = document_anomaly_score on the raw extraction.

    ``scale`` per record (D13) is the reporting scale inferred from the document by
    ``extraction.scale.infer_document_scale``; without it every field compares as
    wrong, every label becomes 1, and ROC-AUC is undefined for want of negatives.
    Documents whose scale could not be established are skipped rather than scored
    under a guessed multiplier, and counted in ``n_skipped``.

    ``exclude_scale_errors`` keeps pure unit errors out of the label. They are real
    errors, but they are invisible to accounting identities by construction (all
    terms scale together), so leaving them in would charge the detector with
    failures it cannot physically see. They are reported separately instead.

    Returns (scores, labels, n_skipped). No silent drops — n_skipped is reported.
    """
    from semantic_firewall.evaluation.xbrl_ground_truth import score_extraction

    scores, labels, skipped = [], [], 0
    for rec in records:
        gt = gt_index.get((rec["cik"], rec["fy"]))
        if not gt:
            skipped += 1
            continue
        scale = rec.get("scale")
        if scale is not None and not scale.get("confident", False):
            skipped += 1
            continue
        per_field = score_extraction(rec["raw_fields"], gt, rel_tol=rel_tol, scale=scale)
        if not per_field:
            skipped += 1
            continue
        if exclude_scale_errors:
            wrong = any(v["error_kind"] not in ("correct", "scale")
                        for v in per_field.values())
        else:
            wrong = any(not v["within_tol"] for v in per_field.values())
        scores.append(document_anomaly_score(rec["raw_fields"], rec["doc_type"]))
        labels.append(1 if wrong else 0)
    return scores, labels, skipped
