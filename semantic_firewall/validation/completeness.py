"""
completeness.py — Mandatory-field completeness check.

Extracted from dd_eval.py so that both the pipeline and the evaluation harness
import it from one place (previously pipeline reached back into the evaluation
module, coupling core to evaluation).
"""

from __future__ import annotations

CHAMPS_OBLIGATOIRES = {
    "bilan":           ["entreprise_nom", "exercice", "actif_total", "passif_total"],
    "compte_resultat": ["entreprise_nom", "exercice", "chiffre_affaires", "ebit"],
    "captable":        ["entreprise_nom", "valorisation_pre_money", "valorisation_post_money",
                        "total_actions", "prix_par_action"],
}


def check_completeness(flat: dict, doc_type: str) -> dict:
    """FAIL if any mandatory field for ``doc_type`` is missing/empty/zero.

    Note (D-minor): a value of "0"/"0.0" is treated as missing. This is a known
    limitation for genuinely-zero fields; loss-making firms are handled in the
    corrector, not here (completeness is about presence, not sign).
    """
    manquants = [
        c for c in CHAMPS_OBLIGATOIRES.get(doc_type, [])
        if not flat.get(c) or str(flat[c]).strip() in ("", "null", "None", "0", "0.0")
    ]
    if manquants:
        return {"statut": "FAIL",
                "message": f"Champs obligatoires manquants : {', '.join(manquants)}"}
    return {"statut": "PASS"}
