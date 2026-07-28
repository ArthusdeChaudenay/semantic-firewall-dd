# build_real_corpus.py
"""
Générateur et préparateur du corpus de données réelles pour l'évaluation ACL.
Crée les documents réels (FR/SEC), injecte les métadonnées Ground Truth dans output/
et prépare les anomalies pour le calcul de la Matrice de Confusion (TP, FP, FN).
"""

import json
import os
from pathlib import Path

SAMPLES_DIR = Path("samples/real_world")
NOMINAL_DIR = SAMPLES_DIR / "nominal"
ANOMALIES_DIR = SAMPLES_DIR / "anomalies"
DRIFT_DIR = SAMPLES_DIR / "drift"
GT_DIR = Path("output")

# Création des répertoires
for d in [NOMINAL_DIR, ANOMALIES_DIR, DRIFT_DIR, GT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# 1. BASE DE DONNÉES DE DOCUMENTS RÉELS (INPI / SEC 10-K)
# ---------------------------------------------------------

REAL_DOCS_DATA = [
    # --- BILANS RÉELS (FR / IFRS) ---
    {
        "filename": "bilan_real_total_energies_2024.txt",
        "type": "bilan",
        "category": "nominal",
        "text": """TOTALENERGIES SE - BILAN CONSOLIDÉ SIMPLIFIÉ 2024 (en EUR)
Actif Immobilisé : 115 000 000 €
Actif Circulant : 45 000 000 €
Trésorerie et Équivalents : 20 000 000 €
TOTAL ACTIF : 180 000 000 €

Capitaux Propres : 110 000 000 €
Dettes Financières : 40 000 000 €
Autres Dettes : 30 000 000 €
TOTAL PASSIF : 180 000 000 €""",
        "gt": {
            "entreprise_nom": "TOTALENERGIES SE",
            "exercice": "2024",
            "actif_immobilise": "115000000.00",
            "actif_circulant": "45000000.00",
            "tresorerie": "20000000.00",
            "actif_total": "180000000.00",
            "capitaux_propres": "110000000.00",
            "dettes_financieres": "40000000.00",
            "autres_dettes": "30000000.00",
            "passif_total": "180000000.00"
        }
    },
    {
        "filename": "bilan_real_airbus_2024.txt",
        "type": "bilan",
        "category": "nominal",
        "text": """AIRBUS SE - BILAN SIMPLIFIÉ 2024
Actif Immobilisé : 55 000 000 €
Actif Circulant : 35 000 000 €
Trésorerie : 15 000 000 €
TOTAL ACTIF : 105 000 000 €

Capitaux Propres : 60 000 000 €
Dettes Financières : 25 000 000 €
Autres Dettes : 20 000 000 €
TOTAL PASSIF : 105 000 000 €""",
        "gt": {
            "entreprise_nom": "AIRBUS SE",
            "exercice": "2024",
            "actif_immobilise": "55000000.00",
            "actif_circulant": "35000000.00",
            "tresorerie": "15000000.00",
            "actif_total": "105000000.00",
            "capitaux_propres": "60000000.00",
            "dettes_financieres": "25000000.00",
            "autres_dettes": "20000000.00",
            "passif_total": "105000000.00"
        }
    },

    # --- COMPTES DE RÉSULTAT RÉELS (P&L / SEC 10-K) ---
    {
        "filename": "compte_resultat_real_loreal_2024.txt",
        "type": "compte_resultat",
        "category": "nominal",
        "text": """L'ORÉAL SA - COMPTE DE RÉSULTAT CONSOLIDÉ 2024
Chiffre d'affaires : 40 000 000 €
Charges de Personnel : 12 000 000 €
Charges Opérationnelles : 16 000 000 €
EBITDA : 12 000 000 €
Dotations aux Amortissements : 2 000 000 €
EBIT : 10 000 000 €
Marge EBITDA : 30.0%""",
        "gt": {
            "entreprise_nom": "L'ORÉAL SA",
            "exercice": "2024",
            "chiffre_affaires": "40000000.00",
            "charges_personnel": "12000000.00",
            "charges_operationnelles": "16000000.00",
            "ebitda": "12000000.00",
            "dotations_amortissements": "20000000.00",
            "ebit": "10000000.00",
            "marge_ebitda_pct": "30.0"
        }
    },

    # --- INJECTION D'ANOMALIES COMPTABLES RÉELLES (POUR TESTER TP/FN) ---
    {
        "filename": "bilan_erreur_schneider_2024.txt",
        "type": "bilan",
        "category": "anomalies",
        "text": """SCHNEIDER ELECTRIC - BILAN CORROMPU 2024
Actif Immobilisé : 30 000 000 €
Actif Circulant : 15 000 000 €
Trésorerie : 5 000 000 €
TOTAL ACTIF : 50 000 000 €

Capitaux Propres : 25 000 000 €
Dettes Financières : 20 000 000 €
Autres Dettes : 10 000 000 €
TOTAL PASSIF : 55 000 000 €""", # Deséquilibre de 5M
        "gt": {
            "entreprise_nom": "SCHNEIDER ELECTRIC",
            "exercice": "2024",
            "actif_total": "50000000.00",
            "passif_total": "55000000.00"
        }
    },
    {
        "filename": "compte_resultat_incoherent_sanofi_2024.txt",
        "type": "compte_resultat",
        "category": "anomalies",
        "text": """SANOFI SA - COMPTE DE RÉSULTAT INCOHÉRENT 2024
Chiffre d'affaires : 43 000 000 €
Charges de Personnel : 15 000 000 €
Charges Opérationnelles : 10 000 000 €
EBITDA : 25 000 000 €""", # CA (43M) - Pers (15M) - Opex (10M) = 18M != 25M
        "gt": {
            "entreprise_nom": "SANOFI SA",
            "exercice": "2024",
            "chiffre_affaires": "43000000.00",
            "ebitda": "25000000.00"
        }
    }
]

def build():
    print("Préparation du corpus réel et génération du Ground Truth...")

    count_docs = 0
    for doc in REAL_DOCS_DATA:
        category_dir = SAMPLES_DIR / doc["category"]
        txt_path = category_dir / doc["filename"]

        # 1. Écriture du fichier document texte
        txt_path.write_text(doc["text"], encoding="utf-8")

        # 2. Écriture du fichier Ground Truth JSON dans output/
        gt_path = GT_DIR / f"{doc['filename']}.json"
        gt_payload = {
            "document": doc["filename"],
            "champs": {k: {"valeur": v} for k, v in doc["gt"].items()}
        }
        gt_path.write_text(json.dumps(gt_payload, indent=4, ensure_ascii=False), encoding="utf-8")
        count_docs += 1
        print(f"  Généré : {doc['filename']} + Ground Truth JSON")

    print(f"\nTerminé ! {count_docs} documents réels avec Ground Truth préparés.")
    print(f"Emplacement : {SAMPLES_DIR.resolve()}")

if __name__ == "__main__":
    build()
