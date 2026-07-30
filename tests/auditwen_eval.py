"""
auditwen_eval.py — Étape 3 : Évaluation structurée selon la méthode AuditWen

Pipeline tri-passes pour chaque document :
  [Pass 1] Extraction structurée   → template extraction_structuree
  [Pass 2] Vérification LLM        → template verification_coherence (Qwen se relit)
  [Pass 3] Contrôle déterministe   → FinVerBenchTaxonomy

Puis croisement des verdicts Pass 2 vs Pass 3 :
  → mesure si Qwen détecte correctement ses propres erreurs arithmétiques et temporelles

Score AuditWen (0-100) = 40% complétude + 30% exactitude (GT) + 30% cohérence LLM/déterministe

Lance avec :
    .\\venv\\Scripts\\python.exe auditwen_eval.py
"""

import json
import sys
from pathlib import Path

from tqdm import tqdm

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.validation.finverbench import FinVerBenchTaxonomy
from semantic_firewall.extraction.llm_extractor import (
    extract_document,
    call_qwen_verification,
    SUPPORTED_EXTENSIONS,
    MODEL_NAME,
)

SAMPLES_DIR = Path("samples")
GROUND_TRUTH_DIR = Path("output")
REPORT_PATH = GROUND_TRUTH_DIR / "auditwen_report.json"


# ==========================================
# UTILITAIRES
# ==========================================

def load_ground_truth(nom_fichier: str) -> dict | None:
    gt_path = GROUND_TRUTH_DIR / f"{nom_fichier}.json"
    if gt_path.exists():
        return json.loads(gt_path.read_text(encoding="utf-8"))
    return None


def normalize(val) -> str:
    if val is None:
        return ""
    s = str(val).strip().replace(",", ".").lower()
    if s in ("none", "null", "n/a", "inconnu"):
        return ""
    try:
        return str(float(s)) if s else ""
    except ValueError:
        return s


def flatten_champs(champs: dict) -> dict:
    """Extrait valeurs et sources brutes pour FinVerBenchTaxonomy."""
    flat = {}
    for k, v in champs.items():
        flat[k] = v.get("valeur", "")
        if "valeur_brute" in v:
            flat[f"{k}_brut"] = v["valeur_brute"]
    return flat


# ==========================================
# MÉTRIQUES
# ==========================================

def compute_completeness(flat: dict) -> float:
    """Taux de champs non vides parmi tous les champs extraits."""
    non_empty = sum(1 for v in flat.values() if v and v not in ("null", "None"))
    return non_empty / max(len(flat), 1)


def compute_accuracy(champs: dict, gt_champs: dict | None) -> float | None:
    """Taux exact-match vs ground truth (None si pas de GT disponible)."""
    if not gt_champs:
        return None
    all_keys = set(gt_champs.keys()) | set(champs.keys())
    matches = sum(
        1 for k in all_keys
        if normalize(champs.get(k, {}).get("valeur", ""))
        == normalize(gt_champs.get(k, {}).get("valeur", ""))
    )
    return matches / len(all_keys) if all_keys else 0.0


def compare_coherence(finverbench: dict, llm_verif: dict) -> dict:
    """
    Croise les verdicts LLM (Pass 2) et déterministe (Pass 3) sur
    l'arithmétique et le temporel.

    Retourne pour chaque axe :
        finverbench_fail   : bool  (FinVerBench a détecté une erreur)
        llm_detecte_erreur : bool  (Qwen a signalé l'erreur dans sa relecture)
        accord             : bool  (les deux sont d'accord)
    """
    fb_arith_fail = finverbench.get("arithmetic", {}).get("statut") != "PASS"
    fb_temp_fail = finverbench.get("temporal", {}).get("statut") != "PASS"

    # coherence_arithmetique: true = pas d'erreur → inversion pour obtenir "fail"
    llm_arith_fail = not llm_verif.get("coherence_arithmetique", True)
    llm_temp_fail = not llm_verif.get("coherence_temporelle", True)

    return {
        "arithmetique": {
            "finverbench_fail": fb_arith_fail,
            "llm_detecte_erreur": llm_arith_fail,
            "accord": fb_arith_fail == llm_arith_fail,
        },
        "temporel": {
            "finverbench_fail": fb_temp_fail,
            "llm_detecte_erreur": llm_temp_fail,
            "accord": fb_temp_fail == llm_temp_fail,
        },
    }


