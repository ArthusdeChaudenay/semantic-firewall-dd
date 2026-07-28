"""
benchmark_compare.py — Benchmark comparatif des 4 méthodes d'extraction financière.

Modes :
  Mode 4 docs (défaut)  : 4 P&L emblématiques  → output/benchmark_results.json
  Mode 49 docs (--full) : corpus complet 49 docs → output/benchmark_results_full.json

Usage :
    .\\venv\\Scripts\\python.exe benchmark_compare.py                  # 4 docs
    .\\venv\\Scripts\\python.exe benchmark_compare.py --no-cache       # 4 docs, force recalcul
    .\\venv\\Scripts\\python.exe benchmark_compare.py --full           # 49 docs (reprise auto)
    .\\venv\\Scripts\\python.exe benchmark_compare.py --full --no-cache  # 49 docs, force recalcul
"""

import re
import json
import time
import sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Infrastructure partagée ────────────────────────────────────────────────────
from llm_extractor import extract_text_from_file, _client, _clean_llm_response, MODEL_NAME
from dd_base import DDTaxonomy
from pipeline import _prepare_text, _certify_dd

OUTPUT_DIR        = Path("output")
RESULTS_PATH      = OUTPUT_DIR / "benchmark_results.json"
RESULTS_FULL_PATH = OUTPUT_DIR / "benchmark_results_full.json"

_PDF = "samples/real_world/sec_10k_pdf"

def _d(id, nom, secteur, annee, fname, part, defi):
    return {"id": id, "nom": nom, "secteur": secteur, "annee": annee,
            "path": f"{_PDF}/{fname}", "particularite": part, "defi": defi}

# ── 4 docs emblématiques (mode défaut) ───────────────────────────────────────
DOCS = [
    _d("apple",      "Apple Inc.",         "Technologie",          2025,
       "compte_resultat_10k_Apple_Inc_2025.pdf",
       "P&L 3 colonnes d'années, Net sales clairement libellé",
       "Colonnes multi-années — risque de lire la mauvaise colonne"),
    _d("walmart",    "Walmart Inc.",        "Grande Distribution",  2026,
       "compte_resultat_10k_Walmart_Inc_2026.pdf",
       "EBIT < D&A dans la section segment, Adjusted EBITDA non-GAAP dans MD&A",
       "LLM extrait EBIT d'un segment non-GAAP → incohérence EBITDA"),
    _d("wellsfargo", "Wells Fargo & Co.",   "Banque de Détail",     2026,
       "compte_resultat_10k_WELLS_FARGO_&_COMPANY_MN_2026.pdf",
       "Structure NII + Noninterest Income, aucune ligne 'Revenue' ni 'Net sales'",
       "Regex échoue : vocabulaire banque de détail"),
    _d("goldman",    "Goldman Sachs Group", "Banque d'Investissement", 2026,
       "compte_resultat_10k_GOLDMAN_SACHS_GROUP_INC_2026.pdf",
       "Net revenues segmentés (FICC, Equities, IBD…), structure complexe",
       "Regex lit le premier segment au lieu des Net revenues consolidés"),
]

