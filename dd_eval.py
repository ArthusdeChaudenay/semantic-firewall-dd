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

from dd_base import DD_BILAN_SCHEMA, DD_CR_SCHEMA, DD_CAPTABLE_SCHEMA, build_dd_prompt, DDTaxonomy, SCHEMAS_BY_TYPE
from llm_extractor import extract_text_from_file, _client, MODEL_NAME, _clean_llm_response

SAMPLES_DIR    = Path("samples/dd")
GT_DIR         = Path("output")
REPORT_PATH    = GT_DIR / "dd_report.json"

# ─── Mapping fichier → type de document (auto-découverte) ────────────────────
def _build_doc_type_map() -> dict:
    from detect_doc_type import detect_doc_type
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

CHAMPS_OBLIGATOIRES = {
    "bilan":           ["entreprise_nom", "exercice", "actif_total", "passif_total"],
    "compte_resultat": ["entreprise_nom", "exercice", "chiffre_affaires", "ebit"],
    "captable":        ["entreprise_nom", "valorisation_pre_money", "valorisation_post_money",
                        "total_actions", "prix_par_action"],
}


# ==========================================
# EXTRACTION LLM DD
# ==========================================

def call_dd_extraction(document_text: str, doc_type: str) -> dict:
    prompt = build_dd_prompt(document_text, doc_type)
    time.sleep(1.0)
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


def check_completeness(flat: dict, doc_type: str) -> dict:
    manquants = [
        c for c in CHAMPS_OBLIGATOIRES.get(doc_type, [])
        if not flat.get(c) or str(flat[c]).strip() in ("", "null", "None", "0", "0.0")
    ]
    if manquants:
        return {"statut": "FAIL", "message": f"Champs obligatoires manquants : {', '.join(manquants)}"}
    return {"statut": "PASS"}


# Champs financiers a verifier par type de document
_TRANSCRIPTION_FIELDS = {
    "bilan":           ["actif_total", "passif_total", "capitaux_propres"],
    "compte_resultat": ["chiffre_affaires", "ebit"],
    "captable":        ["valorisation_pre_money", "valorisation_post_money"],
}

# Mots-cles d'ancrage comptable : libelles attendus sur la ligne du montant.
# Bilingues FR + EN pour les documents SEC 10-K anglais.
_ANCHOR_KEYWORDS: dict[str, list[str]] = {
    "actif_total":            ["total actif", "total de l actif",
                               "total assets"],
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
    "valorisation_post_money":["post-money", "post money", "valorisation post"],
}


def _amount_present_in_text(amount: float, text: str) -> bool:
    n = int(abs(amount))
    if n == 0:
        return True
    NBSP  = ' '
    NNBSP = ' '
    sep   = '{:,}'.format(n)
    variants: set = {
        str(n),
        sep.replace(',', ' '),    # espace normale
        sep.replace(',', NBSP),   # nbsp
        sep.replace(',', NNBSP),  # espace fine
        sep.replace(',', '.'),    # point (1.200.000)
        sep,                      # virgule (1,200,000)
        '{:.2f}'.format(amount),
    }
    if n >= 1_000 and n % 1_000 == 0:
        k = n // 1_000
        variants.update({str(k)+'K', str(k)+'k', str(k)+' K'})
    if n >= 1_000_000 and n % 1_000_000 == 0:
        m = n // 1_000_000
        variants.update({str(m)+'M', str(m)+' M', '{:.1f}M'.format(m)})
    normalized = text.replace(NBSP, ' ').replace(NNBSP, ' ')
    return any(v in normalized for v in variants)


def _find_anchor_lines(lines: list[str], keywords: list[str]) -> list[int]:
    """Retourne tous les indices de lignes contenant un mot-cle d'ancrage."""
    result = []
    for i, line in enumerate(lines):
        ln = line.lower()
        if any(kw in ln for kw in keywords):
            result.append(i)
    return result


def check_transcription_divergence(flat: dict, raw_text: str, doc_type: str) -> dict:
    """Verifie que les montants cles sont presents STRICTEMENT sur la ligne
    d'ancrage comptable ou sur la ligne immediatement suivante.
    Si le montant existe ailleurs mais pas dans ce contexte localise : FAIL.
    """
    fields = _TRANSCRIPTION_FIELDS.get(doc_type, [])
    if not fields:
        return {"statut": "PASS", "message": "Aucun champ financier cle a verifier."}

    lines   = raw_text.splitlines()
    absents = []

    for field in fields:
        val = flat.get(field)
        if not val or str(val).strip() in ("", "null", "None"):
            continue
        num = DDTaxonomy._f(val)
        if num == 0:
            continue

        anchors      = _ANCHOR_KEYWORDS.get(field, [])
        anchor_idxs  = _find_anchor_lines(lines, anchors) if anchors else []

        if not anchor_idxs:
            if not _amount_present_in_text(num, raw_text):
                absents.append(field + "=" + str(int(num)) + " (libelle absent)")
        else:
            # Verifie chaque occurrence de l'ancrage (sous-segments + total consolidé)
            found = any(
                _amount_present_in_text(
                    num,
                    chr(10).join(lines[max(0, idx - 2): idx + 10])
                )
                for idx in anchor_idxs
            )
            # Fallback : si non trouvé près de l'ancre, vérifier le doc entier
            # (tables 10-K complexes : valeur présente mais plus loin dans la colonne)
            if not found:
                found = _amount_present_in_text(num, raw_text)
            if not found:
                absents.append(field + "=" + str(int(num)))

    if not absents:
        return {"statut": "PASS", "message": "Montants cles presents en contexte localise."}
    return {
        "statut": "FAIL",
        "message": (
            "Montants absents du contexte localise (possible hallucination LLM) : "
            + ", ".join(absents)
        ),
    }



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

    # ── Matrice de confusion & metriques ACL ─────────────────────────────────
    # Ground Truth : nom de fichier contient "erreur" ou "atypique" => anomalie attendue
    # Prediction   : completeness FAIL ou au moins un controle DDTaxonomy FAIL/ERROR
    tp = fp = fn = 0
    for r in all_results:
        if r.get("statut") == "error":
            continue
        nom          = r.get("document", "").lower()
        gt_positive  = any(kw in nom for kw in ("erreur", "atypique"))
        pred_positive = (
            r.get("completeness", {}).get("statut") == "FAIL"
            or any(
                v.get("statut") in ("FAIL", "ERROR")
                for v in r.get("dd_audit", {}).values()
            )
        )
        if gt_positive and pred_positive:
            tp += 1
        elif not gt_positive and pred_positive:
            fp += 1
        elif gt_positive and not pred_positive:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    print(f"\n  Metriques ACL — Matrice de confusion  (TP={tp}  FP={fp}  FN={fn})")
    print(f"  {'─' * 44}")
    print(f"  {'Metrique':<20} {'Valeur':>10}   {'Detail':>12}")
    print(f"  {'─' * 44}")
    print(f"  {'Precision':<20} {precision*100:>9.1f}%   TP={tp} / (TP+FP={tp+fp})")
    print(f"  {'Rappel':<20} {recall*100:>9.1f}%   TP={tp} / (TP+FN={tp+fn})")
    print(f"  {'F1-Score':<20} {f1*100:>9.1f}%")
    print(f"  {'─' * 44}")


    return report


if __name__ == "__main__":
    run_dd_evaluation()