def compute_auditwen_score(
    completeness: float,
    accuracy: float | None,
    coherence_comp: dict,
) -> float:
    """
    Score AuditWen sur 100 :
      40% complétude  — tous les champs sont extraits
      30% exactitude  — les valeurs correspondent au ground truth
                        (redistribués sur la complétude si pas de GT)
      30% cohérence   — Qwen et FinVerBench s'accordent sur les erreurs
    """
    accords = [v["accord"] for v in coherence_comp.values()]
    coherence_rate = sum(accords) / len(accords) if accords else 0.0

    if accuracy is not None:
        score = 0.40 * completeness + 0.30 * accuracy + 0.30 * coherence_rate
    else:
        score = 0.70 * completeness + 0.30 * coherence_rate

    return round(score * 100, 1)


# ==========================================
# ÉVALUATION D'UN DOCUMENT
# ==========================================

def evaluate_document(doc_path: Path) -> dict:
    nom = doc_path.name
    print(f"\n  {'─' * 52}")
    print(f"  Document : {nom}")

    # ── Pass 1 : extraction structurée ───────────────────────
    print(f"  [Pass 1] Extraction structurée...", end=" ", flush=True)
    llm_result = extract_document(str(doc_path))

    if llm_result["statut"] == "error":
        print(f"FAIL — {llm_result['message']}")
        return {"document": nom, "statut": "error", "message": llm_result["message"]}

    champs = llm_result["champs"]
    flat = flatten_champs(champs)
    completeness = compute_completeness(flat)
    print(f"OK  ({completeness*100:.0f}% des champs extraits)")

    # ── Pass 2 : vérification LLM ────────────────────────────
    print(f"  [Pass 2] Vérification par Qwen...", end=" ", flush=True)
    try:
        llm_verif = call_qwen_verification(flat)
        arith_ok = llm_verif.get("coherence_arithmetique", "?")
        temp_ok = llm_verif.get("coherence_temporelle", "?")
        anomalies = llm_verif.get("anomalies_detectees", [])
        print(f"OK  (arith={arith_ok}, temp={temp_ok}, anomalies={len(anomalies)})")
        if anomalies:
            for a in anomalies:
                print(f"    ! {a}")
    except Exception as e:
        print(f"FAIL — {e}")
        llm_verif = {
            "coherence_arithmetique": None,
            "coherence_temporelle": None,
            "anomalies_detectees": [],
            "erreur": str(e),
        }

    # ── Pass 3 : FinVerBench déterministe ────────────────────
    print(f"  [Pass 3] Contrôle FinVerBench...", end=" ", flush=True)
    finverbench = FinVerBenchTaxonomy.run_full_audit(flat)
    fb_summary = " | ".join(
        f"{k}={v['statut']}" for k, v in finverbench.items()
    )
    print(f"OK  ({fb_summary})")

    # ── Croisement Pass 2 vs Pass 3 ──────────────────────────
    coherence_comp = compare_coherence(finverbench, llm_verif)
    accords = [v["accord"] for v in coherence_comp.values()]
    print(f"  [Accord LLM/FinVerBench] arith={coherence_comp['arithmetique']['accord']} | temp={coherence_comp['temporel']['accord']}")

    # ── Ground truth + score ─────────────────────────────────
    gt = load_ground_truth(nom)
    accuracy = compute_accuracy(champs, gt.get("champs") if gt else None)
    score = compute_auditwen_score(completeness, accuracy, coherence_comp)

    acc_str = f"{accuracy*100:.0f}%" if accuracy is not None else "N/A"
    print(f"  → Score AuditWen : {score}/100  (complétude={completeness*100:.0f}%, exactitude={acc_str})")

    return {
        "document": nom,
        "statut": "ok",
        "scores": {
            "auditwen_score": score,
            "completeness_pct": round(completeness * 100, 1),
            "accuracy_pct": round(accuracy * 100, 1) if accuracy is not None else None,
        },
        "finverbench": finverbench,
        "llm_verification": llm_verif,
        "coherence_comparison": coherence_comp,
        "champs_extraits": champs,
    }