# ── 49 docs corpus complet (mode --full) ─────────────────────────────────────
DOCS_FULL = [
    # ── Technologie (13) ──────────────────────────────────────────────────────
    _d("apple",      "Apple Inc.",         "Technologie", 2025,
       "compte_resultat_10k_Apple_Inc_2025.pdf",
       "P&L 3 colonnes d'années, Net sales clairement libellé",
       "Colonnes multi-années — lire uniquement la colonne FY la plus récente"),
    _d("alphabet",   "Alphabet Inc.",      "Technologie", 2026,
       "compte_resultat_10k_Alphabet_Inc_2026.pdf",
       "Revenues = Google Services + Cloud + Other Bets",
       "Segments multiples — consolider les revenues totaux"),
    _d("amazon",     "Amazon.com Inc.",    "Technologie", 2026,
       "compte_resultat_10k_AMAZON_COM_INC_2026.pdf",
       "Net sales = Products + Services ; Operating income par segment (AWS vs Retail)",
       "AWS operating income ≠ total group — prendre le consolidé"),
    _d("microsoft",  "Microsoft Corp.",    "Technologie", 2025,
       "compte_resultat_10k_MICROSOFT_CORP_2025.pdf",
       "Fiscal year juin, 3 segments (Productivity, Intelligent Cloud, PCM)",
       "Année fiscale juin — vérifier qu'on lit FY2025 et non FY2024"),
    _d("meta",       "Meta Platforms Inc.","Technologie", 2026,
       "compte_resultat_10k_Meta_Platforms_Inc_2026.pdf",
       "Revenue = Advertising + Other. Marge EBITDA >40%",
       "EBITDA non-GAAP courant — SBC significatif à exclure ou inclure"),
    _d("nvidia",     "NVIDIA Corp.",       "Technologie", 2026,
       "compte_resultat_10k_NVIDIA_CORP_2026.pdf",
       "Croissance explosive, segments Data Center dominant",
       "Revenus en forte variation — vérifier l'année fiscale (jan)"),
    _d("cisco",      "Cisco Systems Inc.", "Technologie", 2025,
       "compte_resultat_10k_CISCO_SYSTEMS_INC_2025.pdf",
       "Produits + Services, fiscal year juillet",
       "Année fiscale juillet — FY2025 = août 2024 à juillet 2025"),
    _d("ibm",        "IBM Corp.",          "Technologie", 2026,
       "compte_resultat_10k_INTERNATIONAL_BUSINESS_MACHINES_COR_2026.pdf",
       "Infrastructure + Software + Consulting ; EBITDA ajusté fréquent",
       "EBITDA ajusté exclut restructuration et acquisition-related charges"),
    _d("oracle",     "Oracle Corp.",       "Technologie", 2026,
       "compte_resultat_10k_ORACLE_CORP_2026.pdf",
       "Cloud + License ; fiscal year mai",
       "Fiscal year mai — FY2026 = juin 2025 à mai 2026"),
    _d("adobe",      "Adobe Inc.",         "Technologie", 2026,
       "compte_resultat_10k_ADOBE_INC_2026.pdf",
       "SaaS pur, marge EBITDA ~45%, D&A = amortissement intangibles",
       "Stock-based compensation significatif dans les charges"),
    _d("qualcomm",   "Qualcomm Inc.",      "Technologie", 2025,
       "compte_resultat_10k_QUALCOMM_INC_DE_2025.pdf",
       "Segments QCT (chips) + QTL (licensing)",
       "Revenues QTL = royalties — pas de COGS standard"),
    _d("netflix",    "Netflix Inc.",       "Technologie", 2026,
       "compte_resultat_10k_NETFLIX_INC_2026.pdf",
       "Content costs lissés (amortization), Operating income en forte croissance",
       "Content amortization = proxy D&A — vérifier le tableau de flux"),
    # ── Banque / Finance (8) ──────────────────────────────────────────────────
    _d("jpmorgan",   "JPMorgan Chase & Co.","Banque Commerciale",   2026,
       "compte_resultat_10k_JPMORGAN_CHASE_&_CO_2026.pdf",
       "Net Interest Income + Noninterest Revenue. Provision for credit losses",
       "Revenue = NII + Noninterest Revenue — pas de 'Net sales'"),
    _d("bofa",       "Bank of America",    "Banque Commerciale",   2026,
       "compte_resultat_10k_BANK_OF_AMERICA_CORP__DE__2026.pdf",
       "Structure similaire JPMorgan, NII sensible aux taux directeurs",
       "NII très variable selon les taux — impact direct sur l'EBITDA proxy"),
    _d("citigroup",  "Citigroup Inc.",     "Banque Commerciale",   2026,
       "compte_resultat_10k_CITIGROUP_INC_2026.pdf",
       "5 segments (Services, Markets, Banking, USPB, International)",
       "Structure très complexe — consolider les revenues groupe"),
    _d("morganstanley","Morgan Stanley",   "Banque d'Investissement",2026,
       "compte_resultat_10k_MORGAN_STANLEY_2026.pdf",
       "Wealth Management dominant, Net revenues = commissions + interest",
       "Gestion de fortune = revenus de commissions, pas de COGS"),
    _d("wellsfargo", "Wells Fargo & Co.",  "Banque de Détail",     2026,
       "compte_resultat_10k_WELLS_FARGO_&_COMPANY_MN_2026.pdf",
       "Structure NII + Noninterest Income, aucune ligne 'Net sales'",
       "Regex échoue : vocabulaire bancaire de détail"),
    _d("goldman",    "Goldman Sachs Group","Banque d'Investissement",2026,
       "compte_resultat_10k_GOLDMAN_SACHS_GROUP_INC_2026.pdf",
       "Net revenues segmentés (FICC, Equities, IBD…)",
       "Consolider les segments — éviter de lire un seul segment FICC"),
    _d("visa",       "Visa Inc.",          "Paiements",            2025,
       "compte_resultat_10k_VISA_INC_2025.pdf",
       "Modèle transaction fees, marges nettes >50%",
       "Pas de COGS (réseau de paiement) — EBITDA = quasi Operating Income"),
    _d("mastercard", "Mastercard Inc.",    "Paiements",            2026,
       "compte_resultat_10k_Mastercard_Inc_2026.pdf",
       "Même modèle Visa — Rebates & Incentives déduits du CA brut",
       "Net revenues = Gross revenues – Rebates — vérifier la définition"),
    # ── Santé / Pharma (10) ───────────────────────────────────────────────────
    _d("jnj",        "Johnson & Johnson",  "Santé",                2026,
       "compte_resultat_10k_JOHNSON_&_JOHNSON_2026.pdf",
       "Pharmaceuticals + MedTech (Consumer spinoff Kenvue 2023)",
       "EBITDA ajusté exclut Talc litigation charges et amortissement Actelion"),
    _d("pfizer",     "Pfizer Inc.",        "Santé / Pharma",       2026,
       "compte_resultat_10k_PFIZER_INC_2026.pdf",
       "CA post-COVID en forte baisse (Comirnaty/Paxlovid), restructuration",
       "CA très variable — éviter de comparer à FY2022 (pic COVID)"),
    _d("abbvie",     "AbbVie Inc.",        "Santé / Pharma",       2026,
       "compte_resultat_10k_AbbVie_Inc_2026.pdf",
       "Humira perte d'exclusivité 2023, amortissement Allergan acquisition",
       "R&D élevée + amortissement intangibles acquisition Allergan (~$6B/an)"),
    _d("merck",      "Merck & Co.",        "Santé / Pharma",       2026,
       "compte_resultat_10k_Merck_&_Co_Inc_2026.pdf",
       "Keytruda ~50% CA, R&D ~25% CA, pipeline oncologie",
       "IPR&D charges variables — EBITDA GAAP vs ajusté très différents"),
    _d("elililly",   "Eli Lilly & Co.",    "Santé / Pharma",       2026,
       "compte_resultat_10k_ELI_LILLY_&_Co_2026.pdf",
       "Forte croissance (Mounjaro/Tirzepatide), marges brutes >75%",
       "Charges de capacité de production (capex converti en COGS)"),
    _d("amgen",      "Amgen Inc.",         "Santé / Pharma",       2026,
       "compte_resultat_10k_AMGEN_INC_2026.pdf",
       "Biologics, acquisition Horizon Therapeutics 2023 (~$28B)",
       "Amortissement intangibles Horizon significatif — EBITDA GAAP réduit"),
    _d("gilead",     "Gilead Sciences",    "Santé / Pharma",       2026,
       "compte_resultat_10k_GILEAD_SCIENCES_INC_2026.pdf",
       "HIV + Hepatitis + Oncology ; royalties impactent le CA",
       "Product royalties déduites ou incluses selon la ligne"),
    _d("unitedhealth","UnitedHealth Group","Santé",                2026,
       "compte_resultat_10k_UNITEDHEALTH_GROUP_INC_2026.pdf",
       "Assurance (UHC) + Services (Optum) — Medical costs = COGS proxy",
       "Operating costs = sinistres payés — pas de COGS au sens industriel"),
    _d("abbott",     "Abbott Laboratories","Santé",                2026,
       "compte_resultat_10k_ABBOTT_LABORATORIES_2026.pdf",
       "Devices + Diagnostics + Nutrition — D&A élevée (dispositifs médicaux)",
       "EBITDA ajusté exclut intégration acquisitions et restructuration"),
    _d("medtronic",  "Medtronic plc",      "Santé",                2026,
       "compte_resultat_10k_Medtronic_plc_2026.pdf",
       "Pure play medical devices, fiscal year avril",
       "Fiscal year avril — FY2026 = mai 2025 à avril 2026"),
    # ── Consommation (3) ─────────────────────────────────────────────────────
    _d("cocacola",   "Coca-Cola Co.",      "Consommation",         2026,
       "compte_resultat_10k_COCA_COLA_CO_2026.pdf",
       "Concentrés + boissons embouteillées ; revenus selon territoire",
       "Deferred revenue et bottlers royalties — CA variable selon périmètre"),
    _d("pepsico",    "PepsiCo Inc.",       "Consommation",         2026,
       "compte_resultat_10k_PEPSICO_INC_2026.pdf",
       "Beverages + Foods (Frito-Lay, Quaker) ; segments multiples",
       "Operating income par division — consolider le groupe"),
    _d("pg",         "Procter & Gamble",   "Consommation",         2025,
       "compte_resultat_10k_PROCTER_&_GAMBLE_Co_2025.pdf",
       "10 catégories produits ; fiscal year juin",
       "Fiscal year juin 2025 — FY2025 = juillet 2024 à juin 2025"),
    # ── Distribution (4) ──────────────────────────────────────────────────────
    _d("walmart",    "Walmart Inc.",       "Grande Distribution",  2026,
       "compte_resultat_10k_Walmart_Inc_2026.pdf",
       "EBIT < D&A dans section segment, Adjusted EBITDA non-GAAP dans MD&A",
       "LLM extrait EBIT d'un segment non-GAAP → incohérence EBITDA"),
    _d("target",     "Target Corp.",       "Distribution",         2026,
       "compte_resultat_10k_TARGET_CORP_2026.pdf",
       "Format hypermarché + e-commerce, marge EBITDA 8-10%",
       "COGS élevé — EBIT très sensible à un seul point de marge"),
    _d("homedepot",  "Home Depot Inc.",    "Distribution",         2026,
       "compte_resultat_10k_HOME_DEPOT_INC_2026.pdf",
       "Home improvement, saisonnier, Capex élevé (digital + supply chain)",
       "Selling, General & Administrative — SG&A à isoler de COGS"),
    _d("lowes",      "Lowe's Companies",   "Distribution",         2026,
       "compte_resultat_10k_LOWES_COMPANIES_INC_2026.pdf",
       "Concurrent direct Home Depot, même structure P&L",
       "Comparer les marges Lowe's vs Home Depot — structure identique"),
    # ── Industrie (5) ────────────────────────────────────────────────────────
    _d("boeing",     "Boeing Co.",         "Industrie",            2026,
       "compte_resultat_10k_BOEING_CO_2026.pdf",
       "Commercial Aircraft + Defense + Services ; pertes possibles (737 MAX)",
       "Operating loss possible — EBITDA peut être négatif"),
    _d("caterpillar","Caterpillar Inc.",   "Industrie",            2026,
       "compte_resultat_10k_CATERPILLAR_INC_2026.pdf",
       "Construction + Mining + Energy & Transportation",
       "Segment reporting — consolider l'Operating income groupe"),
    _d("deere",      "Deere & Co.",        "Industrie",            2025,
       "compte_resultat_10k_DEERE_&_CO_2025.pdf",
       "Agriculture + Construction + Financial Services ; fiscal year oct",
       "Segment Financial Services inclus dans P&L — biais bancaire possible"),
    _d("3m",         "3M Company",         "Industrie",            2026,
       "compte_resultat_10k_3M_CO_2026.pdf",
       "Safety + Transportation + Electronics ; spin-off Healthcare 2024",
       "CA réduit vs historique (Healthcare spinoff) — bien identifier FY2026"),
    # ── Transport (2) ────────────────────────────────────────────────────────
    _d("ups",        "UPS Inc.",           "Transport",            2026,
       "compte_resultat_10k_UNITED_PARCEL_SERVICE_INC_2026.pdf",
       "US Domestic + International + Supply Chain ; volumes post-COVID en baisse",
       "Marges sous pression — EBITDA peut varier fortement d'un an à l'autre"),
    # ── Énergie (1) ──────────────────────────────────────────────────────────
    _d("chevron",    "Chevron Corp.",      "Énergie",              2026,
       "compte_resultat_10k_CHEVRON_CORP_2026.pdf",
       "Upstream + Downstream + Chemicals ; EBITDA sensible au prix du pétrole",
       "DD&A (depletion) très élevée — EBITDA ajusté peut doubler l'EBIT"),
    # ── Télécommunications (2) ───────────────────────────────────────────────
    _d("att",        "AT&T Inc.",          "Télécommunications",   2026,
       "compte_resultat_10k_AT&T_INC_2026.pdf",
       "Mobility + Business Wireline + Consumer Wireline ; D&A très élevée (5G)",
       "D&A réseau 5G ~$20B/an — EBITDA >> EBIT, ratio crucial pour la dette"),
    _d("verizon",    "Verizon Communications","Télécommunications", 2026,
       "compte_resultat_10k_VERIZON_COMMUNICATIONS_INC_2026.pdf",
       "Consumer Group + Business Group ; même secteur AT&T",
       "Comparer D&A et marges EBITDA Verizon vs AT&T"),
    # ── Automobile (1) ───────────────────────────────────────────────────────
    _d("tesla",      "Tesla Inc.",         "Automobile",           2026,
       "compte_resultat_10k_Tesla_Inc_2026.pdf",
       "Automotive + Energy Generation + Services ; R&D ~5% CA",
       "Marges auto en baisse (guerre des prix) — EBITDA variable selon mix"),
]

