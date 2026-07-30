"""
dd_eval.py — Pipeline d'évaluation pour les documents de Due Diligence VC/M&A

Pour chaque document DD dans samples/ :
  [Pass 1] Extraction texte brut (txt / pdf / docx / image)
  [Pass 2] Extraction structurée LLM via template AuditWen DD
  [Pass 3] Contrôles DDTaxonomy (équilibre bilan, EBITDA, cap table…)
  [Pass 4] Comparaison vs ground truth si disponible

Lance avec :
    .\\venv\\Scripts\\python.exe dd_eval.py
"""

import json
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tqdm import tqdm

from semantic_firewall.validation.dd_base import DD_BILAN_SCHEMA, DD_CR_SCHEMA, DD_CAPTABLE_SCHEMA, build_dd_prompt, DDTaxonomy, SCHEMAS_BY_TYPE
from semantic_firewall.extraction.llm_extractor import extract_text_from_file, _client, MODEL_NAME, _clean_llm_response
# Completeness and anchoring now live in the validation layer (single source).
from semantic_firewall.validation.completeness import CHAMPS_OBLIGATOIRES, check_completeness
from semantic_firewall.validation.anchoring import check_transcription_divergence

SAMPLES_DIR    = Path("samples/dd")
GT_DIR         = Path("output")
REPORT_PATH    = GT_DIR / "dd_report.json"

