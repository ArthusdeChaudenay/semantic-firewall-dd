import json
from datetime import datetime

# ==========================================
# BRIQUE 1 : FORMAT D'INSTRUCTION AUDITWEN
# ==========================================
# Ces templates standardisent les requêtes pour maximiser la précision de Qwen-7B.
# Le schéma JSON est défini explicitement pour forcer la structure de réponse.

CHAMPS_SCHEMA = {
    "numero_facture": "chaîne (ex: INV-2026-001)",
    "date_facture": "JJ/MM/AAAA",
    "date_echeance": "JJ/MM/AAAA",
    "fournisseur_nom": "chaîne",
    "fournisseur_tva": "chaîne (ex: FR12345678901)",
    "client_nom": "chaîne",
    "montant_ht": "nombre décimal normalisé sans symbole (ex: 1000.00)",
    "montant_ht_brut": "chaîne EXACTE du document, séparateurs et symbole inclus (ex: '1 000,00 €')",
    "taux_tva": "nombre entier sans % (ex: 20)",
    "montant_tva": "nombre décimal normalisé sans symbole (ex: 200.00)",
    "montant_tva_brut": "chaîne EXACTE du document (ex: '200,00 €')",
    "montant_ttc": "nombre décimal normalisé sans symbole (ex: 1200.00)",
    "montant_ttc_brut": "chaîne EXACTE du document, symbole et séparateurs inclus (ex: '1 200,00 €')",
    "devise": "chaîne ISO-4217 (ex: EUR, USD)",
}

AUDITWEN_TEMPLATES = {
    "extraction_structuree": """[INSTRUCTION D'AUDIT COMPTABLE]
Tu es un auditeur financier senior spécialisé dans la certification des comptes.
Analyse le texte de la facture fourni ci-dessous et extrait rigoureusement les informations demandées.

SCHÉMA JSON DE SORTIE OBLIGATOIRE :
{schema}

Règles de normalisation strictes :
- MONTANTS : nombre décimal avec point, sans symbole monétaire ni espace séparateur de milliers.
  Exemples valides : "1000.00", "850.50" — invalides : "1 000,00 €", "1.000,00", "1000"
- DATES : toujours au format JJ/MM/AAAA, même si le document écrit "12 juin 2026" ou "2026-06-12".
  Exemple : "12/06/2026"
- NUMÉRO DE FACTURE : valeur après N°, No., Numéro, Ref., Facture n° — conserver le code complet.
  Exemple : "INV-2026-001"
- TVA INTRACOMMUNAUTAIRE : format ISO pays (2 lettres) + 11 chiffres, sans espaces.
  Peut apparaître sous "N° TVA", "TVA intracom", "Numéro TVA", "VAT".
  Si le préfixe pays est absent, l'ajouter (France → FR).
  Exemple : "FR12345678901"
- TAUX TVA : entier sans %, sans "pct". Exemple : "20" et non "20%"
- VALEUR ABSENTE : utilise exactement null (pas "", pas "N/A", pas "inconnu").

Exemple de sortie attendue (document fictif) :
{{"numero_facture":"FAC-2025-042","date_facture":"15/03/2025","date_echeance":"14/04/2025","fournisseur_nom":"ACME SARL","fournisseur_tva":"FR98765432109","client_nom":"Beta Corp","montant_ht":"2500.00","taux_tva":"20","montant_tva":"500.00","montant_ttc":"3000.00","devise":"EUR"}}

Contraintes absolues :
- Réponds UNIQUEMENT avec le texte JSON brut correspondant au schéma ci-dessus.
- Pas de commentaire, pas d'explication, pas de balises Markdown (interdit : ```json).
- Ne jamais inventer une valeur absente du document — utilise null.
- TRANSCRIPTION FIDÈLE : recopie les montants EXACTEMENT tels qu'ils figurent dans le document,
  même s'ils semblent arithmétiquement incohérents. Les contrôles comptables sont effectués en aval.

Texte du document :
{document_text}
""",

    "verification_coherence": """[INSTRUCTION DE CONFORMITÉ FINVERBENCH]
En tant qu'auditeur certifié, effectue un contrôle de cohérence croisée sur les données extraites.
IMPORTANT : les deux règles sont totalement indépendantes — évalue chacune séparément.

  Règle 1 — Cohérence arithmétique (indépendante de la Règle 2) :
    Calcule : resultat = montant_ht + montant_tva
    Si |resultat - montant_ttc| > 0.02 : coherence_arithmetique = false
    Sinon : coherence_arithmetique = true
    Une incohérence temporelle n'implique JAMAIS une incohérence arithmétique.

  Règle 2 — Cohérence temporelle (indépendante de la Règle 1) :
    Si date_echeance < date_facture : coherence_temporelle = false
    Sinon : coherence_temporelle = true
    Une incohérence arithmétique n'implique JAMAIS une incohérence temporelle.

Exemples de raisonnement correct :

  Exemple A — erreur temporelle uniquement, arithmétique correcte :
    montant_ht=800.00, montant_tva=160.00, montant_ttc=960.00
    date_facture=25/06/2026, date_echeance=10/06/2026
    Calcul arith  : 800.00 + 160.00 = 960.00, ecart = 0.00, 0.00 <= 0.02 → coherence_arithmetique = true
    Calcul temp   : 10/06/2026 < 25/06/2026 → coherence_temporelle = false
    Reponse : {{"coherence_arithmetique": true, "coherence_temporelle": false, "anomalies_detectees": ["Date d'echeance anterieure a la date de facture"]}}

  Exemple B — erreur arithmétique uniquement, temporel correct :
    montant_ht=5000.00, montant_tva=500.00, montant_ttc=6000.00
    date_facture=10/06/2026, date_echeance=10/07/2026
    Calcul arith  : 5000.00 + 500.00 = 5500.00, ecart = 500.00, 500.00 > 0.02 → coherence_arithmetique = false
    Calcul temp   : 10/07/2026 >= 10/06/2026 → coherence_temporelle = true
    Reponse : {{"coherence_arithmetique": false, "coherence_temporelle": true, "anomalies_detectees": ["Incoherence arithmetique : HT + TVA = 5500.00 != TTC = 6000.00"]}}

Données à auditer :
{extracted_json}

Réponds uniquement avec ce JSON brut exact (sans balises Markdown, sans commentaire) :
{{
    "coherence_arithmetique": true,
    "coherence_temporelle": true,
    "anomalies_detectees": []
}}

Si une anomalie est détectée, ajoute une chaîne descriptive dans anomalies_detectees.
""",
}


