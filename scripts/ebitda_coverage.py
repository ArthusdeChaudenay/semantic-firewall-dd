"""ebitda_coverage.py -- Reviewer item 2: is EBITDA absent from filings, or absent
from our window?

The paper claims the EBITDA identity is unenforceable because "no 10-K income
statement contains an EBITDA line". That is true of the income statement, but many
filers present Adjusted EBITDA with a GAAP reconciliation in MD&A (Item 7), which sits
outside the 60k-character slice we extract from. If coverage in the full text is
substantial, the honest claim is "unenforceable within our extraction window", not
"unenforceable at extraction time" -- and the generalisation in the discussion needs
the same qualifier.

This re-downloads nothing: it re-derives the FULL text from the cached filing HTML if
present, else refetches the primary document (cheap, cached by the SEC layer).

Reports per document: hit counts for EBITDA / Adjusted EBITDA, which SEC item the
first hit falls in, and whether a reconciliation table sits within 2000 characters.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

from scripts.build_benchmark_corpus import _html_to_text
from semantic_firewall.evaluation.xbrl_ground_truth import CACHE_DIR, _headers

FULLTEXT_DIR = CACHE_DIR / "fulltext"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"

RX_EBITDA = re.compile(r"\bEBITDA\b", re.IGNORECASE)
RX_ADJ = re.compile(r"\badjusted\s+EBITDA\b", re.IGNORECASE)
# A GAAP reconciliation is signalled by the bridging language filers must use.
RX_RECON = re.compile(
    r"reconcil\w+|most\s+directly\s+comparable\s+GAAP|"
    r"net\s+income\s*\(?loss\)?\s*(?:plus|add|:)|non-?GAAP\s+financial\s+measure",
    re.IGNORECASE)
RX_ITEM7 = re.compile(r"item\s*7\.?\s*[—–-]?\s*management", re.IGNORECASE)
RX_ITEM8 = re.compile(r"item\s*8\.?\s*[—–-]?\s*financial\s+statements", re.IGNORECASE)


def full_text(doc: dict) -> str | None:
    """Full filing text, cached on disk. None if it cannot be obtained."""
    FULLTEXT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{doc['ticker']}_FY{doc['fy']}"
    cache = FULLTEXT_DIR / f"{stem}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    subs = CACHE_DIR / f"submissions_CIK{doc['cik']:010d}.json"
    if not subs.exists():
        return None
    r = json.loads(subs.read_text(encoding="utf-8"))["filings"]["recent"]
    primary = None
    for accn, rep, pdoc in zip(r["accessionNumber"], r["reportDate"], r["primaryDocument"]):
        if accn == doc["accession"]:
            primary = pdoc
            break
    if not primary:
        return None
    url = ARCHIVE.format(cik=doc["cik"], accn=doc["accession"].replace("-", ""), doc=primary)
    try:
        resp = requests.get(url, headers=_headers(), timeout=90)
        resp.raise_for_status()
        time.sleep(0.15)
    except Exception as e:
        print(f"    [skip] {stem}: {e}")
        return None
    txt = _html_to_text(resp.text)
    cache.write_text(txt, encoding="utf-8")
    return txt


def section_of(text: str, pos: int) -> str:
    """Which SEC item the offset falls in, by nearest preceding item heading."""
    i7 = [m.start() for m in RX_ITEM7.finditer(text) if m.start() <= pos]
    i8 = [m.start() for m in RX_ITEM8.finditer(text) if m.start() <= pos]
    last7, last8 = (max(i7) if i7 else -1), (max(i8) if i8 else -1)
    if last7 < 0 and last8 < 0:
        return "before Item 7"
    return "Item 7 (MD&A)" if last7 > last8 else "Item 8 (statements)"


def main() -> None:
    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines()
              if l.strip()]
    rows, examples = [], []
    sections = Counter()
    for doc in corpus:
        stem = f"{doc['ticker']}_FY{doc['fy']}"
        txt = full_text(doc)
        if txt is None:
            rows.append({"stem": stem, "full": None})
            continue
        hits = list(RX_EBITDA.finditer(txt))
        adj = list(RX_ADJ.finditer(txt))
        recon = False
        sec = None
        if hits:
            p = hits[0].start()
            sec = section_of(txt, p)
            sections[sec] += 1
            window = txt[max(0, p - 2000): p + 2000]
            recon = bool(RX_RECON.search(window))
            if len(examples) < 3:
                examples.append((stem, sec, txt[max(0, p - 220): p + 260].replace("\n", " ")))
        # is it inside the 60k slice we actually extract from?
        sliced = Path(doc["path"]).read_text(encoding="utf-8")[:60_000]
        rows.append({"stem": stem, "full": len(txt), "hits": len(hits), "adj": len(adj),
                     "section": sec, "recon": recon,
                     "in_slice": bool(RX_EBITDA.search(sliced))})

    ok = [r for r in rows if r.get("full")]
    with_hit = [r for r in ok if r["hits"]]
    with_adj = [r for r in ok if r["adj"]]
    with_recon = [r for r in with_hit if r["recon"]]
    in_slice = [r for r in ok if r["in_slice"]]

    print("=" * 74)
    print("  ITEM 2 -- EBITDA coverage in the FULL 10-K text")
    print("=" * 74)
    print(f"  documents with retrievable full text : {len(ok)}/{len(rows)}")
    print(f"  mentioning EBITDA at all             : {len(with_hit):3d}  "
          f"({100*len(with_hit)/max(len(ok),1):.1f}%)")
    print(f"  mentioning 'Adjusted EBITDA'         : {len(with_adj):3d}  "
          f"({100*len(with_adj)/max(len(ok),1):.1f}%)")
    print(f"  with a reconciliation within 2000 ch : {len(with_recon):3d}  "
          f"({100*len(with_recon)/max(len(ok),1):.1f}%)")
    print(f"  EBITDA present in our 60k slice      : {len(in_slice):3d}  "
          f"({100*len(in_slice)/max(len(ok),1):.1f}%)   <-- what the model could see")
    print(f"\n  first-mention section: {dict(sections)}")
    print("\n  examples:")
    for stem, sec, ex in examples:
        print(f"    [{stem}] {sec}\n      ...{ex.strip()[:230]}...")
    Path("data/ebitda_coverage.json").write_text(
        json.dumps({"rows": rows, "summary": {
            "n_fulltext": len(ok), "n_any": len(with_hit), "n_adjusted": len(with_adj),
            "n_reconciled": len(with_recon), "n_in_slice": len(in_slice)}},
            indent=2), encoding="utf-8")
    print("\n  -> data/ebitda_coverage.json")


if __name__ == "__main__":
    main()
