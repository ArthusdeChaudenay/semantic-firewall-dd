"""
run_all_eval.py — Évaluation complète sur les 15 samples (invoice + DD)

  samples/      → evaluate.py  (FinVerBenchTaxonomy — factures)
  samples/dd/   → dd_eval.py   (DDTaxonomy — bilan / P&L / cap table)

Lance avec :
    .\\venv\\Scripts\\python.exe run_all_eval.py
"""

import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── imports des deux pipelines ─────────────────────────────────────────────
from semantic_firewall.evaluation.evaluate import run_evaluation
from semantic_firewall.evaluation.dd_eval import run_dd_evaluation

SEP = "=" * 60

# ==========================================
# MAPPING DDTaxonomy → catégories unifiées
# ==========================================
# Regroupe les contrôles DD sous les 3 axes du rapport consolidé

DD_TO_UNIFIED = {
    # completeness
    "completeness":       "completeness",
    # arithmetic
    "equilibre_bilan":    "arithmetic",
    "decomposition_actif":"arithmetic",
    "coherence_ebitda":   "arithmetic",
    "coherence_ebit":     "arithmetic",
    "coherence_marge_ebitda": "arithmetic",
    "valorisation_post":  "arithmetic",
    "prix_par_action":    "arithmetic",
    "total_pct":          "arithmetic",
    # temporal — pas de contrôle temporel dans DDTaxonomy pour l'instant
}

AXES = ["completeness", "arithmetic", "temporal"]


def _agg(results_list: list[dict]) -> dict:
    """Agrège les résultats d'un pipeline sous les 3 axes."""
    counts = {ax: {"PASS": 0, "FAIL": 0, "ERROR": 0} for ax in AXES}
    for doc in results_list:
        if doc.get("statut") == "error":
            continue
        # ── invoice docs ───────────────────────────────────────
        tc = doc.get("taxonomy_checks") or doc.get("audit_finverbench")
        if tc:
            for ax in AXES:
                if ax in tc:
                    s = tc[ax].get("statut", "ERROR")
                    counts[ax][s] = counts[ax].get(s, 0) + 1
            continue
        # ── DD docs ────────────────────────────────────────────
        comp = doc.get("completeness", {})
        if comp:
            s = comp.get("statut", "ERROR")
            counts["completeness"][s] = counts["completeness"].get(s, 0) + 1
        dd_audit = doc.get("dd_audit", {})
        for check, res in dd_audit.items():
            ax = DD_TO_UNIFIED.get(check)
            if ax:
                s = res.get("statut", "ERROR")
                counts[ax][s] = counts[ax].get(s, 0) + 1
    return counts


def _pass_rate(c: dict) -> float:
    total = sum(c.values())
    return round(c.get("PASS", 0) / total * 100, 1) if total else 0.0


# NOTE (D3): a filename-keyword confusion matrix used to live here. It labelled
# ground truth from the presence of "erreur"/"incoherent" in the filename on a
# handful of author-written documents - a self-fulfilling test, not an evaluation.
# Removed. Classification metrics belong in run_experiment.py against XBRL labels.


# ==========================================
# MAIN
# ==========================================

def run_all() -> None:
    print(f"\n{SEP}")
    print(f"  ÉVALUATION COMPLÈTE — FinVerBench + DDTaxonomy")
    print(f"{SEP}")

    # ── factures (11 docs) ─────────────────────────────────────
    print("\n[1/2] Pipeline invoice (samples/)…")
    bench_report = run_evaluation()

    # ── documents DD (4 docs) ─────────────────────────────────
    print("\n[2/2] Pipeline DD (samples/dd/)…")
    dd_report = run_dd_evaluation()

    # ── consolidation ──────────────────────────────────────────
    invoice_results = bench_report.get("resultats_par_document", [])
    dd_results      = dd_report.get("resultats", [])
    all_results     = invoice_results + dd_results

    nb_total   = len(all_results)
    nb_erreurs = sum(1 for r in all_results if r.get("statut") == "error")

    counts = _agg(all_results)

    acc_invoice = bench_report.get("metriques", {}).get("accuracy_champs_pct")
    acc_dd      = dd_report.get("accuracy_moyenne_pct")

    print(f"\n{SEP}")
    print(f"  RÉSULTATS CONSOLIDÉS — {nb_total} documents")
    print(f"{SEP}")
    print(f"  Erreurs d'extraction     : {nb_erreurs}")
    if acc_invoice is not None:
        print(f"  Accuracy champs invoice  : {acc_invoice}%")
    if acc_dd is not None:
        print(f"  Accuracy champs DD       : {acc_dd}%")
    print(f"  {'─' * 52}")

    # ── axes principaux ────────────────────────────────────────
    for ax in AXES:
        c   = counts[ax]
        pr  = _pass_rate(c)
        bar = ("✅" if c.get("FAIL", 0) + c.get("ERROR", 0) == 0
               else "⚠ " if c.get("FAIL", 0) > 0 and c.get("ERROR", 0) == 0
               else "❌")
        print(f"  {ax.capitalize():<14} {pr:>6.1f}% PASS {bar}  {c}")

    print(f"  {'─' * 52}")
    print(f"  (⚠ = FAIL attendu sur doc de test intentionnel)")
    print(f"{SEP}")

    print()


if __name__ == "__main__":
    run_all()
