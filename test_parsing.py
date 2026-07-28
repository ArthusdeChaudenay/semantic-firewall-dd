"""
test_parsing.py — Validation du pipeline de parsing document par document

Pour chaque fichier de samples/, vérifie en 3 étapes :
  [1] Extraction texte brut  (OCR / PyMuPDF / docx / txt)
  [2] Extraction LLM         (Qwen via AuditWen)
  [3] Cohérence des données  (FinVerBenchTaxonomy)

Lance avec :
    .\\venv\\Scripts\\python.exe test_parsing.py
    .\\venv\\Scripts\\python.exe test_parsing.py samples/facture_exemple.txt  (un seul fichier)
"""

import json
import re
import sys
from pathlib import Path

from benchmark_base import FinVerBenchTaxonomy
from llm_extractor import extract_text_from_file, extract_document, SUPPORTED_EXTENSIONS

SAMPLES_DIR = Path("samples")

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
AMOUNT_RE = re.compile(r"^\d+(\.\d+)?$")

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"


# ==========================================
# VALIDATIONS UNITAIRES
# ==========================================

def _validate_field_formats(champs: dict) -> list[str]:
    """Retourne la liste des anomalies de format détectées."""
    issues = []
    for date_field in ("date_facture", "date_echeance"):
        val = champs.get(date_field, {}).get("valeur", "")
        if val and not DATE_RE.match(val):
            issues.append(f"{date_field} : format inattendu '{val}' (attendu JJ/MM/AAAA)")

    for amount_field in ("montant_ht", "montant_tva", "montant_ttc"):
        val = champs.get(amount_field, {}).get("valeur", "")
        if val and not AMOUNT_RE.match(val):
            issues.append(f"{amount_field} : valeur non numérique '{val}'")

    return issues


def _check_fields_present(champs: dict) -> tuple[list[str], list[str]]:
    """Retourne (champs_ok, champs_vides)."""
    ok, empty = [], []
    for key, meta in champs.items():
        val = meta.get("valeur", "")
        (ok if val and val not in ("null", "None") else empty).append(key)
    return ok, empty


# ==========================================
# AFFICHAGE
# ==========================================

def _print_section(title: str):
    print(f"\n  {'─' * 50}")
    print(f"  {title}")
    print(f"  {'─' * 50}")


def _print_result(label: str, status: str, detail: str = ""):
    detail_str = f"  →  {detail}" if detail else ""
    print(f"  {status}  {label}{detail_str}")


def _print_champs(champs: dict, ok_fields: list, empty_fields: list):
    print(f"\n  Champs extraits ({len(ok_fields)}/{len(ok_fields)+len(empty_fields)}) :")
    for key in sorted(ok_fields):
        val = champs[key]["valeur"]
        print(f"    {PASS}  {key:<25} = {val}")
    for key in sorted(empty_fields):
        print(f"    {FAIL}  {key:<25} = (vide)")


# ==========================================
# TEST D'UN DOCUMENT
# ==========================================

def test_document(filepath: str) -> dict:
    path = Path(filepath)
    nom = path.name
    results = {"document": nom, "etapes": {}}

    print(f"\n{'=' * 56}")
    print(f"  Document : {nom}")
    print(f"{'=' * 56}")

    # ── Étape 1 : Extraction texte brut ──────────────────────
    _print_section("Étape 1 — Extraction du texte brut")
    try:
        texte = extract_text_from_file(str(path))
        nb_chars = len(texte.strip())
        if nb_chars == 0:
            _print_result("Texte extrait", FAIL, "aucun contenu extrait")
            results["etapes"]["extraction_texte"] = {"statut": "FAIL", "nb_caracteres": 0}
            return results
        _print_result("Texte extrait", PASS, f"{nb_chars} caractères")
        print(f"\n  Aperçu (150 premiers caractères) :")
        print(f"  {texte[:150].replace(chr(10), ' ').strip()!r}")
        results["etapes"]["extraction_texte"] = {"statut": "PASS", "nb_caracteres": nb_chars}
    except Exception as e:
        _print_result("Extraction texte", FAIL, str(e))
        results["etapes"]["extraction_texte"] = {"statut": "FAIL", "erreur": str(e)}
        return results

    # ── Étape 2 : Extraction LLM ─────────────────────────────
    _print_section("Étape 2 — Extraction LLM (Qwen / AuditWen)")
    llm_result = extract_document(str(path))

    if llm_result["statut"] == "error":
        _print_result("Appel LLM", FAIL, llm_result.get("message", "erreur inconnue"))
        results["etapes"]["extraction_llm"] = {"statut": "FAIL", "erreur": llm_result.get("message")}
        return results

    champs = llm_result["champs"]
    ok_fields, empty_fields = _check_fields_present(champs)
    _print_result("Appel LLM", PASS, "réponse JSON reçue et parsée")
    _print_champs(champs, ok_fields, empty_fields)

    format_issues = _validate_field_formats(champs)
    if format_issues:
        print(f"\n  Anomalies de format :")
        for issue in format_issues:
            _print_result(issue, WARN)
    else:
        _print_result("Formats des champs", PASS, "dates et montants conformes")

    results["etapes"]["extraction_llm"] = {
        "statut": "PASS" if not empty_fields else "WARN",
        "champs_ok": ok_fields,
        "champs_vides": empty_fields,
        "anomalies_format": format_issues,
    }

    # ── Étape 3 : Cohérence FinVerBench ──────────────────────
    _print_section("Étape 3 — Contrôles FinVerBench")
    flat = {}
    for k, v in champs.items():
        flat[k] = v.get("valeur", "")
        if "valeur_brute" in v:
            flat[f"{k}_brut"] = v["valeur_brute"]
    audit = FinVerBenchTaxonomy.run_full_audit(flat)

    audit_statuts = {}
    for check_name, check_result in audit.items():
        statut = check_result["statut"]
        label = check_name.capitalize().ljust(15)
        if statut == "PASS":
            _print_result(label, PASS)
        elif statut == "FAIL":
            _print_result(label, FAIL, check_result.get("message", ""))
        else:
            _print_result(label, WARN, check_result.get("message", ""))
        audit_statuts[check_name] = statut

    results["etapes"]["finverbench"] = audit_statuts
    results["audit_detail"] = audit
    return results


# ==========================================
# RÉSUMÉ GLOBAL
# ==========================================

def print_summary(all_results: list[dict]):
    print(f"\n{'=' * 56}")
    print(f"  RÉSUMÉ GLOBAL")
    print(f"{'=' * 56}")
    print(f"  {'Document':<40} {'Texte':<8} {'LLM':<8} {'Audit'}")
    print(f"  {'─' * 52}")
    for r in all_results:
        doc = r["document"][:39]
        texte_s = r["etapes"].get("extraction_texte", {}).get("statut", "N/A")
        llm_s = r["etapes"].get("extraction_llm", {}).get("statut", "N/A")
        audit_s = r["etapes"].get("finverbench", {})
        all_pass = all(v == "PASS" for v in audit_s.values()) if audit_s else False
        audit_str = "PASS" if all_pass else ("WARN/FAIL" if audit_s else "N/A")
        print(f"  {doc:<40} {texte_s:<8} {llm_s:<8} {audit_str}")
    print(f"{'=' * 56}\n")


# ==========================================
# POINT D'ENTRÉE
# ==========================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]
    else:
        targets = sorted(
            f for f in SAMPLES_DIR.iterdir()
            if f.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    if not targets:
        print(f"Aucun document trouvé dans '{SAMPLES_DIR}/'.")
        sys.exit(1)

    all_results = [test_document(str(t)) for t in targets]
    print_summary(all_results)