# ─── Mapping fichier → type de document (auto-découverte) ────────────────────
def _build_doc_type_map() -> dict:
    from semantic_firewall.extraction.detect_doc_type import detect_doc_type
    result = {}
    for p in sorted(SAMPLES_DIR.glob("*.txt")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        dt = detect_doc_type(str(p), text)
        if dt != "inconnu" and dt != "facture":
            result[p.name] = dt
    return result

DOC_TYPE_MAP = _build_doc_type_map()


# ==========================================
# EXTRACTION LLM DD
# ==========================================

def call_dd_extraction(document_text: str, doc_type: str) -> dict:
    prompt = build_dd_prompt(document_text, doc_type)
    # NOTE: no time.sleep here — it would contaminate the latency we claim to
    # measure (audit minor defect). Rate-limiting/back-off belongs in the client.
    response = _client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    raw = _clean_llm_response(response.choices[0].message.content)
    return json.loads(raw)


# ==========================================
# UTILITAIRES
# ==========================================

def load_ground_truth(nom_fichier: str) -> dict | None:
    gt_path = GT_DIR / f"{nom_fichier}.json"
    if gt_path.exists():
        data = json.loads(gt_path.read_text(encoding="utf-8"))
        return data.get("champs")
    return None


def normalize(val) -> str:
    if val is None:
        return ""
    s = str(val).strip().replace(",", ".").lower()
    if s in ("none", "null", "n/a"):
        return ""
    try:
        return str(float(s)) if s else ""
    except ValueError:
        return s


def compare_fields(llm: dict, gt: dict) -> dict:
    all_keys = set(gt) | set(llm)
    results = {}
    for key in sorted(all_keys):
        lv = normalize(llm.get(key))
        gv = normalize(gt.get(key, {}).get("valeur", "") if isinstance(gt.get(key), dict) else gt.get(key, ""))
        results[key] = {"llm": lv, "ground_truth": gv, "match": lv == gv}
    return results


# check_completeness  -> semantic_firewall.validation.completeness
# check_transcription_divergence -> semantic_firewall.validation.anchoring
# (both imported at the top of this module)


# ==========================================
# ÉVALUATION D'UN DOCUMENT
# ==========================================

def evaluate_dd_document(doc_path: Path, doc_type: str) -> dict:
    nom = doc_path.name
    sep = "─" * 54

    print(f"\n  {sep}")
    print(f"  Document : {nom}  [{doc_type}]")

    # ── Pass 1 : extraction texte brut ───────────────────────
    print(f"  [Pass 1] Extraction texte...", end=" ", flush=True)
    try:
        text = extract_text_from_file(str(doc_path))
        if not text.strip():
            print("FAIL — document vide")
            return {"document": nom, "doc_type": doc_type, "statut": "error", "message": "Document vide"}
        print(f"OK  ({len(text)} caractères)")
    except Exception as e:
        print(f"FAIL — {e}")
        return {"document": nom, "doc_type": doc_type, "statut": "error", "message": str(e)}

    # ── Pass 2 : extraction LLM DD ───────────────────────────
    print(f"  [Pass 2] Extraction LLM ({doc_type})...", end=" ", flush=True)
    try:
        extracted = call_dd_extraction(text, doc_type)
        print(f"OK  ({len(extracted)} champs)")
    except json.JSONDecodeError as e:
        print(f"FAIL — JSON invalide : {e}")
        return {"document": nom, "doc_type": doc_type, "statut": "error", "message": f"JSON invalide : {e}"}
    except Exception as e:
        print(f"FAIL — {e}")
        return {"document": nom, "doc_type": doc_type, "statut": "error", "message": str(e)}

    flat = {k: str(v) if v is not None else "" for k, v in extracted.items()}

    # ── Pass 3 : DDTaxonomy ──────────────────────────────────
    print(f"  [Pass 3] Contrôles DDTaxonomy...", end=" ", flush=True)
    completeness = check_completeness(flat, doc_type)
    dd_audit     = DDTaxonomy.run_audit(flat, doc_type)

    summary = " | ".join(f"{k}={v['statut']}" for k, v in dd_audit.items())
    print(f"OK  (compl={completeness['statut']} | {summary})")

    for check_name, result in dd_audit.items():
        if result["statut"] != "PASS":
            print(f"    ! [{result['statut']}] {check_name} : {result.get('message', '')}")

    # ── Pass 4 : ground truth ────────────────────────────────
    gt = load_ground_truth(nom)
    field_comparison = {}
    accuracy = None

    if gt:
        field_comparison = compare_fields(flat, gt)
        matches = sum(1 for v in field_comparison.values() if v["match"])
        total   = len(field_comparison)
        accuracy = round(matches / total * 100, 1) if total else None
        print(f"  [Pass 4] Ground truth : {accuracy}%  ({matches}/{total} champs corrects)")
    else:
        all_check_results = [completeness] + list(dd_audit.values())
        nb_pass     = sum(1 for c in all_check_results if c.get("statut") == "PASS")
        total_checks = len(all_check_results)
        accuracy = round(nb_pass / total_checks * 100, 1) if total_checks else None
        print(f"  [Pass 4] Ground truth : N/A  (taux PASS contrôles = {accuracy}%)")

    return {
        "document":          nom,
        "doc_type":          doc_type,
        "statut":            "ok",
        "completeness":      completeness,
        "dd_audit":          dd_audit,
        "champs_extraits":   flat,
        "comparaison_champs":field_comparison,
        "accuracy_pct":      accuracy,
    }


# ==========================================
# PIPELINE PRINCIPAL
# ==========================================

def run_dd_evaluation() -> dict:
    documents = [
        (SAMPLES_DIR / nom, doc_type)
        for nom, doc_type in DOC_TYPE_MAP.items()
        if (SAMPLES_DIR / nom).exists()
    ]

    if not documents:
        print("Aucun document DD trouvé dans samples/.")
        return {}

    GT_DIR.mkdir(exist_ok=True)

    sep = "=" * 56
    print(f"\n{sep}")
    print(f"  Évaluation DD AuditWen — {MODEL_NAME}")
    print(f"  Documents : {len(documents)}")
    print(f"{sep}")

    all_results = []
    for doc_path, doc_type in tqdm(documents, desc="DD-AuditWen", unit="doc", leave=False):
        all_results.append(evaluate_dd_document(doc_path, doc_type))

    # ── Métriques agrégées ────────────────────────────────────
    ok      = [r for r in all_results if r.get("statut") == "ok"]
    errors  = [r for r in all_results if r.get("statut") == "error"]
    acc_vals = [r["accuracy_pct"] for r in ok if r.get("accuracy_pct") is not None]
    acc_avg  = round(sum(acc_vals) / len(acc_vals), 1) if acc_vals else None

    # Compteurs par contrôle
    all_checks: dict[str, dict] = {}
    for r in ok:
        for check, result in r.get("dd_audit", {}).items():
            c = all_checks.setdefault(check, {"PASS": 0, "FAIL": 0, "ERROR": 0})
            c[result.get("statut", "ERROR")] = c.get(result.get("statut", "ERROR"), 0) + 1

    report = {
        "modele":       MODEL_NAME,
        "nb_documents": len(documents),
        "nb_erreurs":   len(errors),
        "accuracy_moyenne_pct": acc_avg,
        "controles":    all_checks,
        "resultats":    all_results,
    }

    REPORT_PATH.write_text(json.dumps(report, indent=4, ensure_ascii=False), encoding="utf-8")

    # ── Affichage résumé ──────────────────────────────────────
    print(f"\n{sep}")
    print(f"  RÉSULTATS DD — {MODEL_NAME}")
    print(f"{sep}")
    print(f"  {'Document':<38} {'Type':<18} {'Accuracy':>8}")
    print(f"  {'─' * 52}")

    for r in all_results:
        if r.get("statut") == "error":
            print(f"  {r['document']:<38} {'ERREUR':>26}")
            continue
        acc_str = f"{r['accuracy_pct']}%" if r.get("accuracy_pct") is not None else "N/A"
        print(f"  {r['document']:<38} {r['doc_type']:<18} {acc_str:>8}")

    print(f"  {'─' * 52}")
    if acc_avg:
        print(f"  {'Accuracy moyenne':<38} {'':18} {acc_avg:>7}%")

    print(f"\n  Contrôles DDTaxonomy :")
    for check, counts in all_checks.items():
        total = sum(counts.values())
        pr = round(counts.get("PASS", 0) / total * 100) if total else 0
        print(f"    {check:<30} {pr:>3}% PASS  {counts}")

    print(f"{sep}")
    print(f"  Rapport : {REPORT_PATH}\n")

    # NOTE (D3): the previous precision/recall/F1 "confusion matrix" derived the
    # ground-truth label from keywords in the FILENAME ("erreur"/"atypique") on
    # ~14 author-written documents. That is a self-fulfilling unit test, not an
    # evaluation, and has been REMOVED. Classification metrics must be computed
    # against XBRL-derived labels on hundreds of real filings — see
    # semantic_firewall/evaluation/run_experiment.py (experiment E1).

    return report


if __name__ == "__main__":
    run_dd_evaluation()
