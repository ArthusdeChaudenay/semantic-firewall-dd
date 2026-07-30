"""
xbrl_ground_truth.py — Experiment E0: machine-readable ground truth from SEC XBRL.

This is "the unblock" (audit §6.1): the SEC ``companyfacts`` API and the Financial
Statement Data Sets expose the exact tagged value for every fact in every filing,
for free, at the scale of hundreds-to-thousands of filings. That turns every
previously-fake metric (recall = non-null rate; EBITDA coherence = tautology;
anomaly labels read from filenames) into a real one: correctness against a known
answer, per field, per sector, per method.

Nothing else in the experiment plan matters until this exists.

What this module provides (runnable; requires network + a proper SEC User-Agent):
  * ``fetch_company_facts(cik)``      — download & cache the companyfacts JSON.
  * ``ground_truth_for(cik, fy, fp)`` — the tagged values for one fiscal period,
                                        mapped onto our extraction field names.
  * ``build_manifest(pairs, out)``    — write a frozen, checksummed GT manifest
                                        (CIK + accession + fact ids) for reproducibility.
  * ``score_extraction(extracted, gt)`` — exact / tolerant correctness per field.

Run:
    python -m semantic_firewall.evaluation.xbrl_ground_truth --cik 320193 --fy 2023
    python -m semantic_firewall.evaluation.xbrl_ground_truth --manifest data/cik_fy.csv \\
           --out data/ground_truth.jsonl

SEC requires a descriptive User-Agent (name + email). Set SEC_USER_AGENT, e.g.
    export SEC_USER_AGENT="Semantic Firewall research (you@example.com)"
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

CACHE_DIR = Path("data/xbrl_cache")
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Our extraction field  →  ordered list of candidate us-gaap concepts.
# First concept that has a value for the period wins; the choice is recorded so
# the ground truth is auditable (which tag produced which number).
US_GAAP_CONCEPTS: dict[str, list[str]] = {
    "chiffre_affaires": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "ebit": [
        "OperatingIncomeLoss",
    ],
    "dotations_amortissements": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "resultat_net": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "actif_total": [
        "Assets",
    ],
    "passif_total": [
        "LiabilitiesAndStockholdersEquity",
    ],
    "capitaux_propres": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
}

# EBITDA is intentionally NOT tagged in XBRL (it is a non-GAAP measure). The
# ground-truth EBITDA is DERIVED as ebit + d&a from the tagged values — which is
# also exactly why the arithmetic-identity detector is testable against it.


def _headers() -> dict:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError(
            "Set SEC_USER_AGENT='Your Name (you@example.com)' — the SEC blocks "
            "requests without a descriptive User-Agent."
        )
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


def fetch_company_facts(cik: int, ttl_days: int = 30) -> dict:
    """Download companyfacts for a CIK, cached on disk. Requires ``requests``."""
    import requests

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"CIK{cik:010d}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < ttl_days * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    resp = requests.get(COMPANYFACTS_URL.format(cik=cik), headers=_headers(), timeout=30)
    resp.raise_for_status()
    time.sleep(0.15)  # be polite: SEC fair-access ≈ 10 req/s
    data = resp.json()
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def load_ticker_cik_map(ttl_days: int = 30) -> dict:
    """Return {TICKER: cik} from SEC's company_tickers.json (cached)."""
    import requests

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "company_tickers.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < ttl_days * 86400:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        resp = requests.get(TICKERS_URL, headers=_headers(), timeout=30)
        resp.raise_for_status()
        time.sleep(0.15)
        raw = resp.json()
        cache.write_text(json.dumps(raw), encoding="utf-8")
    return {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}


def resolve_cik(ticker: str) -> int | None:
    return load_ticker_cik_map().get(ticker.upper())


# NOTE on the XBRL `fy` trap: a companyfacts entry's `fy`/`fp` fields mark the
# FILING the fact was disclosed in, and a 10-K discloses the current year PLUS two
# comparative years. Filtering on `fy` therefore returns comparative-column values
# (wrong year). We instead key on the PERIOD-END DATE, which unambiguously identifies
# the fiscal year, and require ~annual duration for income-statement concepts.

def _iter_usd_entries(facts: dict, concept: str, form: str = "10-K"):
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return
    for unit, entries in node.get("units", {}).items():
        if not unit.startswith("USD"):
            continue
        for e in entries:
            if str(e.get("form", "")).startswith(form):
                yield unit, e


def _end_year(e: dict) -> int | None:
    end = e.get("end")
    return int(end[:4]) if end else None


def _is_annual(e: dict) -> bool:
    """True for balance-sheet instants (no start) or ~annual duration facts."""
    from datetime import date
    start, end = e.get("start"), e.get("end")
    if not start or not end:
        return True
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return False
    return 330 <= days <= 400


def _annual_fiscal_years(facts: dict, form: str = "10-K") -> list[int]:
    """All fiscal years (by period-end year) with an annual fact, across concepts."""
    years: set[int] = set()
    probes = [c for lst in US_GAAP_CONCEPTS.values() for c in lst]
    for concept in probes:
        for _unit, e in _iter_usd_entries(facts, concept, form):
            if _is_annual(e):
                y = _end_year(e)
                if y:
                    years.add(y)
    return sorted(years)


