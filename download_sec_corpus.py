"""
download_sec_corpus.py — Téléchargeur de 10-K SEC via l'API publique EDGAR.

Flux :
  1. Récupère le mapping ticker→CIK depuis company_tickers.json
  2. Pour chaque ticker : trouve le dernier 10-K via submissions API
  3. Télécharge le document primaire directement (pas besoin de l'index)

Usage :
    .\\venv\\Scripts\\python.exe download_sec_corpus.py [--limit N]
"""

import json
import time
import argparse
from pathlib import Path
import urllib.request
import urllib.error

# ── Constantes EDGAR ──────────────────────────────────────────────────────────

UA         = "AuditWen-Research valentin.noel@devoteam.com"
RATE_SLEEP = 0.2   # ~5 req/s, sous le seuil EDGAR de 10 req/s

OUTPUT_DIR = Path("samples/real_world/sec_10k")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ~50 grandes capitalisations US (mix de secteurs)
TARGET_TICKERS = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META",
    "TSLA", "NVDA", "JPM",  "JNJ",  "XOM",
    "WMT",  "UNH",  "CVX",  "BAC",  "PG",
    "HD",   "ABBV", "LLY",  "PFE",  "MRK",
    "KO",   "PEP",  "CSCO", "INTC", "QCOM",
    "IBM",  "ORCL", "ADBE", "NFLX", "V",
    "MA",   "GS",   "MS",   "WFC",  "C",
    "TGT",  "LOW",  "T",    "VZ",   "AMGN",
    "GILD", "MDT",  "ABT",  "MMM",  "HON",
    "UPS",  "FDX",  "CAT",  "DE",   "BA",
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read() if binary else r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}", end="")
        return None
    except Exception as e:
        print(f"ERR:{e}", end="")
        return None


def _get_json(url: str) -> dict | None:
    raw = _get(url)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ── Logique EDGAR ──────────────────────────────────────────────────────────────

def get_cik_map() -> dict[str, str]:
    """Mapping ticker → CIK depuis company_tickers.json."""
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    if not data:
        return {}
    return {v["ticker"]: str(v["cik_str"]) for v in data.values()}


def get_latest_10k(cik: str) -> dict | None:
    """Retourne les métadonnées du dernier 10-K (accession, primary_doc, date)."""
    url  = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    data = _get_json(url)
    if not data:
        return None
    rec = data.get("filings", {}).get("recent", {})
    for i, form in enumerate(rec.get("form", [])):
        if form == "10-K":
            return {
                "cik":         cik,
                "accession":   rec["accessionNumber"][i],
                "primary_doc": rec["primaryDocument"][i],
                "filing_date": rec.get("filingDate", [""])[i],
                "company":     data.get("name", ""),
            }
    return None


def download_filing(info: dict) -> Path | None:
    """Télécharge le document primaire du dépôt directement depuis les Archives EDGAR."""
    cik         = info["cik"]
    accession   = info["accession"]
    primary_doc = info["primary_doc"]
    company     = info["company"]
    filing_date = info["filing_date"]

    acc_clean = accession.replace("-", "")
    url       = (f"https://www.sec.gov/Archives/edgar/data/"
                 f"{cik}/{acc_clean}/{primary_doc}")

    ext  = Path(primary_doc).suffix.lower() or ".htm"
    year = filing_date[:4] if filing_date else "????"
    safe = (company.replace(" ", "_").replace("/", "_")
                   .replace(",", "").replace(".", "")[:35])
    out_path = OUTPUT_DIR / f"10k_{safe}_{year}{ext}"

    if out_path.exists():
        print("déjà téléchargé", end="")
        return out_path

    content = _get(url, binary=True)
    if not content:
        return None

    out_path.write_bytes(content)
    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main(limit: int = 50):
    print(f"=== EDGAR 10-K Downloader  —  objectif {limit} fichiers ===\n")

    print("  Chargement company_tickers.json...", end=" ", flush=True)
    cik_map = get_cik_map()
    print(f"{len(cik_map)} entreprises référencées.\n")
    time.sleep(RATE_SLEEP)

    downloaded = errors = skipped = 0

    for ticker in TARGET_TICKERS:
        if downloaded >= limit:
            break

        cik = cik_map.get(ticker)
        if not cik:
            print(f"  [{ticker:5s}] CIK introuvable, ignoré.")
            skipped += 1
            continue

        print(f"  [{ticker:5s}] CIK={cik:<12} ", end="", flush=True)
        time.sleep(RATE_SLEEP)

        info = get_latest_10k(cik)
        if not info:
            print("aucun 10-K.")
            errors += 1
            continue
        time.sleep(RATE_SLEEP)

        path = download_filing(info)
        if not path:
            print("  — aucun fichier téléchargeable.")
            errors += 1
            continue

        size_mb = path.stat().st_size / 1_048_576
        fmt     = "PDF" if path.suffix == ".pdf" else "HTM"
        print(f"  {fmt}  {path.name:<50}  {size_mb:5.1f} Mo  [{info['filing_date']}]")
        time.sleep(RATE_SLEEP)
        downloaded += 1

    print(f"\n{'='*60}")
    print(f"  Téléchargés : {downloaded}  |  Erreurs : {errors}  |  Ignorés : {skipped}")
    print(f"  Dossier     : {OUTPUT_DIR.resolve()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Téléchargeur EDGAR 10-K")
    parser.add_argument("--limit", type=int, default=50,
                        help="Nombre max de fichiers (défaut: 50)")
    args = parser.parse_args()
    main(args.limit)
