"""
dd_base.py — Schémas, templates AuditWen et taxonomie de validation
               pour les documents de Due Diligence VC / M&A

Types de documents couverts :
  - bilan          : bilan simplifié (balance sheet)
  - compte_resultat: P&L avec EBITDA / EBIT / résultat net
  - captable       : tableau de capitalisation (Série A / B / …)
"""

import json
import re

# ==========================================
# SCHÉMAS D'EXTRACTION PAR TYPE
# ==========================================

DD_BILAN_SCHEMA = {
    "entreprise_nom":    "chaîne",
    "exercice":          "AAAA (ex: 2025)",
    "actif_immobilise":  "nombre décimal (ex: 1200000.00)",
    "actif_circulant":   "nombre décimal (ex: 850000.00)",
    "tresorerie":        "nombre décimal (ex: 450000.00)",
    "actif_total":       "nombre décimal",
    "capitaux_propres":  "nombre décimal",
    "dettes_financieres":"nombre décimal",
    "autres_dettes":     "nombre décimal",
    "passif_total":      "nombre décimal",
}

DD_CR_SCHEMA = {
    "entreprise_nom":              "chaîne",
    "exercice":                    "AAAA",
    "chiffre_affaires":            "nombre décimal",
    "charges_personnel":           "nombre décimal",
    "charges_operationnelles":     "nombre décimal (hors personnel et amortissements)",
    "ebitda":                      "nombre décimal",
    "dotations_amortissements":    "nombre décimal",
    "ebit":                        "nombre décimal",
    "charges_financieres":         "nombre décimal",
    "resultat_net":                "nombre décimal",
    "marge_ebitda_pct":            "nombre décimal ex: 40.0 pour 40%",
}

DD_CAPTABLE_SCHEMA = {
    "entreprise_nom":           "chaîne",
    "date_captable":            "JJ/MM/AAAA",
    "valorisation_pre_money":   "nombre décimal",
    "montant_levee":            "nombre décimal",
    "valorisation_post_money":  "nombre décimal",
    "total_actions":            "entier",
    "prix_par_action":          "nombre décimal",
    "fondateurs_pct":           "nombre décimal (ex: 60.0 pour 60%)",
    "investisseurs_pct":        "nombre décimal",
    "esop_pct":                 "nombre décimal",
    "autres_actionnaires_pct":  "nombre décimal (null si absent)",
    "total_pct":                "nombre décimal (doit être ≈ 100.0)",
}

# ==========================================
# TEMPLATES AUDITWEN — DOCUMENTS DD
# ==========================================