def nearest_fiscal_year(facts: dict, target_year: int, form: str = "10-K") -> int | None:
    """Available fiscal year (by period-end) nearest to ``target_year``.

    Deterministic and independent of any model output (avoids optimistic bias):
    ties break toward the more recent year.
    """
    years = _annual_fiscal_years(facts, form)
    if not years:
        return None
    return min(years, key=lambda y: (abs(y - target_year), -y))


def _pick_fact(facts: dict, concept: str, fy: int, form: str = "10-K") -> dict | None:
    """The annual value whose PERIOD END falls in fiscal year ``fy``.

    Among candidates, prefer the most recently *filed* (captures restatements),
    then the latest period end.
    """
    best = None  # (sort_key, unit, entry)
    for unit, e in _iter_usd_entries(facts, concept, form):
        if _end_year(e) != fy or not _is_annual(e):
            continue
        key = (e.get("filed", ""), e.get("end", ""))
        if best is None or key > best[0]:
            best = (key, unit, e)
    if best is None:
        return None
    _key, unit, e = best
    return {"concept": concept, "unit": unit, "val": float(e["val"]),
            "end": e.get("end"), "accn": e.get("accn"),
            "frame": e.get("frame"), "fy_tag": e.get("fy")}


def build_fields_from_facts(facts: dict, fy: int, fp: str = "FY", form: str = "10-K") -> dict:
    """Extract {field: {val, concept, ...}} for one fiscal period from fetched facts.

    EBITDA is DERIVED (ebit + d&a) — it is intentionally not tagged in XBRL.
    """
    fields: dict = {}
    for field, concepts in US_GAAP_CONCEPTS.items():
        for concept in concepts:
            hit = _pick_fact(facts, concept, fy, form)
            if hit:
                fields[field] = hit
                break
    if "ebit" in fields and "dotations_amortissements" in fields:
        fields["ebitda"] = {
            "concept": "DERIVED(OperatingIncomeLoss + DepreciationDepletionAndAmortization)",
            "unit": fields["ebit"]["unit"],
            "val": fields["ebit"]["val"] + fields["dotations_amortissements"]["val"],
            "derived": True,
        }
    return fields


def ground_truth_for(cik: int, fy: int, fp: str = "FY", form: str = "10-K") -> dict:
    """Return {field: {val, concept, accn, ...}} for one company-period.

    ``matched_on`` documents the join key (CIK + fiscal period + form + unit scale),
    as the audit specifies. EBITDA is derived from ebit + d&a when both are present.
    """
    facts = fetch_company_facts(cik)
    out: dict = {"cik": cik, "fy": fy, "fp": fp, "form": form,
                 "entity": facts.get("entityName", ""),
                 "fields": build_fields_from_facts(facts, fy, fp, form),
                 "matched_on": "CIK + fy + fp + form + USD unit"}
    return out


def build_manifest(pairs: list[tuple[int, int]], out_path: Path) -> int:
    """Build a frozen JSONL ground-truth manifest for (cik, fy) pairs.

    Each line records CIK, accession numbers and the exact concept per field, so a
    reviewer can re-derive every number. Returns the count written.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for cik, fy in pairs:
            try:
                gt = ground_truth_for(cik, fy)
            except Exception as e:  # keep going; log the gap (no silent drop)
                print(f"  [skip] CIK={cik} FY={fy}: {e}")
                continue
            if gt["fields"]:
                fh.write(json.dumps(gt, ensure_ascii=False) + "\n")
                n += 1
    print(f"  wrote {n} ground-truth records -> {out_path}")
    return n


def score_extraction(extracted: dict, gt_fields: dict, rel_tol: float = 0.01) -> dict:
    """Correctness of an extraction against XBRL ground truth, per field.

    Returns {field: {"extracted", "truth", "exact", "within_tol"}}. This is the
    honest redefinition of "recall" the audit asks for: proportion of CORRECT
    fields (with an explicit tolerance), not proportion of non-null fields.
    """
    from semantic_firewall.validation.dd_base import DDTaxonomy

    res = {}
    for field, meta in gt_fields.items():
        truth = float(meta["val"])
        ev = DDTaxonomy._f(extracted.get(field))
        exact = abs(ev - truth) < 1.0
        tol = max(abs(truth) * rel_tol, 1.0)
        res[field] = {"extracted": ev, "truth": truth,
                      "exact": exact, "within_tol": abs(ev - truth) <= tol}
    return res


def _main() -> None:
    ap = argparse.ArgumentParser(description="E0 — build XBRL ground truth from SEC.")
    ap.add_argument("--cik", type=int, help="single CIK (e.g. 320193 for Apple)")
    ap.add_argument("--fy", type=int, help="fiscal year, e.g. 2023")
    ap.add_argument("--fp", default="FY")
    ap.add_argument("--manifest", help="CSV of 'cik,fy' rows to build a GT manifest")
    ap.add_argument("--out", default="data/ground_truth.jsonl")
    args = ap.parse_args()

    if args.manifest:
        pairs = []
        for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.lower().startswith("cik"):
                continue
            c, y = line.split(",")[:2]
            pairs.append((int(c), int(y)))
        build_manifest(pairs, Path(args.out))
    elif args.cik and args.fy:
        gt = ground_truth_for(args.cik, args.fy, args.fp)
        print(json.dumps(gt, indent=2, ensure_ascii=False))
    else:
        ap.error("provide --cik/--fy for one company, or --manifest for a batch")


if __name__ == "__main__":
    _main()