# ==========================================
# BRIQUE 2 : TAXONOMIE D'ERREURS FINVERBENCH
# ==========================================
# Validations déterministes — elles ne dépendent pas du LLM.

class FinVerBenchTaxonomy:

    CHAMPS_OBLIGATOIRES = [
        "numero_facture", "date_facture", "date_echeance",
        "montant_ht", "montant_tva", "montant_ttc",
    ]

    DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]

    @staticmethod
    def check_arithmetic_error(data: dict) -> dict:
        """Détecte : HT + TVA != TTC (tolérance d'arrondi 0.02)."""
        try:
            ht = float(str(data.get("montant_ht", 0)).replace(",", ".").strip())
            tva = float(str(data.get("montant_tva", 0)).replace(",", ".").strip())
            ttc = float(str(data.get("montant_ttc", 0)).replace(",", ".").strip())

            ttc_calcule = ht + tva
            if abs(ttc_calcule - ttc) > 0.02:
                return {
                    "statut": "FAIL",
                    "erreur_type": "Arithmetic_Inconsistency",
                    "message": (
                        f"Incohérence : HT ({ht}) + TVA ({tva}) = {ttc_calcule:.2f}, "
                        f"mais le document affiche TTC ({ttc})"
                    ),
                }
            return {"statut": "PASS"}
        except (ValueError, TypeError):
            return {"statut": "ERROR", "message": "Valeurs numériques invalides ou manquantes."}

    @staticmethod
    def check_linkage_error(data: dict) -> dict:
        """Détecte : date_echeance antérieure à date_facture."""
        date_facture_str = str(data.get("date_facture", "")).strip()
        date_echeance_str = str(data.get("date_echeance", "")).strip()

        def parse_date(s: str):
            for fmt in FinVerBenchTaxonomy.DATE_FORMATS:
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        date_facture = parse_date(date_facture_str)
        date_echeance = parse_date(date_echeance_str)

        if date_facture is None or date_echeance is None:
            return {
                "statut": "ERROR",
                "message": (
                    f"Format de date non reconnu — "
                    f"date_facture='{date_facture_str}', date_echeance='{date_echeance_str}'"
                ),
            }

        if date_echeance < date_facture:
            return {
                "statut": "FAIL",
                "erreur_type": "Temporal_Incoherence",
                "message": (
                    f"Date d'échéance ({date_echeance_str}) "
                    f"antérieure à la date de facture ({date_facture_str})"
                ),
            }
        return {"statut": "PASS"}

    @staticmethod
    def check_missing_fields(data: dict) -> dict:
        """Détecte les champs obligatoires absents ou vides."""
        manquants = [
            c for c in FinVerBenchTaxonomy.CHAMPS_OBLIGATOIRES
            if not data.get(c) or str(data[c]).strip() in ("", "null", "None")
        ]
        if manquants:
            return {
                "statut": "FAIL",
                "erreur_type": "Missing_Fields",
                "message": f"Champs obligatoires manquants : {', '.join(manquants)}",
            }
        return {"statut": "PASS"}

    @staticmethod
    def check_transcription_integrity(data: dict) -> dict:
        """Détecte si la valeur normalisée LLM diverge de la valeur brute du document."""
        MONETARY_BRUT = [
            ("montant_ht",  "montant_ht_brut"),
            ("montant_tva", "montant_tva_brut"),
            ("montant_ttc", "montant_ttc_brut"),
        ]
        divergences = []

        for llm_field, brut_field in MONETARY_BRUT:
            llm_val = str(data.get(llm_field, "") or "").strip()
            brut_val = str(data.get(brut_field, "") or "").strip()

            if not llm_val or not brut_val or brut_val in ("null", "None"):
                continue

            normalized = (
                brut_val
                .replace(" ", "").replace(" ", "")
                .replace(",", ".").replace("€", "").replace("$", "").replace("£", "")
                .strip()
            )
            try:
                brut_float = float(normalized)
                llm_float  = float(llm_val)
                if abs(brut_float - llm_float) > 0.02:
                    divergences.append(
                        f"{llm_field} : document='{brut_val}' ({brut_float:.2f}) ≠ LLM={llm_float:.2f}"
                    )
            except ValueError:
                pass

        if divergences:
            return {
                "statut": "FAIL",
                "erreur_type": "Transcription_Divergence",
                "message": f"LLM a modifié la valeur du document : {' | '.join(divergences)}",
            }
        return {"statut": "PASS"}

    @staticmethod
    def check_vat_rate_consistency(data: dict) -> dict:
        """Détecte : montant_tva != montant_ht × taux_tva / 100 (tolérance 0.02)."""
        try:
            ht = float(str(data.get("montant_ht", 0)).replace(",", ".").strip())
            tva = float(str(data.get("montant_tva", 0)).replace(",", ".").strip())
            taux = float(str(data.get("taux_tva", 0)).replace(",", ".").strip())

            if taux == 0:
                if abs(tva) > 0.02:
                    return {
                        "statut": "FAIL",
                        "erreur_type": "VAT_Rate_Inconsistency",
                        "message": f"Taux TVA nul mais montant_tva={tva:.2f} non nul",
                    }
                return {"statut": "PASS"}

            tva_calculee = ht * taux / 100
            if abs(tva_calculee - tva) > 0.02:
                return {
                    "statut": "FAIL",
                    "erreur_type": "VAT_Rate_Inconsistency",
                    "message": (
                        f"Incohérence taux : HT ({ht}) × {taux}% = {tva_calculee:.2f}, "
                        f"mais montant_TVA affiché ({tva})"
                    ),
                }
            return {"statut": "PASS"}
        except (ValueError, TypeError):
            return {"statut": "ERROR", "message": "Valeurs numériques invalides ou manquantes."}

    @classmethod
    def run_full_audit(cls, data: dict) -> dict:
        """Exécute les cinq contrôles et retourne un rapport structuré."""
        return {
            "completeness":    cls.check_missing_fields(data),
            "arithmetic":      cls.check_arithmetic_error(data),
            "temporal":        cls.check_linkage_error(data),
            "vat_rate":        cls.check_vat_rate_consistency(data),
            "transcription":   cls.check_transcription_integrity(data),
        }


