"""corpus_reconcile.py -- Reviewer item 6: make the corpus arithmetic close.

The paper says "62 large-cap tickers across 12 sectors", two filings each, minus 13
scale exclusions. That is 62 x 2 - 13 = 111, but the corpus has 104. Seven documents
are unaccounted for, and Table 2 lists 11 sectors, not 12. Both must reconcile
exactly, with a per-document reason for every drop.

Replays the corpus builder's decision path against the on-disk caches -- no network,
no re-download -- and prints the arithmetic.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts.build_benchmark_corpus import TICKERS, _fy_from_period_end, _recent_10ks
from semantic_firewall.evaluation.xbrl_ground_truth import (
    CACHE_DIR,
    build_fields_from_facts,
)
from semantic_firewall.extraction.scale import infer_document_scale

CORPUS_DIR = Path("data/corpus")
YEARS = 2


def cached(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> None:
    tmap_p = CACHE_DIR / "company_tickers.json"
    raw = json.loads(tmap_p.read_text(encoding="utf-8"))
    tmap = {r["ticker"].upper(): int(r["cik_str"]) for r in raw.values()}

    kept, drops = [], []
    sectors_declared = {s for _t, s in TICKERS}
    n_requested = 0

    for ticker, sector in TICKERS:
        cik = tmap.get(ticker)
        if not cik:
            drops.append((ticker, "-", "ticker not resolvable to a CIK"))
            continue
        subs = cached(CACHE_DIR / f"submissions_CIK{cik:010d}.json")
        facts = cached(CACHE_DIR / f"CIK{cik:010d}.json")
        if subs is None or facts is None:
            drops.append((ticker, "-", "company metadata never fetched"))
            continue
        filings = _recent_10ks(subs, YEARS)
        if len(filings) < YEARS:
            drops.append((ticker, "-",
                          f"only {len(filings)} 10-K filing(s) in the recent window, "
                          f"{YEARS} requested"))
        for fil in filings:
            n_requested += 1
            fy = _fy_from_period_end(fil["period_end"])
            stem = f"{ticker}_FY{fy}"
            txt = CORPUS_DIR / f"{stem}.txt"
            if not txt.exists():
                drops.append((stem, sector, "primary document not retrievable"))
                continue
            text = txt.read_text(encoding="utf-8")
            scale = infer_document_scale(text)
            if not scale["confident"]:
                drops.append((stem, sector, "no declared reporting scale in the "
                                            "statement section"))
                continue
            if not build_fields_from_facts(facts, fy):
                drops.append((stem, sector, f"no XBRL facts for FY{fy}"))
                continue
            kept.append((stem, sector))

    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sectors_final = Counter(d["sector"] for d in corpus)

    n_tick = len(TICKERS)
    by_reason = Counter(r for _s, _sec, r in drops)

    print("=" * 74)
    print("  ITEM 6 -- CORPUS RECONCILIATION")
    print("=" * 74)
    print(f"  tickers targeted                      {n_tick:>5}")
    print(f"  x {YEARS} filings each  (upper bound)      {n_tick * YEARS:>5}")
    print(f"  10-K filings actually available       {n_requested:>5}"
          f"   (shortfall {n_tick * YEARS - n_requested})")
    print("  minus, per document:")
    for reason, k in by_reason.most_common():
        if reason.startswith("only "):
            continue
        print(f"    {reason:<52} {-k:>5}")
    doc_drops = sum(k for r, k in by_reason.items() if not r.startswith("only "))
    print(f"  {'=' * 58}")
    print(f"  documents retained                    {n_requested - doc_drops:>5}")
    print(f"  corpus.jsonl on disk                  {len(corpus):>5}"
          f"   {'MATCH' if n_requested - doc_drops == len(corpus) else 'MISMATCH'}")

    short = [d for d in drops if d[2].startswith("only ")]
    if short:
        print(f"\n  companies with fewer than {YEARS} recent 10-K filings ({len(short)}):")
        for t, _s, r in short:
            print(f"    {t:<8} {r}")

    print(f"\n  sectors declared in the ticker list : {len(sectors_declared)}"
          f"  {sorted(sectors_declared)}")
    print(f"  sectors present in the final corpus : {len(sectors_final)}")
    missing = sectors_declared - set(sectors_final)
    if missing:
        print(f"  -> declared but absent after exclusions: {sorted(missing)}"
              f"   (this is the 12-vs-11 discrepancy)")

    print(f"\n  per-document drops ({len(drops) - len(short)}):")
    for stem, sec, reason in drops:
        if reason.startswith("only "):
            continue
        print(f"    {stem:<16} {sec:<12} {reason}")

    Path("data/corpus_reconciliation.json").write_text(json.dumps(
        {"tickers": n_tick, "filings_available": n_requested,
         "drops": [{"doc": s, "sector": sec, "reason": r} for s, sec, r in drops],
         "kept": len(kept), "corpus_on_disk": len(corpus),
         "sectors_declared": sorted(sectors_declared),
         "sectors_final": dict(sectors_final)}, indent=2), encoding="utf-8")
    print("\n  -> data/corpus_reconciliation.json")


if __name__ == "__main__":
    main()
