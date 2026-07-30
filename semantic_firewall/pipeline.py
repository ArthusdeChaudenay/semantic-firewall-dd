"""
pipeline.py — Orchestrateur de certification pour un document ou un dossier DD.

Fonctions publiques :
  certify_document(filepath)            → dict  certification d'un seul document
  certify_dossier(filepaths, nom)       → dict  rapport dossier (N documents)
  certify_document_from_bytes(...)      → dict  pour l'API (upload en mémoire)
"""

import json
import re
import uuid
import sys
import time
from datetime import datetime
from pathlib import Path

from semantic_firewall.config import MAX_DOC_CHARS, MAX_LLM_CHARS
from semantic_firewall.extraction.detect_doc_type import detect_doc_type
from semantic_firewall.extraction.llm_extractor import extract_text_from_file, MODEL_NAME
from semantic_firewall.monitoring.semantic_monitor import get_monitor
from semantic_firewall.validation.anchoring import check_transcription_divergence
from semantic_firewall.validation.completeness import check_completeness
from semantic_firewall.validation.corrector import apply_corrections

OUTPUT_DIR  = Path("output")
DOSSIERS_DIR = OUTPUT_DIR / "dossiers"
DOSSIERS_DIR.mkdir(parents=True, exist_ok=True)

# Enable the separate corrector stage in production certification. Turn OFF to
# obtain the honest raw-extraction baseline (B5 in the benchmark).
ENABLE_CORRECTOR = True

# Limite de caractères — voir semantic_firewall.config. Documents extrêmement longs
# (Bank of America: ~800k chars) : on cible la section états financiers.
_MAX_DOC_CHARS = MAX_DOC_CHARS

# Marqueurs de début des états financiers (FR + EN)
_FIN_SECTION_RE = re.compile(
    r"consolidated\s+statements?\s+of\s+(operations|income|earnings|comprehensive\s+income)|"
    r"financial\s+statements\s+and\s+supplementary|"
    r"notes\s+to\s+(the\s+)?consolidated\s+financial|"
    r"compte[s]?\s+de\s+r[ée]sultat\s+consolid|"
    r"[ée]tat[s]?\s+(de\s+)?r[ée]sultat|"
    r"bilan\s+consolid[ée]",
    re.I,
)


def _prepare_text(text: str) -> str:
    """Tronque les documents trop longs en ciblant la section états financiers."""
    if len(text) <= _MAX_DOC_CHARS:
        return text
    m = _FIN_SECTION_RE.search(text)
    if m:
        start = max(0, m.start() - 500)
    else:
        start = len(text) // 4  # fallback : sauter le préambule juridique
    return text[start: start + _MAX_DOC_CHARS]

# ── helpers d'import différé pour éviter les imports circulaires ──────────────

def _get_invoice_pipeline():
    from semantic_firewall.extraction.llm_extractor import extract_document
    from semantic_firewall.validation.finverbench import FinVerBenchTaxonomy
    return extract_document, FinVerBenchTaxonomy

def _get_dd_pipeline():
    from semantic_firewall.extraction.llm_extractor import extract_text_from_file, _client, _clean_llm_response
    from semantic_firewall.validation.dd_base import build_dd_prompt, DDTaxonomy
    return extract_text_from_file, _client, _clean_llm_response, build_dd_prompt, DDTaxonomy


# ==========================================
# CERTIFICATION D'UN DOCUMENT
# ==========================================

def certify_document(filepath: str) -> dict:
    """
    Certifie un document en 3 passes :
      1. Détection du type
      2. Extraction LLM (invoice ou DD selon le type)
      3. Contrôles déterministes (FinVerBench ou DDTaxonomy)

    Retourne un dict de certification structuré.
    """
    path = Path(filepath)
    doc_name = path.name

    # ── Pass 1 : extraction texte ─────────────────────────────────────────────
    try:
        text = extract_text_from_file(filepath)
    except Exception as e:
        return _cert_error(doc_name, "inconnu", f"Extraction texte impossible : {e}")

    if not text.strip():
        return _cert_error(doc_name, "inconnu", "Document vide ou illisible.")

    # ── Détection du type ─────────────────────────────────────────────────────
    doc_type = detect_doc_type(filepath, text)
    if doc_type == "inconnu":
        return _cert_error(doc_name, "inconnu",
            "Type de document non reconnu. "
            "Nommez le fichier avec un préfixe parmi : bilan_, compte_resultat_, captable_, facture_")

    # ── Pass 2 + 3 selon le type ──────────────────────────────────────────────
    if doc_type == "facture":
        return _certify_invoice(doc_name, filepath, text)
    else:
        return _certify_dd(doc_name, doc_type, text)