KEY_FIELDS = [
    "chiffre_affaires",
    "ebit",
    "ebitda",
    "dotations_amortissements",
    "resultat_net",
]

# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 1 : Parseur Regex / Géométrique Classique
# ══════════════════════════════════════════════════════════════════════════════

_PATTERNS_M1 = {
    "chiffre_affaires": [
        r'Total\s+net\s+revenues?\s+([\d,]+)',
        r'Net\s+revenues?\s+([\d,]+)',
        r'Net\s+sales?\s+([\d,]+)',
        r'Total\s+revenues?\s+([\d,]+)',
        r'Revenues?\s+([\d,]+)',
    ],
    "ebit": [
        r'Income\s+from\s+operations\s+([\d,]+)',
        r'Operating\s+income\s+([\d,]+)',
        r'Operating\s+profit\s+([\d,]+)',
        r'Total\s+operating\s+income\s+([\d,]+)',
    ],
    "dotations_amortissements": [
        r'Depreciation\s+and\s+amortization\s+([\d,]+)',
        r'Depreciation,\s*depletion\s+and\s+amortization\s+([\d,]+)',
    ],
    "resultat_net": [
        r'Net\s+income\s+attributable\s+to\s+\S+.*?([\d,]+)',
        r'Net\s+income\s+([\d,]+)',
        r'Net\s+earnings\s+([\d,]+)',
    ],
}

