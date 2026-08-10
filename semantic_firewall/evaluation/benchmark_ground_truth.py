"""
benchmark_ground_truth.py — D2 fix: REAL external ground truth for the benchmark.

The audit's D2: the benchmark had no ground truth at all — "recall" was the share of
non-null fields (a coverage rate), and EBITDA "coherence" was tautological. This
module builds actual ground truth for the benchmark corpus from SEC XBRL
(companyfacts), so extraction can be scored on CORRECTNESS, not coverage.

Join key (audit spec): CIK + fiscal period + form (10-K) + USD unit. The target
fiscal year per document is chosen deterministically as the annual period nearest
to the document's labelled year — independent of any model output, so there is no
optimistic bias in the choice.

Run (needs a descriptive SEC User-Agent):
    export SEC_USER_AGENT="Your Name (you@example.com)"
    python -m semantic_firewall.evaluation.benchmark_ground_truth            # 4 docs
    python -m semantic_firewall.evaluation.benchmark_ground_truth --full     # 49 docs

Writes output/benchmark_xbrl_ground_truth.json (committed, checksummable).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from semantic_firewall.evaluation.xbrl_ground_truth import (
    build_fields_from_facts,
    fetch_company_facts,
    nearest_fiscal_year,
    resolve_cik,
)

# Committed reproducibility artifact (output/ is git-ignored; data/*.json is kept).
OUT_PATH = Path("data/benchmark_xbrl_ground_truth.json")

# Benchmark document id → SEC ticker. Curated (all large caps) so CIK resolution is
# reliable; tickers are resolved to CIKs via SEC's company_tickers.json.
ID_TO_TICKER: dict[str, str] = {
    "apple": "AAPL", "alphabet": "GOOGL", "amazon": "AMZN", "microsoft": "MSFT",
    "meta": "META", "nvidia": "NVDA", "cisco": "CSCO", "ibm": "IBM",
    "oracle": "ORCL", "adobe": "ADBE", "qualcomm": "QCOM", "netflix": "NFLX",
    "jpmorgan": "JPM", "bofa": "BAC", "citigroup": "C", "morganstanley": "MS",
    "wellsfargo": "WFC", "goldman": "GS", "visa": "V", "mastercard": "MA",
    "jnj": "JNJ", "pfizer": "PFE", "abbvie": "ABBV", "merck": "MRK",
    "elililly": "LLY", "amgen": "AMGN", "gilead": "GILD", "unitedhealth": "UNH",
    "abbott": "ABT", "medtronic": "MDT", "cocacola": "KO", "pepsico": "PEP",
    "pg": "PG", "walmart": "WMT", "target": "TGT", "homedepot": "HD",
    "lowes": "LOW", "boeing": "BA", "caterpillar": "CAT", "deere": "DE",
    "3m": "MMM", "ups": "UPS", "chevron": "CVX", "att": "T",
    "verizon": "VZ", "tesla": "TSLA",
}


def build(docs: list[dict], out_path: Path = OUT_PATH) -> dict:
    """Fetch XBRL ground truth for each benchmark doc. Logs every gap (no silent drop)."""
    out: dict[str, dict] = {}
    gaps: list[str] = []
    for doc in docs:
        did, ticker = doc["id"], ID_TO_TICKER.get(doc["id"])
        if not ticker:
            gaps.append(f"{did}: no ticker mapping"); continue
        cik = resolve_cik(ticker)
        if not cik:
            gaps.append(f"{did} ({ticker}): CIK not found"); continue
        try:
            facts = fetch_company_facts(cik)
        except Exception as e:
            gaps.append(f"{did} ({ticker}): fetch failed — {e}"); continue
        fy = nearest_fiscal_year(facts, int(doc["annee"]))
        if fy is None:
            gaps.append(f"{did} ({ticker}): no annual 10-K facts"); continue
        fields = build_fields_from_facts(facts, fy)
        if not fields:
            gaps.append(f"{did} ({ticker}) FY{fy}: no concepts matched"); continue
        labelled = int(doc["annee"])
        out[did] = {
            "id": did, "nom": doc["nom"], "ticker": ticker, "cik": cik,
            "labelled_year": labelled, "chosen_fy": fy,
            # D14: nearest_fiscal_year falls back to the closest available period, so
            # a document labelled FY2026 can silently resolve to FY2025 facts. A
            # one-year offset on revenue exceeds every tolerance and is
            # indistinguishable from an extraction error, so flag it explicitly.
            "year_mismatch": fy != labelled,
            "year_offset": fy - labelled,
            "entity": facts.get("entityName", ""),
            "matched_on": "CIK + fy + fp=FY + form=10-K + USD unit",
            "fields": {k: {"val": v["val"], "concept": v["concept"],
                           "end": v.get("end"), "accn": v.get("accn"),
                           "derived": bool(v.get("derived", False))}
                       for k, v in fields.items()},
        }
        flag = "" if fy == labelled else f"  ⚠ labelled {labelled}"
        print(f"  ✓ {did:<14} {ticker:<6} CIK={cik:<8} FY{fy}  "
              f"{len(fields)} fields{flag}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, indent=2, ensure_ascii=False)
    out_path.write_text(payload, encoding="utf-8")
    checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    print(f"\n  wrote {len(out)} ground-truth records -> {out_path}  (sha256:{checksum})")
    n_mismatch = sum(1 for r in out.values() if r["year_mismatch"])
    if n_mismatch:
        print(f"  ⚠ D14: {n_mismatch}/{len(out)} record(s) resolved to a fiscal year "
              f"different from the document label — excluded by load() unless "
              f"require_year_match=False.")
    if gaps:
        print(f"  {len(gaps)} gap(s) (not silently dropped):")
        for g in gaps:
            print(f"    - {g}")
    return out


def load(path: Path = OUT_PATH, require_year_match: bool = True) -> tuple[dict, dict]:
    """Load the ground-truth manifest. Returns ``(records, report)``.

    D14: records whose ``chosen_fy`` differs from the document's labelled year are
    excluded by default — the facts belong to a different filing, so scoring against
    them would charge an extraction with someone else's numbers. ``report`` always
    states how many were dropped, so the exclusion is never silent.
    """
    if not path.exists():
        return {}, {"loaded": 0, "excluded_year_mismatch": 0, "path": str(path),
                     "present": False}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not require_year_match:
        return raw, {"loaded": len(raw), "excluded_year_mismatch": 0,
                     "path": str(path), "present": True}
    kept = {k: v for k, v in raw.items() if not v.get("year_mismatch")}
    dropped = sorted(set(raw) - set(kept))
    return kept, {"loaded": len(kept), "excluded_year_mismatch": len(dropped),
                  "excluded_ids": dropped, "path": str(path), "present": True}


def coverage_report(records: dict) -> dict:
    """D15: how many documents actually have ground truth for each field.

    XBRL only contains what the filer tagged. Banks do not tag ``OperatingIncomeLoss``
    and many filers put D&A only in the cash-flow statement, so the effective n
    differs per field — often sharply. Publishing a single corpus size would
    overstate the evidence behind the EBITDA identity, which is the flagship result.

    ``derived`` counts fields that are not tagged but computed from tagged ones
    (EBITDA = EBIT + D&A). Those are flagged because a detector built on the same
    identity is partly coupled to such a label.
    """
    per_field: dict[str, dict] = {}
    for rec in records.values():
        for field, meta in rec.get("fields", {}).items():
            e = per_field.setdefault(field, {"n": 0, "n_derived": 0})
            e["n"] += 1
            e["n_derived"] += int(bool(meta.get("derived")))
    total = len(records)
    for field, e in per_field.items():
        e["pct_of_corpus"] = round(100 * e["n"] / total, 1) if total else 0.0
    return {"n_documents": total, "per_field": dict(sorted(per_field.items()))}


def _main() -> None:
    from semantic_firewall.evaluation.benchmark_compare import DOCS, DOCS_FULL
    ap = argparse.ArgumentParser(description="D2 — build XBRL ground truth for the benchmark.")
    ap.add_argument("--full", action="store_true", help="49-doc corpus (default: 4 docs)")
    args = ap.parse_args()
    build(DOCS_FULL if args.full else DOCS)


if __name__ == "__main__":
    _main()