DD_AUDITWEN_TEMPLATES = {
    "bilan": """[INSTRUCTION D'AUDIT FINANCIER — BILAN / BALANCE SHEET]
Tu es un auditeur M&A senior. Extrais les chiffres clés du bilan ci-dessous.
Le document peut être en français OU en anglais — adapte la lecture des labels en conséquence.

SCHÉMA JSON DE SORTIE :
{schema}

CORRESPONDANCES BILINGUES (français → anglais) :
- entreprise_nom     : nom de la société, "Company name", en-tête du rapport
- exercice           : année fiscale, "Fiscal year", "As of December 31,…"
- actif_immobilise   : "Actif immobilisé", "Immobilisations", "Non-current assets",
                       "Long-term assets", "Property plant and equipment net",
                       "Goodwill and intangibles"
- actif_circulant    : "Actif circulant", "Current assets" (hors trésorerie),
                       "Accounts receivable", "Inventories", "Other current assets"
- tresorerie         : "Trésorerie", "Cash and cash equivalents", "Short-term investments",
                       "Cash and equivalents"
- actif_total        : "Total actif", "TOTAL ACTIF", "Total assets" → OBLIGATOIRE
- capitaux_propres   : "Capitaux propres", "Shareholders equity", "Stockholders equity",
                       "Total equity", "Total stockholders equity"
- dettes_financieres : "Dettes financières", "Emprunts", "Long-term debt",
                       "Short-term borrowings", "Notes payable", "Convertible notes",
                       "Finance lease obligations"
- autres_dettes      : toute ligne de passif hors capitaux propres et dettes financières.
                       FR : "Dettes d'exploitation", "Dettes fournisseurs", "Dettes fiscales".
                       EN : "Accounts payable", "Accrued liabilities", "Deferred revenue",
                            "Operating liabilities", "Trade payables", "Other current liabilities"
- passif_total       : "Total passif", "TOTAL PASSIF", "Total liabilities and equity",
                       "Total liabilities and stockholders equity" → OBLIGATOIRE

Règles :
- MONTANTS : recopie la valeur numérique EXACTEMENT telle qu'elle figure dans le document
  (ex: si le document affiche "22,476" → extraire 22476.0). Ne PAS multiplier ni convertir.
- EXERCICE EN COURS : extraire les chiffres de la période la plus récente présentée.
- VALEURS CONSOLIDÉES : totaux du groupe, pas les chiffres par entité ou segment.
- EXERCICE : année seule, 4 chiffres (ex: 2025).
- Réponds UNIQUEMENT avec le JSON brut. Pas de commentaire, pas de balises Markdown.
- Valeur absente → null.

Document :
{document_text}
""",

    "compte_resultat": """[INSTRUCTION D'AUDIT FINANCIER — COMPTE DE RÉSULTAT / P&L]
Tu es un auditeur M&A senior. Extrais les indicateurs P&L du document ci-dessous.
Le document peut être en français OU en anglais — adapte la lecture des labels en conséquence.

SCHÉMA JSON DE SORTIE :
{schema}

CORRESPONDANCES BILINGUES (français → anglais) :
- entreprise_nom       : nom de la société, "Company name", en-tête du rapport
- exercice             : année fiscale, "Fiscal year", "Year ended"
- chiffre_affaires     : "Chiffre d'affaires", "CA", "Revenue", "Net sales",
                         "Total net sales", "Net revenues", "Total revenues",
                         "Total net revenues", "Total revenue" → OBLIGATOIRE
                         (Amazon: "Total net sales"; Apple: "Net sales")
                         ÉTABLISSEMENTS FINANCIERS (banques, assureurs, sociétés de paiement) :
                         chercher "Net revenues", "Total net revenues". Si absent, CALCULER :
                         Net interest income + Total noninterest income (ou équivalent).
                         (Wells Fargo, JPMorgan, Visa, Mastercard : utiliser cette règle)
- charges_personnel    : "Charges de personnel", "Personnel", "Employee compensation",
                         "Salaries", "Labor costs", "Employee benefits expense"
- charges_operationnelles: "Charges opérationnelles", "Cost of goods sold", "Cost of revenue",
                         "Operating expenses (hors personnel et D&A)", "SG&A", "R&D expenses"
- ebitda               : CALCULER systématiquement : ebitda = ebit + dotations_amortissements.
                         NE PAS utiliser un "Adjusted EBITDA" ou "Non-GAAP EBITDA" du document.
                         Si un EBITDA explicite GAAP est présent, vérifier qu'il est ≈ ebit + D&A.
                         ATTENTION : ebitda DOIT être différent de ebit quand D&A > 0.
                         Exemple : ebit=10 000, D&A=2 000 → ebitda=12 000 (jamais 10 000).
- dotations_amortissements: "Dotations aux amortissements" dans le compte de résultat OU
                         "Depreciation and amortization" / "D&A" tel qu'indiqué dans le
                         TABLEAU DES FLUX DE TRÉSORERIE (cash flow statement, section activités
                         opérationnelles). NE PAS utiliser l'"accumulated depreciation" du bilan.
- ebit                 : Extraire UNIQUEMENT depuis le tableau "Consolidated Statements of Income"
                         (ou "Consolidated Statements of Operations") — la ligne totale du groupe.
                         Labels : "EBIT", "Operating income", "Operating profit",
                         "Income from operations", "Total operating income".
                         NE PAS utiliser : segments, MD&A narrative, tableaux non-GAAP,
                         colonnes d'années antérieures, ni ajustements.
                         ÉTABLISSEMENTS FINANCIERS : "Income before income tax expense",
                         "Pre-tax income", "Income before taxes".
- charges_financieres  : "Charges financières", "Interest expense", "Net interest expense"
- resultat_net         : "Résultat net", "Net income", "Net earnings", "Net profit",
                         "Net income attributable to…"
- marge_ebitda_pct     : "Marge EBITDA", si absente calcule EBITDA / chiffre_affaires × 100

Règles :
- MONTANTS : recopie la valeur numérique EXACTEMENT telle qu'elle figure dans le tableau
  (ex: si le document affiche "24,948" → extraire 24948.0 ; si "21.5" → 21.5).
  Ne PAS multiplier par 1M ou 1B. Ne PAS convertir d'unités.
- EXERCICE EN COURS : extraire les chiffres de l'exercice fiscal le plus récent présenté.
  Ne pas utiliser les colonnes des années comparatives précédentes.
- VALEURS CONSOLIDÉES : utiliser les totaux consolidés du groupe, pas les chiffres par segment.
- MARGE EBITDA : nombre décimal en pourcentage, sans symbole % (ex: 40.0 pour 40%).
- EXERCICE : année seule, 4 chiffres (ex: 2025).
- Réponds UNIQUEMENT avec le JSON brut. Pas de commentaire, pas de balises Markdown.
- Valeur absente → null.

Document :
{document_text}
""",

    "captable": """[INSTRUCTION D'AUDIT FINANCIER — CAP TABLE]
Tu es un auditeur M&A spécialisé en structuration du capital.
Extrais les données du tableau de capitalisation ci-dessous.

SCHÉMA JSON DE SORTIE :
{schema}

Règles :
- VALORISATIONS et MONTANTS : nombre décimal sans symbole (ex: 8000000.00).
- POURCENTAGES : nombre décimal sans symbole % (ex: 60.0 pour 60%).
- DATE : format JJ/MM/AAAA.
- TOTAL_ACTIONS : entier sans séparateur de milliers (ex: 1000000).
- TRANSCRIPTION FIDÈLE : recopie les chiffres EXACTEMENT tels qu'ils figurent dans le document.
- Réponds UNIQUEMENT avec le JSON brut. Pas de commentaire, pas de balises Markdown.
- Valeur absente → null.

Document :
{document_text}
""",
}