def certify_document_from_bytes(filename: str, content: bytes) -> dict:
    """Variante pour l'API : écrit le fichier dans un répertoire temporaire."""
    tmp_dir = OUTPUT_DIR / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / filename
    tmp_path.write_bytes(content)
    try:
        return certify_document(str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


# ── Certification facture ──────────────────────────────────────────────────────

def _certify_invoice(doc_name: str, filepath: str, text: str) -> dict:
    extract_document, FinVerBenchTaxonomy = _get_invoice_pipeline()

    llm_result = extract_document(filepath)
    if llm_result["statut"] == "error":
        return _cert_error(doc_name, "facture", llm_result.get("message", "Erreur LLM"))

    # Aplatir les champs pour les validateurs
    flat = {}
    for k, v in llm_result.get("champs", {}).items():
        flat[k] = v.get("valeur", "")
        if "valeur_brute" in v:
            flat[f"{k}_brut"] = v["valeur_brute"]

    audit = FinVerBenchTaxonomy.run_full_audit(flat)
    anomalies = [
        {"check": k, "statut": v["statut"], "message": v.get("message", "")}
        for k, v in audit.items()
        if v["statut"] in ("FAIL", "ERROR")
    ]
    statut = "CERTIFIÉ" if not anomalies else "ANOMALIE"
    score  = _compute_score(audit)

    semantic = get_monitor().analyze(text, "facture")

    if statut == "CERTIFIÉ" and semantic.get("jsd_alert"):
        centroid_scores = semantic.get("centroid_scores", {})
        best_centroid = max(centroid_scores.values()) if centroid_scores else 0
        if best_centroid >= 20.0:
            statut = "VIGILANCE"

    return {
        "document":          doc_name,
        "doc_type":          "facture",
        "statut":            statut,
        "score_confiance":   score,
        "anomalies":         anomalies,
        "controles":         audit,
        "champs_extraits":   {k: v.get("valeur") for k, v in llm_result["champs"].items()},
        "semantic_analysis": semantic,
        "modele":            MODEL_NAME,
        "horodatage":        _now(),
    }


# ── Certification document DD ─────────────────────────────────────────────────

def _certify_dd(doc_name: str, doc_type: str, text: str) -> dict:
    extract_text_from_file, _client, _clean_llm_response, build_dd_prompt, DDTaxonomy = _get_dd_pipeline()

    # Extraction LLM (1 retry sur JSONDecodeError)
    prepared = _prepare_text(text)
    doc_text = prepared[:MAX_LLM_CHARS]
    # Journaliser toute troncature (D-minor : la perte de données ne doit jamais
    # être invisible dans les résultats).
    truncation = {
        "original_chars":  len(text),
        "prepared_chars":  len(prepared),
        "sent_to_llm_chars": len(doc_text),
        "truncated": len(doc_text) < len(text),
        "chars_dropped": max(0, len(text) - len(doc_text)),
    }
    last_err = None
    extracted = None
    _TRANSIENT = ("429", "connection error", "timeout", "service unavailable",
                  "502", "503", "504")
    for attempt in range(5):
        sleep_before = [0, 5, 15, 30, 60][attempt]
        if sleep_before:
            time.sleep(sleep_before)
        try:
            prompt = build_dd_prompt(doc_text, doc_type)
            resp   = _client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=8192,
            )
            raw_content = resp.choices[0].message.content or ""
            if not raw_content.strip():
                last_err = ValueError("Réponse vide (tentative %d/5)" % (attempt + 1))
                continue
            extracted = json.loads(_clean_llm_response(raw_content))
            break
        except json.JSONDecodeError as e:
            last_err = e
        except Exception as e:
            err_str = str(e).lower()
            if any(t in err_str for t in _TRANSIENT):
                last_err = e
                continue
            return _cert_error(doc_name, doc_type, f"Erreur LLM : {e}")
    if extracted is None:
        return _cert_error(doc_name, doc_type, f"Réponse LLM non parseable : {last_err}")

    # ── RAW extraction (what the LLM actually produced) ───────────────────────
    raw_flat = {k: str(v) if v is not None else "" for k, v in extracted.items()}

    # ── DETECTOR — runs on RAW, BEFORE any correction (D1) ─────────────────────
    # These violations and the confidence score are earned, not tautological.
    dd_audit_raw = DDTaxonomy.run_audit(raw_flat, doc_type)
    completeness = check_completeness(raw_flat, doc_type)
    anchoring    = check_transcription_divergence(raw_flat, doc_text, doc_type)

    # ── CORRECTOR — a SEPARATE, optional second system (D1) ────────────────────
    if ENABLE_CORRECTOR:
        corrected_flat, corrections = apply_corrections(raw_flat, doc_type, doc_text)
    else:
        corrected_flat, corrections = dict(raw_flat), []
    dd_audit_post = DDTaxonomy.run_audit(corrected_flat, doc_type)

    # ── Honest verdict: derived from the RAW detector + completeness + anchoring
    anomalies = []
    if completeness["statut"] != "PASS":
        anomalies.append({"check": "completeness", "statut": "FAIL",
                          "message": completeness.get("message", "")})
    for k, v in dd_audit_raw.items():
        if v["statut"] in ("FAIL", "ERROR"):
            anomalies.append({"check": k, "statut": v["statut"],
                              "message": v.get("message", "")})
    if anchoring["statut"] == "FAIL":
        anomalies.append({"check": "anchoring", "statut": "FAIL",
                          "message": anchoring.get("message", "")})

    statut = "CERTIFIÉ" if not anomalies else "ANOMALIE"
    score  = _compute_score_dd(completeness, dd_audit_raw, anchoring)

    all_controles = {"completeness": completeness, "anchoring": anchoring, **dd_audit_raw}
    semantic = get_monitor().analyze(text, doc_type)

    if statut == "CERTIFIÉ" and semantic.get("jsd_alert"):
        statut = "VIGILANCE"

    return {
        "document":          doc_name,
        "doc_type":          doc_type,
        "statut":            statut,
        "score_confiance":   score,               # computed on RAW detection
        "anomalies":         anomalies,
        "controles":         all_controles,       # RAW detector (honest)
        "controles_post_correction": dd_audit_post,
        "champs_extraits":   {k: (v if v != "" else None) for k, v in raw_flat.items()},
        "champs_corriges":   {k: (v if v != "" else None) for k, v in corrected_flat.items()},
        "corrections_appliquees": corrections,
        "semantic_analysis": semantic,
        "truncation":        truncation,
        "modele":            MODEL_NAME,
        "horodatage":        _now(),
    }