def method1_regex(text: str) -> dict:
    result = {}
    for field, patterns in _PATTERNS_M1.items():
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    v = float(m.group(1).replace(",", ""))
                    if v > 0:
                        result[field] = v
                        break
                except (ValueError, IndexError):
                    pass
    # EBITDA calculé uniquement si on a EBIT + D&A
    ebit = result.get("ebit", 0)
    da   = result.get("dotations_amortissements", 0)
    if ebit > 0 and da > 0:
        result["ebitda"] = ebit + da
    return result


# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 2 : LLM Pure Zéro-Shot (sans couche déterministe)
# ══════════════════════════════════════════════════════════════════════════════

_ZEROSHOT_PROMPT = """Extract these 5 financial metrics from the income statement below.
Return ONLY a JSON object with exactly these keys (null if not found):
  chiffre_affaires, ebit, ebitda, dotations_amortissements, resultat_net

Numbers must match the document exactly. No units, no text, no markdown.

Document:
{text}
"""

def method2_zeroshot(text: str) -> dict:
    try:
        resp = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": _ZEROSHOT_PROMPT.format(text=text[:120_000])}],
            temperature=0,
            max_tokens=256,
        )
        raw  = resp.choices[0].message.content or ""
        data = json.loads(_clean_llm_response(raw))
        return {k: data.get(k) for k in KEY_FIELDS}
    except Exception as e:
        return {k: None for k in KEY_FIELDS}


# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 3 : LLM + Chain-of-Thought Auto-correction
# ══════════════════════════════════════════════════════════════════════════════

_COT_EXTRACT = """Extract these financial metrics from the income statement.
Return ONLY JSON with keys: chiffre_affaires, ebit, ebitda, dotations_amortissements, resultat_net.
Numbers only. Null if missing.

Document:
{text}
"""

_COT_VERIFY = """You extracted:
{json_data}

Check ONE rule only: EBITDA = ebit + dotations_amortissements.
- If this holds (within 5%), return the JSON unchanged.
- If ebitda is null but both ebit and dotations_amortissements are non-null, set ebitda = ebit + dotations_amortissements.
- If ebitda violates this rule, recalculate ebitda = ebit + dotations_amortissements.
- DO NOT change any other value. DO NOT change units. Return all other fields exactly as given.

Return ONLY the corrected JSON. No explanation.
"""

def method3_cot(text: str) -> dict:
    # Étape 1 : extraction initiale
    try:
        resp1 = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": _COT_EXTRACT.format(text=text[:120_000])}],
            temperature=0,
            max_tokens=256,
        )
        raw1 = resp1.choices[0].message.content or ""
        extracted = json.loads(_clean_llm_response(raw1))
    except Exception:
        return {k: None for k in KEY_FIELDS}

    # Étape 2 : vérification CoT (biais de confirmation — le LLM relit ses propres chiffres)
    try:
        resp2 = _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "user", "content": _COT_VERIFY.format(json_data=json.dumps(extracted, indent=2))}
            ],
            temperature=0,
            max_tokens=256,
        )
        raw2 = resp2.choices[0].message.content or ""
        verified = json.loads(_clean_llm_response(raw2))
        # Anti-dégradation : si step 2 retourne moins de champs non-null que step 1, garder step 1
        n1 = sum(1 for k in KEY_FIELDS if extracted.get(k) is not None)
        n2 = sum(1 for k in KEY_FIELDS if verified.get(k) is not None)
        if n2 < n1:
            return {k: extracted.get(k) for k in KEY_FIELDS}
        return {k: verified.get(k) for k in KEY_FIELDS}
    except Exception:
        return {k: extracted.get(k) for k in KEY_FIELDS}


# ══════════════════════════════════════════════════════════════════════════════
# MÉTHODE 4 : Pare-feu Hybride Neuro-Symbolique (système complet)
# ══════════════════════════════════════════════════════════════════════════════