SCHEMAS_BY_TYPE = {
    "bilan":           DD_BILAN_SCHEMA,
    "compte_resultat": DD_CR_SCHEMA,
    "captable":        DD_CAPTABLE_SCHEMA,
}


def build_dd_prompt(document_text: str, doc_type: str) -> str:
    schema = SCHEMAS_BY_TYPE[doc_type]
    template = DD_AUDITWEN_TEMPLATES[doc_type]
    return template.format(
        schema=json.dumps(schema, indent=4, ensure_ascii=False),
        document_text=document_text,
    )


# ==========================================
# TAXONOMIE DE VALIDATION DD
# ==========================================

class DDTaxonomy:

    @staticmethod
    def _f(val, default: float = 0.0) -> float:
        try:
            s = str(val or default).strip()
            s = s.replace("\xa0", "").replace("$", "").replace("€", "").replace("£", "").replace("¥", "").replace("%", "")

            # Parenthèses comptables pour négatifs : (22,476) → -22476
            negative = s.startswith("(") and s.endswith(")")
            if negative:
                s = s[1:-1].strip()

            # Multiplicateurs textuels (ex: "22,476 million", "1.5B", "22.5bn")
            multiplier = 1.0
            sl = s.lower().replace(" ", "")
            for suffix, mult in [
                ("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("thousand", 1e3),
                ("bn", 1e9), ("mm", 1e6),
            ]:
                if sl.endswith(suffix):
                    multiplier = mult
                    s = s[:-(len(suffix))].strip()
                    sl = s.lower().replace(" ", "")
                    break
            else:
                m = re.search(r"(?i)([bmk])\s*$", sl)
                if m:
                    sfx = m.group(1).lower()
                    multiplier = {"b": 1e9, "m": 1e6, "k": 1e3}[sfx]
                    s = s[:m.start()].strip()

            s = s.replace(" ", "")
            if "." in s and "," in s:
                if s.rfind(".") > s.rfind(","):
                    s = s.replace(",", "")           # anglais : 1,234.56 → 1234.56
                else:
                    s = s.replace(".", "").replace(",", ".")  # français : 1.234,56 → 1234.56
            elif "," in s:
                parts = s.split(",")
                if len(parts) == 2 and len(parts[-1]) != 3:
                    s = s.replace(",", ".")          # décimal français : 1,5
                else:
                    s = s.replace(",", "")           # séparateur de milliers : 1,234

            result = float(s) * multiplier
            return -result if negative else result
        except (ValueError, TypeError):
            return default

    # ── BILAN ─────────────────────────────────────────────────

    @classmethod
    def check_balance_sheet_equilibrium(cls, data: dict) -> dict:
        """Actif total == Passif total."""
        actif  = cls._f(data.get("actif_total"))
        passif = cls._f(data.get("passif_total"))
        if actif == 0 and passif == 0:
            return {"statut": "ERROR", "message": "Actif et passif totaux absents."}
        if abs(actif - passif) > 1.0:
            return {
                "statut": "FAIL",
                "erreur_type": "Balance_Sheet_Imbalance",
                "message": (
                    f"Actif total ({actif:,.0f} €) ≠ Passif total ({passif:,.0f} €), "
                    f"écart = {abs(actif - passif):,.0f} €"
                ),
            }
        return {"statut": "PASS"}

    @classmethod
    def check_balance_sheet_decomposition(cls, data: dict) -> dict:
        """Actif total == Actif immobilisé + Actif circulant + Trésorerie."""
        immo   = cls._f(data.get("actif_immobilise"))
        circ   = cls._f(data.get("actif_circulant"))
        treso  = cls._f(data.get("tresorerie"))
        total  = cls._f(data.get("actif_total"))
        if total == 0:
            return {"statut": "ERROR", "message": "Actif total absent."}
        computed = immo + circ + treso
        if abs(computed - total) > 1.0:
            return {
                "statut": "FAIL",
                "erreur_type": "Balance_Sheet_Decomposition",
                "message": (
                    f"Immo ({immo:,.0f}) + Circ ({circ:,.0f}) + Tréso ({treso:,.0f}) "
                    f"= {computed:,.0f} ≠ Actif total ({total:,.0f})"
                ),
            }
        return {"statut": "PASS"}

    @classmethod
    def run_bilan_audit(cls, data: dict) -> dict:
        return {
            "equilibre_bilan":      cls.check_balance_sheet_equilibrium(data),
            "decomposition_actif":  cls.check_balance_sheet_decomposition(data),
        }

    # ── COMPTE DE RÉSULTAT ────────────────────────────────────

    @classmethod
    def check_ebitda_consistency(cls, data: dict) -> dict:
        """EBITDA ≈ EBIT + D&A (relation universelle GAAP/IFRS, tolérance 15%)."""
        ca     = cls._f(data.get("chiffre_affaires"))
        ebit   = cls._f(data.get("ebit"))
        da     = cls._f(data.get("dotations_amortissements"))
        ebitda = cls._f(data.get("ebitda"))
        if ca == 0:
            return {"statut": "SKIP", "message": "Chiffre d'affaires absent — vérification impossible."}
        if ebitda == 0:
            return {"statut": "SKIP", "message": "EBITDA absent — vérification impossible."}
        if da == 0:
            return {"statut": "SKIP", "message": "D&A absent — vérification impossible."}
        computed = ebit + da
        tol = max(abs(ebitda) * 0.15, 1.0)
        if abs(computed - ebitda) > tol:
            return {
                "statut": "FAIL",
                "erreur_type": "EBITDA_Inconsistency",
                "message": (
                    f"EBIT ({ebit:,.0f}) + D&A ({da:,.0f}) "
                    f"= {computed:,.0f} ≠ EBITDA ({ebitda:,.0f})"
                ),
            }
        return {"statut": "PASS"}

    @classmethod
    def check_ebit_consistency(cls, data: dict) -> dict:
        """EBIT ≈ EBITDA - D&A (tolérance 15%)."""
        ebitda  = cls._f(data.get("ebitda"))
        da      = cls._f(data.get("dotations_amortissements"))
        ebit    = cls._f(data.get("ebit"))
        if ebitda == 0:
            return {"statut": "SKIP", "message": "EBITDA absent — vérification impossible."}
        if da == 0:
            return {"statut": "SKIP", "message": "D&A absent — vérification impossible."}
        computed = ebitda - da
        ref = abs(ebit) if ebit != 0 else abs(computed)
        tol = max(ref * 0.15, 1.0)
        if abs(computed - ebit) > tol:
            return {
                "statut": "FAIL",
                "erreur_type": "EBIT_Inconsistency",
                "message": f"EBITDA ({ebitda:,.0f}) - D&A ({da:,.0f}) = {computed:,.0f} ≠ EBIT ({ebit:,.0f})",
            }
        return {"statut": "PASS"}

    @classmethod
    def check_ebitda_margin(cls, data: dict) -> dict:
        """Marge EBITDA == EBITDA / CA × 100 (tolérance 1.0 pt pour arrondi LLM)."""
        ca      = cls._f(data.get("chiffre_affaires"))
        ebitda  = cls._f(data.get("ebitda"))
        marge   = cls._f(data.get("marge_ebitda_pct"))
        if ca == 0:
            return {"statut": "SKIP", "message": "Chiffre d'affaires absent — vérification impossible."}
        if marge == 0:
            return {"statut": "SKIP", "message": "Marge EBITDA absente — vérification impossible."}
        computed = ebitda / ca * 100
        if abs(computed - marge) > 1.0:
            return {
                "statut": "FAIL",
                "erreur_type": "EBITDA_Margin_Inconsistency",
                "message": f"EBITDA/CA = {computed:.1f}% ≠ marge affichée ({marge}%)",
            }
        return {"statut": "PASS"}

    @classmethod
    def run_cr_audit(cls, data: dict) -> dict:
        return {
            "coherence_ebitda":        cls.check_ebitda_consistency(data),
            "coherence_ebit":          cls.check_ebit_consistency(data),
            "coherence_marge_ebitda":  cls.check_ebitda_margin(data),
        }

    # ── CAP TABLE ─────────────────────────────────────────────

    @classmethod
    def check_captable_total_pct(cls, data: dict) -> dict:
        """Somme des pourcentages actionnaires ≈ 100%."""
        PCT_FIELDS = [
            "fondateurs_pct", "investisseurs_pct",
            "esop_pct", "autres_actionnaires_pct",
        ]
        total = sum(cls._f(data.get(f)) for f in PCT_FIELDS)
        if total == 0:
            total = cls._f(data.get("total_pct"))
        if abs(total - 100.0) > 0.5:
            return {
                "statut": "FAIL",
                "erreur_type": "CapTable_Total_Inconsistency",
                "message": f"Somme des parts = {total:.2f}% ≠ 100%",
            }
        return {"statut": "PASS"}

    @classmethod
    def check_post_money_valuation(cls, data: dict) -> dict:
        """Valorisation post-money == pré-money + montant levée."""
        pre   = cls._f(data.get("valorisation_pre_money"))
        levee = cls._f(data.get("montant_levee"))
        post  = cls._f(data.get("valorisation_post_money"))
        if pre == 0 or post == 0:
            return {"statut": "ERROR", "message": "Valorisation pré ou post absente."}
        if levee > 0:
            computed = pre + levee
            if abs(computed - post) > 1.0:
                return {
                    "statut": "FAIL",
                    "erreur_type": "PostMoney_Inconsistency",
                    "message": (
                        f"Pré ({pre:,.0f}) + Levée ({levee:,.0f}) = {computed:,.0f} ≠ Post ({post:,.0f})"
                    ),
                }
        return {"statut": "PASS"}

    @classmethod
    def check_price_per_share(cls, data: dict) -> dict:
        """Prix par action ≈ valorisation post-money / total actions (tolérance 1%)."""
        post    = cls._f(data.get("valorisation_post_money"))
        actions = cls._f(data.get("total_actions"))
        prix    = cls._f(data.get("prix_par_action"))
        if actions == 0 or post == 0:
            return {"statut": "ERROR", "message": "Total actions ou valorisation post absente."}
        if prix == 0:
            return {"statut": "ERROR", "message": "Prix par action absent."}
        computed  = post / actions
        tolerance = max(computed * 0.01, 0.001)
        if abs(computed - prix) > tolerance:
            return {
                "statut": "FAIL",
                "erreur_type": "PricePerShare_Inconsistency",
                "message": (
                    f"Post ({post:,.0f}) / Actions ({actions:,.0f}) = {computed:.4f} ≠ Prix ({prix:.4f})"
                ),
            }
        return {"statut": "PASS"}

    @classmethod
    def run_captable_audit(cls, data: dict) -> dict:
        return {
            "total_pct":          cls.check_captable_total_pct(data),
            "valorisation_post":  cls.check_post_money_valuation(data),
            "prix_par_action":    cls.check_price_per_share(data),
        }

    # ── DISPATCH AUTO ─────────────────────────────────────────

    @classmethod
    def run_audit(cls, data: dict, doc_type: str) -> dict:
        """Dispatch vers le bon audit selon le type de document."""
        if doc_type == "bilan":
            return cls.run_bilan_audit(data)
        if doc_type == "compte_resultat":
            return cls.run_cr_audit(data)
        if doc_type == "captable":
            return cls.run_captable_audit(data)
        return {"error": f"Type inconnu : '{doc_type}'"}


# ==========================================
# ZONE DE TEST UNITAIRE
# ==========================================
if __name__ == "__main__":
    import json as _json, sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("─── Bilan équilibré ───")
    bilan_ok = {
        "actif_immobilise": "1200000", "actif_circulant": "850000",
        "tresorerie": "450000", "actif_total": "2500000",
        "capitaux_propres": "1500000", "dettes_financieres": "700000",
        "autres_dettes": "300000", "passif_total": "2500000",
    }
    print(_json.dumps(DDTaxonomy.run_bilan_audit(bilan_ok), indent=2, ensure_ascii=False))

    print("\n─── Bilan déséquilibré (passif gonflé) ───")
    bilan_err = {**bilan_ok, "passif_total": "2650000"}
    print(_json.dumps(DDTaxonomy.run_bilan_audit(bilan_err), indent=2, ensure_ascii=False))

    print("\n─── CR cohérent ───")
    cr_ok = {
        "chiffre_affaires": "3200000", "charges_personnel": "1280000",
        "charges_operationnelles": "640000", "ebitda": "1280000",
        "dotations_amortissements": "320000", "ebit": "960000",
        "marge_ebitda_pct": "40.0",
    }
    print(_json.dumps(DDTaxonomy.run_cr_audit(cr_ok), indent=2, ensure_ascii=False))

    print("\n─── Cap table cohérente ───")
    ct_ok = {
        "valorisation_pre_money": "8000000", "montant_levee": "2000000",
        "valorisation_post_money": "10000000", "total_actions": "1000000",
        "prix_par_action": "10.0",
        "fondateurs_pct": "60", "investisseurs_pct": "20",
        "esop_pct": "10", "autres_actionnaires_pct": "10",
    }
    print(_json.dumps(DDTaxonomy.run_captable_audit(ct_ok), indent=2, ensure_ascii=False))