# ==========================================
# CERTIFICATION D'UN DOSSIER
# ==========================================

def certify_dossier(filepaths: list[str], entreprise_nom: str = "") -> dict:
    """
    Certifie un dossier complet (bilan + P&L + captable ou factures).
    Persiste le résultat dans output/dossiers/<uuid>.json.
    """
    dossier_id = str(uuid.uuid4())
    certifications = [certify_document(fp) for fp in filepaths]

    nb_certifies = sum(1 for c in certifications if c["statut"] == "CERTIFIÉ")
    nb_vigilance = sum(1 for c in certifications if c["statut"] == "VIGILANCE")
    nb_anomalies = sum(1 for c in certifications if c["statut"] == "ANOMALIE")
    nb_erreurs   = sum(1 for c in certifications if c["statut"] == "ERREUR")

    all_anomalies = [
        {"document": c["document"], **a}
        for c in certifications
        for a in c.get("anomalies", [])
    ]

    if nb_anomalies > 0 or nb_erreurs > 0:
        verdict = "ANOMALIE"
    elif nb_vigilance > 0:
        verdict = "VIGILANCE"
    else:
        verdict = "CERTIFIÉ"

    rapport = {
        "dossier_id":     dossier_id,
        "entreprise_nom": entreprise_nom,
        "horodatage":     _now(),
        "modele":         MODEL_NAME,
        "verdict":        verdict,
        "nb_documents":   len(certifications),
        "nb_certifies":   nb_certifies,
        "nb_vigilance":   nb_vigilance,
        "nb_anomalies":   nb_anomalies,
        "nb_erreurs":     nb_erreurs,
        "anomalies":      all_anomalies,
        "certifications": certifications,
    }

    out_path = DOSSIERS_DIR / f"{dossier_id}.json"
    out_path.write_text(json.dumps(rapport, indent=4, ensure_ascii=False), encoding="utf-8")

    return rapport


# ==========================================
# UTILITAIRES
# ==========================================

def _cert_error(doc_name: str, doc_type: str, message: str) -> dict:
    return {
        "document":        doc_name,
        "doc_type":        doc_type,
        "statut":          "ERREUR",
        "score_confiance": 0.0,
        "anomalies":       [{"check": "extraction", "statut": "ERROR", "message": message}],
        "controles":       {},
        "champs_extraits": {},
        "modele":          MODEL_NAME,
        "horodatage":      _now(),
    }


def _compute_score(audit: dict) -> float:
    checks = list(audit.values())
    if not checks:
        return 0.0
    nb_pass = sum(1 for c in checks if c.get("statut") == "PASS")
    return round(nb_pass / len(checks) * 100, 1)


def _compute_score_dd(completeness: dict, dd_audit: dict, transcription: dict | None = None) -> float:
    all_checks = [completeness] + list(dd_audit.values())
    if transcription:
        all_checks.append(transcription)
    scored = [c for c in all_checks if c.get("statut") != "SKIP"]
    if not scored:
        return 0.0
    nb_pass = sum(1 for c in scored if c.get("statut") == "PASS")
    return round(nb_pass / len(scored) * 100, 1)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ==========================================
# TEST CLI RAPIDE
# ==========================================

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== Test pipeline.py ===\n")

    samples = sorted(str(p) for p in Path("samples/dd").glob("*.txt"))

    for fp in samples:
        print(f"  → {fp}")
        cert = certify_document(fp)
        print(f"    statut : {cert['statut']}  score : {cert['score_confiance']}%")
        if cert["anomalies"]:
            for a in cert["anomalies"]:
                print(f"    ! [{a['statut']}] {a['check']} : {a['message']}")
        print()

    print("--- Test dossier complet ---")
    rapport = certify_dossier(samples, "TechVenture SAS")
    print(f"  verdict : {rapport['verdict']}")
    print(f"  certifiés : {rapport['nb_certifies']}/{rapport['nb_documents']}")
    print(f"  rapport  : output/dossiers/{rapport['dossier_id']}.json")