def method4_hybrid(doc_name: str, text: str) -> dict:
    result = _certify_dd(doc_name, "compte_resultat", text)
    fields = result.get("champs_extraits", {})
    return {
        "fields":         {k: fields.get(k) for k in KEY_FIELDS},
        "statut":         result["statut"],
        "score":          result["score_confiance"],
        "anomalies":      result.get("anomalies", []),
        "controles":      result.get("controles", {}),
        "semantic_jsd":   result.get("semantic_analysis", {}).get("jsd_score"),
        "semantic_alert": result.get("semantic_analysis", {}).get("jsd_alert"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CALCUL DES MÉTRIQUES
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(fields: dict) -> dict:
    f = DDTaxonomy._f

    # Rappel : % champs extraits (non-null, non-zéro)
    nb_extracted = sum(1 for k in KEY_FIELDS if f(fields.get(k)) != 0)
    recall = round(nb_extracted / len(KEY_FIELDS) * 100, 1)

    # Cohérence EBITDA = EBIT + D&A (±15 % tolérance DDTaxonomy)
    ebit   = f(fields.get("ebit"))
    da     = f(fields.get("dotations_amortissements"))
    ebitda = f(fields.get("ebitda"))

    coherence = None
    coherence_error_pct = None
    if ebit > 0 and da > 0 and ebitda > 0:
        expected = ebit + da
        err_pct  = abs(ebitda - expected) / expected * 100
        coherence = err_pct <= 15.0
        coherence_error_pct = round(err_pct, 1)

    return {
        "fields_extracted":   nb_extracted,
        "total_fields":       len(KEY_FIELDS),
        "recall_pct":         recall,
        "ebitda_coherent":    coherence,
        "ebitda_error_pct":   coherence_error_pct,
    }


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_benchmark(docs: list, results_path: Path,
                  use_cache: bool = True) -> dict:
    """
    Exécute le benchmark sur `docs`.
    Si `use_cache=True` et que `results_path` existe déjà, recharge les docs déjà traités
    et reprend à partir du suivant (sauvegarde incrémentale).
    """
    # ── Chargement du cache / reprise ────────────────────────────────────────
    completed: dict[str, dict] = {}
    if use_cache and results_path.exists():
        try:
            prior = json.loads(results_path.read_text(encoding="utf-8"))
            for d in prior.get("documents", []):
                completed[d["id"]] = d
            if completed:
                print(f"  Reprise : {len(completed)}/{len(docs)} doc(s) déjà traité(s).")
        except Exception:
            pass

    all_docs = list(completed.values())  # start from cached docs

    for idx, doc in enumerate(docs, 1):
        if doc["id"] in completed:
            continue  # déjà traité

        print(f"\n{'━'*62}")
        print(f"  [{idx}/{len(docs)}] {doc['nom']}  ({doc['secteur']}, {doc['annee']})")
        print(f"  Défi : {doc['defi']}")
        print(f"{'━'*62}")

        # Extraction texte commune à toutes les méthodes
        try:
            raw_text = extract_text_from_file(doc["path"])
            text     = _prepare_text(raw_text)
            print(f"  Texte extrait : {len(text):,} chars")
        except Exception as e:
            print(f"  ERREUR extraction texte : {e}")
            continue

        doc_entry = {
            "id":          doc["id"],
            "nom":         doc["nom"],
            "secteur":     doc["secteur"],
            "annee":       doc["annee"],
            "particularite": doc["particularite"],
            "defi":        doc["defi"],
            "text_chars":  len(text),
            "methods":     {},
        }

        # ── Méthode 1 : Regex ─────────────────────────────────────────────────
        print("\n  [M1] Regex / Géométrique classique …", flush=True)
        t0 = time.time()
        m1_fields  = method1_regex(text)
        m1_metrics = compute_metrics(m1_fields)
        doc_entry["methods"]["method1"] = {
            "label":   "Regex / Géométrique",
            "fields":  m1_fields,
            "metrics": m1_metrics,
            "time_s":  round(time.time() - t0, 3),
            "anomalies_detected": [],
        }
        print(f"     Recall {m1_metrics['recall_pct']}%  "
              f"EBITDA cohérent: {m1_metrics['ebitda_coherent']}")

        # ── Méthode 2 : LLM Zéro-Shot ─────────────────────────────────────────
        print("\n  [M2] LLM Zéro-Shot …", flush=True)
        time.sleep(1.0)
        t0 = time.time()
        m2_fields  = method2_zeroshot(text)
        m2_metrics = compute_metrics(m2_fields)
        doc_entry["methods"]["method2"] = {
            "label":   "LLM Zéro-Shot",
            "fields":  m2_fields,
            "metrics": m2_metrics,
            "time_s":  round(time.time() - t0, 1),
            "anomalies_detected": [],
        }
        print(f"     Recall {m2_metrics['recall_pct']}%  "
              f"EBITDA cohérent: {m2_metrics['ebitda_coherent']}")

        # ── Méthode 3 : LLM + CoT ─────────────────────────────────────────────
        print("\n  [M3] LLM + Chain-of-Thought …", flush=True)
        time.sleep(1.0)
        t0 = time.time()
        m3_fields  = method3_cot(text)
        m3_metrics = compute_metrics(m3_fields)
        doc_entry["methods"]["method3"] = {
            "label":   "LLM + CoT Auto-correction",
            "fields":  m3_fields,
            "metrics": m3_metrics,
            "time_s":  round(time.time() - t0, 1),
            "anomalies_detected": [],
        }
        print(f"     Recall {m3_metrics['recall_pct']}%  "
              f"EBITDA cohérent: {m3_metrics['ebitda_coherent']}")

        # ── Méthode 4 : Hybride Neuro-Symbolique ─────────────────────────────
        print("\n  [M4] Hybride Neuro-Symbolique …", flush=True)
        time.sleep(1.0)
        t0 = time.time()
        m4_result  = method4_hybrid(Path(doc["path"]).name, text)
        m4_metrics = compute_metrics(m4_result["fields"])
        doc_entry["methods"]["method4"] = {
            "label":            "Pare-feu Hybride Neuro-Symbolique",
            "fields":           m4_result["fields"],
            "metrics":          m4_metrics,
            "time_s":           round(time.time() - t0, 1),
            "statut":           m4_result["statut"],
            "score_confiance":  m4_result["score"],
            "anomalies_detected": m4_result["anomalies"],
            "controles":        m4_result["controles"],
            "semantic_jsd":     m4_result["semantic_jsd"],
            "semantic_alert":   m4_result["semantic_alert"],
        }
        print(f"     Recall {m4_metrics['recall_pct']}%  "
              f"EBITDA cohérent: {m4_metrics['ebitda_coherent']}  "
              f"Statut: {m4_result['statut']}  Score: {m4_result['score']}%")

        all_docs.append(doc_entry)

        # ── Sauvegarde incrémentale après chaque doc ──────────────────────────
        OUTPUT_DIR.mkdir(exist_ok=True)
        snapshot = {
            "date":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model":     MODEL_NAME,
            "documents": all_docs,
        }
        results_path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return {
        "date":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model":     MODEL_NAME,
        "documents": all_docs,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # ── Mode M4-retry : relancer uniquement M4 sur les docs ERREUR / manquants ─
    if "--m4-retry" in sys.argv:
        if not RESULTS_FULL_PATH.exists():
            print(f"Fichier résultats introuvable : {RESULTS_FULL_PATH}")
            print("Lancez d'abord : .\\venv\\Scripts\\python.exe benchmark_compare.py --full")
            sys.exit(1)

        data     = json.loads(RESULTS_FULL_PATH.read_text(encoding="utf-8"))
        all_docs = {d["id"]: d for d in data["documents"]}

        erreur_ids   = {d["id"] for d in data["documents"]
                        if d.get("methods", {}).get("method4", {}).get("statut") == "ERREUR"}
        anomalie_ids = {d["id"] for d in data["documents"]
                        if d.get("methods", {}).get("method4", {}).get("statut") == "ANOMALIE"}
        missing_ids  = {doc["id"] for doc in DOCS_FULL if doc["id"] not in all_docs}
        retry_ids    = erreur_ids | anomalie_ids | missing_ids
        docs_to_retry = [doc for doc in DOCS_FULL if doc["id"] in retry_ids]

        print(f"  M4 Retry — {len(docs_to_retry)} doc(s) : "
              f"{len(erreur_ids)} ERREUR + {len(anomalie_ids)} ANOMALIE + {len(missing_ids)} manquants")
        print(f"  Modèle : {MODEL_NAME}\n")

        updated = 0
        for doc in docs_to_retry:
            print(f"  → [{doc['id']}] {doc['nom']}")
            try:
                raw_text = extract_text_from_file(doc["path"])
                text = _prepare_text(raw_text)
                print(f"    Texte : {len(text):,} chars")
            except Exception as e:
                print(f"    SKIP — extraction impossible : {e}")
                continue

            t0 = time.time()
            time.sleep(1.0)
            m4_result  = method4_hybrid(Path(doc["path"]).name, text)
            m4_metrics = compute_metrics(m4_result["fields"])
            elapsed    = round(time.time() - t0, 1)

            new_m4 = {
                "label":              "Pare-feu Hybride Neuro-Symbolique",
                "fields":             m4_result["fields"],
                "metrics":            m4_metrics,
                "time_s":             elapsed,
                "statut":             m4_result["statut"],
                "score_confiance":    m4_result["score"],
                "anomalies_detected": m4_result["anomalies"],
                "controles":          m4_result["controles"],
                "semantic_jsd":       m4_result["semantic_jsd"],
                "semantic_alert":     m4_result["semantic_alert"],
            }

            if doc["id"] in all_docs:
                all_docs[doc["id"]]["methods"]["method4"] = new_m4
            else:
                all_docs[doc["id"]] = {
                    "id": doc["id"], "nom": doc["nom"],
                    "secteur": doc["secteur"], "annee": doc["annee"],
                    "particularite": doc["particularite"], "defi": doc["defi"],
                    "text_chars": len(text),
                    "methods": {"method4": new_m4},
                }

            print(f"    Recall {m4_metrics['recall_pct']}%  "
                  f"Statut: {m4_result['statut']}  Score: {m4_result['score']}%")
            updated += 1

            # Sauvegarde incrémentale
            snapshot = {
                "date":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "model":     MODEL_NAME,
                "documents": list(all_docs.values()),
            }
            RESULTS_FULL_PATH.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        print(f"\n✓ M4 Retry terminé — {updated}/{len(docs_to_retry)} doc(s) mis à jour")
        print(f"  → {RESULTS_FULL_PATH}")
        sys.exit(0)

    use_full  = "--full"     in sys.argv
    use_cache = "--no-cache" not in sys.argv

    active_docs  = DOCS_FULL if use_full else DOCS
    results_path = RESULTS_FULL_PATH if use_full else RESULTS_PATH
    nb_docs      = len(active_docs)

    print("╔══════════════════════════════════════════════════════════════╗")
    if use_full:
        print("║  BENCHMARK — 4 MÉTHODES × 49 P&L 10-K SEC RÉELS            ║")
    else:
        print("║  BENCHMARK — 4 MÉTHODES × 4 P&L 10-K SEC RÉELS             ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Modèle LLM : {MODEL_NAME}")
    print(f"  Corpus     : {nb_docs} documents")
    print(f"  Date       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    if use_cache and results_path.exists() and not use_full:
        # Mode 4-docs + cache complet → lecture directe
        prior_data = json.loads(results_path.read_text(encoding="utf-8"))
        if len(prior_data.get("documents", [])) == nb_docs:
            print(f"\n  Cache trouvé → {results_path}")
            print("  Utilisez --no-cache pour forcer le recalcul.\n")
            data = prior_data
        else:
            data = run_benchmark(active_docs, results_path, use_cache=False)
            print(f"\n✓ Résultats sauvegardés → {results_path}")
    else:
        data = run_benchmark(active_docs, results_path, use_cache=use_cache)
        print(f"\n✓ Résultats sauvegardés → {results_path}")

    # Résumé rapide
    n_cols = 66
    print(f"\n┌{'─'*n_cols}┐")
    print(f"│{'RÉSUMÉ':^{n_cols}}│")
    print(f"├{'─'*20}┬{'─'*10}┬{'─'*10}┬{'─'*10}┬{'─'*10}┤")
    print(f"│{'Document':<20}│{'  M1':>8}  │{'  M2':>8}  │{'  M3':>8}  │{'  M4':>8}  │")
    print(f"├{'─'*20}┼{'─'*10}┼{'─'*10}┼{'─'*10}┼{'─'*10}┤")
    for doc in data["documents"]:
        row = f"│ {doc['nom'][:18]:<18} │"
        for mk in ["method1", "method2", "method3", "method4"]:
            m = doc["methods"].get(mk, {})
            r = m.get("metrics", {}).get("recall_pct", 0)
            row += f" {r:>5.1f}%  │"
        print(row)
    print(f"└{'─'*20}┴{'─'*10}┴{'─'*10}┴{'─'*10}┴{'─'*10}┘")
    print("  (Recall = % champs extraits sur 5 champs obligatoires)")

    # Moyennes par méthode
    print()
    for mk, label in [("method1","M1 Regex"),("method2","M2 ZeroShot"),
                      ("method3","M3 CoT"),("method4","M4 Hybride")]:
        recalls = [d["methods"].get(mk,{}).get("metrics",{}).get("recall_pct",0)
                   for d in data["documents"]]
        coh = [d["methods"].get(mk,{}).get("metrics",{}).get("ebitda_coherent")
               for d in data["documents"] if d["methods"].get(mk,{}).get("metrics",{}).get("ebitda_coherent") is not None]
        avg_r = sum(recalls)/len(recalls) if recalls else 0
        coh_r = sum(1 for c in coh if c)/len(coh)*100 if coh else None
        coh_s = f"{coh_r:.0f}%" if coh_r is not None else "—"
        print(f"  {label:<12} avg recall={avg_r:>5.1f}%  EBITDA cohérent={coh_s}")