# ==========================================
# ZONE DE TEST UNITAIRE
# ==========================================
if __name__ == "__main__":
    print("--- Test 1 : Incohérence arithmétique ---")
    facture_erronee = {
        "numero_facture": "FAC-2026-001",
        "date_facture": "01/06/2026",
        "date_echeance": "30/06/2026",
        "montant_ht": "100.00",
        "montant_tva": "20.00",
        "montant_ttc": "150.00",  # Erreur injectée : devrait être 120.00
    }
    print(json.dumps(FinVerBenchTaxonomy.run_full_audit(facture_erronee), indent=4, ensure_ascii=False))

    print("\n--- Test 2 : Incohérence temporelle ---")
    facture_dates_inversees = {
        "numero_facture": "FAC-2026-002",
        "date_facture": "30/06/2026",
        "date_echeance": "01/06/2026",  # Erreur injectée : antérieure à la facture
        "montant_ht": "500.00",
        "montant_tva": "100.00",
        "montant_ttc": "600.00",
    }
    print(json.dumps(FinVerBenchTaxonomy.run_full_audit(facture_dates_inversees), indent=4, ensure_ascii=False))

    print("\n--- Test 3 : Facture valide ---")
    facture_valide = {
        "numero_facture": "FAC-2026-003",
        "date_facture": "12/06/2026",
        "date_echeance": "26/06/2026",
        "montant_ht": "1000.00",
        "montant_tva": "200.00",
        "montant_ttc": "1200.00",
    }
    print(json.dumps(FinVerBenchTaxonomy.run_full_audit(facture_valide), indent=4, ensure_ascii=False))
