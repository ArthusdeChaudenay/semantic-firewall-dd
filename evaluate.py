"""
evaluate.py — Pipeline d'évaluation FinVerBench x AuditWen pour Qwen-7B

Pour chaque document dans samples/ :
  1. Extraction LLM via llm_extractor.py (templates AuditWen)
  2. Comparaison champ par champ avec le ground truth (output/<doc>.json)
  3. Contrôles déterministes FinVerBenchTaxonomy

Métriques produites :
  - Accuracy des champs extraits (exact-match normalisé)
  - Taux de PASS/FAIL/ERROR par contrôle FinVerBench
  - Rapport JSON complet dans output/benchmark_report.json
"""

import json
import os
from pathlib import Path

from tqdm import tqdm

from benchmark_base import FinVerBenchTaxonomy
from llm_extractor import extract_document, SUPPORTED_EXTENSIONS, MODEL_NAME

SAMPLES_DIR = Path("samples")
GROUND_TRUTH_DIR = Path("output")
REPORT_PATH = GROUND_TRUTH_DIR / "benchmark_report.json"


# ==========================================
# UTILITAIRES
# ==========================================

def load_ground_truth(nom_fichier: str) -> dict | None:
    gt_path = GROUND_TRUTH_DIR / f"{nom_fichier}.json"
    if gt_path.exists():
        return json.loads(gt_path.read_text(encoding="utf-8"))
    return None


def normalize(val) -> str:
    """Normalisation pour comparaison : minuscule, virgule→point, flottants canoniques."""
    if val is None:
        return ""
    s = str(val).strip().replace(",", ".").lower()
    if s in ("none", "null", "n/a", "inconnu"):
        return ""
    try:
        return str(float(s)) if s else ""
    except ValueError:
        return s


def compare_fields(llm_champs: dict, gt_champs: dict) -> dict:
    """
    Compare champ par champ le résultat LLM avec le ground truth.
    Retourne un dict {champ: {llm, ground_truth, match}}.
    """
    all_keys = set(gt_champs.keys()) | set(llm_champs.keys())
    results = {}
    for key in sorted(all_keys):
        llm_val = normalize(llm_champs.get(key, {}).get("valeur", ""))
        gt_val = normalize(gt_champs.get(key, {}).get("valeur", ""))
        results[key] = {
            "llm": llm_val,
            "ground_truth": gt_val,
            "match": llm_val == gt_val,
        }
    return results


def flatten_champs(champs: dict) -> dict:
    """Extrait valeurs et sources brutes pour FinVerBenchTaxonomy."""
    flat = {}
    for k, v in champs.items():
        flat[k] = v.get("valeur", "")
        if "valeur_brute" in v:
            flat[f"{k}_brut"] = v["valeur_brute"]
    return flat


def _statut_counter() -> dict:
    return {"PASS": 0, "FAIL": 0, "ERROR": 0}


# ==========================================
# PIPELINE D'ÉVALUATION
# ==========================================

def run_evaluation() -> dict:
    documents = sorted(
        f for f in SAMPLES_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not documents:
        print(f"Aucun document trouvé dans '{SAMPLES_DIR}/'.")
        return {}

    GROUND_TRUTH_DIR.mkdir(exist_ok=True)

    # Compteurs globaux
    total_fields = 0
    correct_fields = 0
    taxonomy_counts = {
        "completeness":  _statut_counter(),
        "arithmetic":    _statut_counter(),
        "temporal":      _statut_counter(),
        "vat_rate":      _statut_counter(),
        "transcription": _statut_counter(),
    }
    extraction_errors = 0

    all_results = []

    print(f"\n=== FinVerBench × AuditWen — Évaluation de {MODEL_NAME} ===")
    print(f"Documents détectés dans '{SAMPLES_DIR}/' : {len(documents)}\n")

    for doc_path in tqdm(documents, desc="Traitement", unit="doc"):
        nom = doc_path.name

        # 1. Extraction LLM
        llm_result = extract_document(str(doc_path))

        if llm_result["statut"] == "error":
            extraction_errors += 1

        # 2. Ground truth (optionnel)
        gt_result = load_ground_truth(nom)
        field_comparison = {}
        if gt_result and llm_result["statut"] == "ok":
            field_comparison = compare_fields(llm_result["champs"], gt_result["champs"])
            for fc in field_comparison.values():
                total_fields += 1
                if fc["match"]:
                    correct_fields += 1

        # 3. Contrôles FinVerBench
        flat = flatten_champs(llm_result.get("champs", {}))
        audit = FinVerBenchTaxonomy.run_full_audit(flat)

        for check_name, check_result in audit.items():
            statut = check_result.get("statut", "ERROR")
            taxonomy_counts[check_name][statut] = taxonomy_counts[check_name].get(statut, 0) + 1

        all_results.append({
            "document": nom,
            "statut_extraction": llm_result["statut"],
            "message_erreur": llm_result.get("message"),
            "champs_llm": llm_result.get("champs", {}),
            "comparaison_champs": field_comparison,
            "audit_finverbench": audit,
        })

    # ==========================================
    # CALCUL DES MÉTRIQUES AGRÉGÉES
    # ==========================================
    n = len(documents)
    if total_fields > 0:
        accuracy_pct = round(correct_fields / total_fields * 100, 2)
    else:
        total_checks = sum(sum(c.values()) for c in taxonomy_counts.values())
        pass_checks  = sum(c["PASS"] for c in taxonomy_counts.values())
        accuracy_pct = round(pass_checks / total_checks * 100, 2) if total_checks else None

    def pass_rate(counter: dict) -> float | None:
        total = sum(counter.values())
        return round(counter["PASS"] / total * 100, 2) if total else None

    report = {
        "modele": MODEL_NAME,
        "nb_documents": n,
        "metriques": {
            "accuracy_champs_pct": accuracy_pct,
            "champs_corrects": correct_fields,
            "champs_total": total_fields,
            "erreurs_extraction": extraction_errors,
            "finverbench": {
                check: {
                    "pass_rate_pct": pass_rate(counter),
                    "detail": counter,
                }
                for check, counter in taxonomy_counts.items()
            },
        },
        "resultats_par_document": all_results,
    }

    REPORT_PATH.write_text(json.dumps(report, indent=4, ensure_ascii=False), encoding="utf-8")

    # ==========================================
    # AFFICHAGE DU RÉSUMÉ
    # ==========================================
    sep = "=" * 58
    print(f"\n{sep}")
    print(f"  RAPPORT FinVerBench × AuditWen — {MODEL_NAME}")
    print(sep)
    print(f"  Documents traités       : {n}")
    print(f"  Erreurs d'extraction    : {extraction_errors}")

    if accuracy_pct is not None:
        print(f"  Accuracy champs LLM     : {accuracy_pct:.1f}%  ({correct_fields}/{total_fields})")
    else:
        print(f"  Accuracy champs LLM     : N/A (aucun ground truth trouvé)")

    print()
    for check, counter in taxonomy_counts.items():
        pr = pass_rate(counter)
        label = check.capitalize().ljust(20)
        pr_str = f"{pr:.1f}%" if pr is not None else "N/A"
        print(f"  FinVerBench {label}: {pr_str} PASS  {counter}")

    print(sep)
    print(f"  Rapport complet         : {REPORT_PATH}")
    print(f"{sep}\n")

    return report


if __name__ == "__main__":
    run_evaluation()