# ==========================================
# PIPELINE PRINCIPAL
# ==========================================

def run_auditwen_evaluation() -> dict:
    documents = sorted(
        f for f in SAMPLES_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not documents:
        print(f"Aucun document trouvé dans '{SAMPLES_DIR}/'.")
        return {}

    GROUND_TRUTH_DIR.mkdir(exist_ok=True)

    print(f"\n{'=' * 56}")
    print(f"  Étape 3 — Évaluation AuditWen")
    print(f"  Modèle  : {MODEL_NAME}")
    print(f"  Docs    : {len(documents)}")
    print(f"{'=' * 56}")

    all_results = []
    for doc_path in tqdm(documents, desc="AuditWen", unit="doc", leave=False):
        all_results.append(evaluate_document(doc_path))

    # ── Métriques agrégées ────────────────────────────────────
    ok = [r for r in all_results if r.get("statut") == "ok"]
    scores = [r["scores"]["auditwen_score"] for r in ok]
    mean_score = round(sum(scores) / len(scores), 1) if scores else 0
    comp_avg = round(sum(r["scores"]["completeness_pct"] for r in ok) / len(ok), 1) if ok else 0
    acc_vals = [r["scores"]["accuracy_pct"] for r in ok if r["scores"].get("accuracy_pct") is not None]
    acc_avg = round(sum(acc_vals) / len(acc_vals), 1) if acc_vals else None

    # Taux d'accord LLM/FinVerBench global
    all_accords = []
    for r in ok:
        for v in r.get("coherence_comparison", {}).values():
            all_accords.append(v["accord"])
    accord_rate = round(sum(all_accords) / len(all_accords) * 100, 1) if all_accords else None

    report = {
        "modele": MODEL_NAME,
        "nb_documents": len(documents),
        "metriques_globales": {
            "auditwen_score_moyen": mean_score,
            "completeness_moyenne_pct": comp_avg,
            "accuracy_moyenne_pct": acc_avg,
            "accord_llm_finverbench_pct": accord_rate,
        },
        "resultats": all_results,
    }

    REPORT_PATH.write_text(
        json.dumps(report, indent=4, ensure_ascii=False), encoding="utf-8"
    )

    # ── Affichage résumé ──────────────────────────────────────
    sep = "=" * 56
    print(f"\n{sep}")
    print(f"  RÉSULTATS — Méthode AuditWen × {MODEL_NAME}")
    print(sep)
    print(f"  {'Document':<36} {'Score':>7}  {'Compl.':>6}  {'Exact.':>6}")
    print(f"  {'─' * 52}")
    for r in all_results:
        if r.get("statut") == "error":
            print(f"  {r['document']:<36} {'ERREUR':>7}")
            continue
        s = r["scores"]
        acc_str = f"{s['accuracy_pct']}%" if s.get("accuracy_pct") is not None else "  N/A"
        print(f"  {r['document']:<36} {s['auditwen_score']:>6}/100  {s['completeness_pct']:>5}%  {acc_str:>6}")
    print(f"  {'─' * 52}")
    print(f"  {'Score moyen':<36} {mean_score:>6}/100  {comp_avg:>5}%  {f'{acc_avg}%' if acc_avg else 'N/A':>6}")
    if accord_rate is not None:
        print(f"  Accord LLM / FinVerBench : {accord_rate}%")
    print(sep)
    print(f"  Rapport : {REPORT_PATH}\n")

    return report


if __name__ == "__main__":
    run_auditwen_evaluation()
