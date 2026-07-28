"""
detect_doc_type.py — Détection automatique du type de document DD ou facture.

Priorité :
  1. Nom de fichier (préfixe/mot-clé)
  2. Contenu texte (500 premiers caractères, keywords)
  3. Fallback → "inconnu"

Types reconnus : "bilan", "compte_resultat", "captable", "facture"
"""

import re
from pathlib import Path

# ── Patterns sur le nom de fichier ────────────────────────────────────────────
_FILENAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"bilan",            re.I), "bilan"),
    (re.compile(r"compte.?r[eé]sultat|p[&/]l|(?:^|_)pl(?:_|$)", re.I), "compte_resultat"),
    (re.compile(r"cap.?table|captable|capitalisation|actionnariat", re.I), "captable"),
    (re.compile(r"facture|invoice|fact",  re.I), "facture"),
]

# ── Keywords dans le contenu ──────────────────────────────────────────────────
_CONTENT_RULES: list[tuple[list[str], str]] = [
    (["TOTAL ACTIF", "TOTAL PASSIF", "ACTIF IMMOBILISÉ", "CAPITAUX PROPRES",
      "BILAN SIMPLIFIÉ", "BILAN CONSOLIDÉ", "BILAN FINANCIER",
      "TOTAL ASSETS", "TOTAL LIABILITIES", "SHAREHOLDERS EQUITY",
      "BALANCE SHEET", "CONSOLIDATED BALANCE"],                            "bilan"),
    (["EBITDA", "EBIT", "CHIFFRE D'AFFAIRES", "RÉSULTAT NET",
      "COMPTE DE RÉSULTAT", "RÉSULTAT D'EXPLOITATION", "MARGE",
      "INCOME STATEMENT", "REVENUE", "NET INCOME", "OPERATING INCOME",
      "GROSS PROFIT", "PROFIT AND LOSS", "PROFIT & LOSS"],                 "compte_resultat"),
    (["CAP TABLE", "CAPITALISATION", "VALORISATION", "PRE-MONEY",
      "POST-MONEY", "ACTIONNAIRES", "FONDATEURS", "ESOP",
      "CAPITALIZATION TABLE", "SHARE CAPITAL", "EQUITY OWNERSHIP"],        "captable"),
    (["FACTURE", "INVOICE", "MONTANT HT", "MONTANT TTC", "TVA",
      "DATE D'ÉCHÉANCE", "N° TVA", "FOURNISSEUR",
      "BILL TO", "PAYMENT DUE", "PURCHASE ORDER"],                         "facture"),
]


def detect_doc_type(filepath: str, text: str = "") -> str:
    """
    Détecte le type de document à partir du nom de fichier puis du contenu.

    Args:
        filepath : chemin ou nom du fichier
        text     : texte brut extrait du document (optionnel)

    Returns:
        "bilan" | "compte_resultat" | "captable" | "facture" | "inconnu"
    """
    stem = Path(filepath).stem.lower()

    # 1. Nom de fichier
    for pattern, doc_type in _FILENAME_RULES:
        if pattern.search(stem):
            return doc_type

    # 2. Contenu (3000 premiers caractères, insensible à la casse)
    if text:
        snippet = text[:3000].upper()
        for keywords, doc_type in _CONTENT_RULES:
            if any(kw in snippet for kw in keywords):
                return doc_type

    return "inconnu"


# ── Tests rapides ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cases = [
        ("bilan_techventure_2025.pdf",    "",                                "bilan"),
        ("compte_resultat_q3.xlsx",       "",                                "compte_resultat"),
        ("captable_serie_a.txt",          "",                                "captable"),
        ("facture_001.pdf",               "",                                "facture"),
        ("document_inconnu.pdf",          "TOTAL ACTIF : 2 500 000 €",       "bilan"),
        ("rapport.pdf",                   "EBITDA 40% CHIFFRE D'AFFAIRES",   "compte_resultat"),
        ("data.xlsx",                     "",                                "inconnu"),
    ]
    for path, text, expected in cases:
        result = detect_doc_type(path, text)
        ok = "✓" if result == expected else "✗"
        print(f"  {ok}  {path!r:40s} → {result!r}  (attendu: {expected!r})")
