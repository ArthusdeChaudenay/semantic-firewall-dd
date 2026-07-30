"""
build_en_corpus.py — Construit le corpus de référence anglais pour le semantic monitor.

Lit les textes Item 8 déjà parsés dans samples/real_world/parsed/,
sélectionne 16 sociétés couvrant 6 secteurs, et les écrit dans
samples/references/en/ avec les préfixes reconnus par detect_doc_type().

Lancer une seule fois (ou après ajout de nouveaux 10-K parsés) :
    .\\venv\\Scripts\\python.exe build_en_corpus.py
"""

import re
from pathlib import Path

PARSED_DIR = Path("samples/real_world/parsed")
EN_REF_DIR = Path("samples/references/en")
EN_REF_DIR.mkdir(parents=True, exist_ok=True)

# Sociétés sélectionnées : 16 entreprises, 6 secteurs différents
# → diversité sectorielle = meilleure calibration du centroïde anglais
SELECTION = {
    # Technologie
    "Apple_Inc_2025":              "tech",
    "MICROSOFT_CORP_2025":         "tech",
    "Alphabet_Inc_2026":           "tech",
    "NVIDIA_CORP_2026":            "tech",
    "Meta_Platforms_Inc_2026":     "tech",
    # Industrie
    "3M_CO_2026":                  "industrial",
    "BOEING_CO_2026":              "industrial",
    "CATERPILLAR_INC_2026":        "industrial",
    # Grande consommation
    "AMAZON_COM_INC_2026":         "consumer",
    "Walmart_Inc_2026":            "consumer",
    "COCA_COLA_CO_2026":           "consumer",
    # Pharma
    "JOHNSON_&_JOHNSON_2026":      "pharma",
    "PFIZER_INC_2026":             "pharma",
    "AbbVie_Inc_2026":             "pharma",
    # Finance
    "JPMORGAN_CHASE_&_CO_2026":    "finance",
    "GOLDMAN_SACHS_GROUP_INC_2026":"finance",
}

# Longueur max extraite par fichier (12 000 chars = bon équilibre richesse/taille)
CHUNK_SIZE = 12_000

# Marqueurs du début des états financiers (Item 8) dans les 10-K
_FIN_MARKERS = re.compile(
    r"consolidated\s+statements?\s+of\s+(operations|income|earnings|"
    r"comprehensive\s+income|financial\s+condition)|"
    r"financial\s+statements\s+and\s+supplementary|"
    r"notes\s+to\s+(the\s+)?consolidated\s+financial",
    re.I
)


def _extract_financial_chunk(text: str, chunk_size: int = CHUNK_SIZE) -> str:
    """
    Tente de trouver le début des états financiers dans le texte.
    Fallback : milieu du document (évite les longs préambules business).
    """
    m = _FIN_MARKERS.search(text)
    if m:
        start = max(0, m.start() - 200)
    else:
        # Fallback : prendre à partir du tiers du document
        start = len(text) // 3
    return text[start: start + chunk_size]


def build_compte_resultat_refs() -> int:
    created = 0
    for company_key, sector in SELECTION.items():
        src = PARSED_DIR / f"10k_{company_key}_item8.txt"
        if not src.exists():
            print(f"  [ABSENT] {src.name}")
            continue

        text = src.read_text(encoding="utf-8", errors="replace")
        chunk = _extract_financial_chunk(text)

        # Nom de sortie : préfixe compte_resultat_ + suffixe court
        short = company_key.lower().replace("&", "and").replace(",", "").replace(" ", "_")
        out = EN_REF_DIR / f"compte_resultat_en_{short}.txt"
        out.write_text(chunk, encoding="utf-8")
        print(f"  OK  {out.name:<55} ({len(chunk):,} chars)  [{sector}]")
        created += 1

    return created


def build_bilan_refs() -> int:
    """
    Crée 3 références bilingues anglaises pour le type bilan.
    Contenu : vocabulaire balance sheet typique d'un 10-K.
    Basé sur les mêmes fichiers item8 qui contiennent aussi le bilan consolidé.
    """
    bilan_companies = [
        ("Apple_Inc_2025",             "tech"),
        ("MICROSOFT_CORP_2025",        "tech"),
        ("AMAZON_COM_INC_2026",        "consumer"),
    ]
    created = 0
    for company_key, sector in bilan_companies:
        src = PARSED_DIR / f"10k_{company_key}_item8.txt"
        if not src.exists():
            continue

        text = src.read_text(encoding="utf-8", errors="replace")
        # Chercher la section balance sheet dans le texte
        m = re.search(
            r"consolidated\s+balance\s+sheet|"
            r"total\s+assets|stockholders.{0,10}equity",
            text, re.I
        )
        start = max(0, m.start() - 100) if m else len(text) // 2
        chunk = text[start: start + 8_000]

        short = company_key.lower().replace("&", "and").replace(",", "").replace(" ", "_")
        out = EN_REF_DIR / f"bilan_en_{short}.txt"
        out.write_text(chunk, encoding="utf-8")
        print(f"  OK  {out.name:<55} ({len(chunk):,} chars)  [{sector}]")
        created += 1

    return created


def build_facture_refs() -> int:
    """Crée un fichier de référence facture anglaise (invoice)."""
    invoice_text = """INVOICE
Invoice Number: INV-2025-00847
Invoice Date: March 15, 2025
Due Date: April 14, 2025
Payment Terms: Net 30

Bill To:
TechVenture Capital LLC
123 Sand Hill Road, Suite 400
Menlo Park, CA 94025
United States

From:
Advisory Services Corp
456 Financial District Blvd
New York, NY 10004
United States
Tax ID: 12-3456789

Services Rendered:
Description                                    Qty    Unit Price      Amount
Strategic Advisory Services - Q1 2025          1    $45,000.00   $45,000.00
Market Research Report - Software Sector       1    $12,500.00   $12,500.00
Due Diligence Support - Target Company         1    $28,000.00   $28,000.00
Legal Compliance Review                        1     $8,500.00    $8,500.00

                                            Subtotal:            $94,000.00
                                            Tax (8.875%):         $8,342.50
                                            TOTAL DUE:          $102,342.50

Payment Instructions:
Bank: First National Bank
Account Name: Advisory Services Corp
Account Number: 123456789
Routing Number: 021000021
Reference: INV-2025-00847

Please make payment by April 14, 2025. Late payments subject to 1.5% monthly interest.
Questions? Contact billing@advisoryservices.com or +1 (212) 555-0100.

Thank you for your business.
"""
    out = EN_REF_DIR / "facture_en_advisory_services_2025.txt"
    out.write_text(invoice_text, encoding="utf-8")
    print(f"  OK  {out.name:<55} ({len(invoice_text):,} chars)  [services]")
    return 1


def build_captable_refs() -> int:
    """Crée un fichier de référence cap table anglaise."""
    captable_text = """CAPITALIZATION TABLE — SERIES B PREFERRED FINANCING
Company: NovaTech AI Inc.
Date: January 10, 2025
Pre-Money Valuation: $85,000,000
Round Size: $20,000,000
Post-Money Valuation: $105,000,000

SHAREHOLDER BREAKDOWN
                                Shares          % Pre    % Post
Common Stock
  Founders (3 persons)       5,000,000         55.0%     44.2%
  Employee Stock Pool (ESOP) 1,100,000         12.1%      9.7%
  Early Employees              400,000          4.4%      3.5%
  Total Common              6,500,000         71.4%     57.4%

Preferred Stock
  Seed Investors (2022)        800,000          8.8%      7.1%
  Series A Investors (2023)  1,300,000         14.3%     11.5%
  Series B Investors (2025)  2,500,000            —      22.1%
  Total Preferred            4,600,000         23.1%     40.7%

Warrants & Options (unissued)
  Outstanding warrants         250,000          2.7%      2.2%
  Options reserved             750,000          8.2%      6.6%
  Total dilutive             1,000,000         11.0%      8.8%

TOTAL FULLY DILUTED         11,300,000        100.0%    106.2%

Price per share (Series B):   $8.00
Liquidation preference: 1x non-participating
Anti-dilution: broad-based weighted average
Ownership threshold for pro-rata rights: 5%

Lead investor: Sequoia Capital — $12,000,000
Co-investors: a16z — $5,000,000 ; Tiger Global — $3,000,000
"""
    out = EN_REF_DIR / "captable_en_novatech_series_b_2025.txt"
    out.write_text(captable_text, encoding="utf-8")
    print(f"  OK  {out.name:<55} ({len(captable_text):,} chars)  [startup]")
    return 1


def main():
    print("=== build_en_corpus — Corpus de référence anglais ===\n")
    print(f"  Sortie : {EN_REF_DIR.resolve()}\n")

    total = 0

    print("── Compte de résultat / Income statements ──────────────────")
    total += build_compte_resultat_refs()

    print("\n── Bilan / Balance sheets ──────────────────────────────────")
    total += build_bilan_refs()

    print("\n── Factures / Invoices ─────────────────────────────────────")
    total += build_facture_refs()

    print("\n── Cap tables ──────────────────────────────────────────────")
    total += build_captable_refs()

    print(f"\n{'='*60}")
    print(f"  {total} fichiers créés dans {EN_REF_DIR}")
    print(f"  Relancez l'API pour recharger le semantic monitor.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
