"""build_benchmark_corpus.py — build a real, FY-aligned 10-K corpus from SEC EDGAR.

This replaces the previous corpus, whose fiscal year came from a hand-written label
that disagreed with the available XBRL facts for 32 of 46 documents (audit D14). Here
the fiscal year is read from the FILING ITSELF (``reportDate`` in the submissions
API), so the document and its ground truth refer to the same period by construction —
the mismatch cannot arise.

For each ticker we take the N most recent 10-K filings, download the primary
document, reduce it to the financial-statements section, and record:

    {path, ticker, cik, fy, period_end, accession, doc_type, scale}

Then XBRL ground truth is fetched for exactly those (cik, fy) pairs.

Everything is cached on disk, so re-runs are offline and reproducible. Gaps are
always logged, never silently dropped.

Run:
    export SEC_USER_AGENT="Your Name (you@example.com)"
    python -m scripts.build_benchmark_corpus --years 2 --limit 60
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.evaluation.xbrl_ground_truth import (
    CACHE_DIR,
    _headers,
    build_fields_from_facts,
    fetch_company_facts,
    load_ticker_cik_map,
)
from semantic_firewall.extraction.scale import infer_document_scale

CORPUS_DIR = Path("data/corpus")
FILING_CACHE = CACHE_DIR / "filings"
CORPUS_JSONL = Path("data/corpus.jsonl")
GT_JSONL = Path("data/ground_truth.jsonl")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc}"

# 60 large caps across 12 sectors. Deliberately includes the hard cases the audit
# identified: banks with no "Net sales" line (JPM, WFC, GS...), off-calendar fiscal
# years (MSFT, ORCL, MDT, DE), content amortisation as D&A proxy (NFLX), depletion
# (CVX, XOM), and loss-making periods (BA).
TICKERS: list[tuple[str, str]] = [
    ("AAPL", "Technology"), ("MSFT", "Technology"), ("GOOGL", "Technology"),
    ("AMZN", "Technology"), ("META", "Technology"), ("NVDA", "Technology"),
    ("CSCO", "Technology"), ("IBM", "Technology"), ("ORCL", "Technology"),
    ("ADBE", "Technology"), ("QCOM", "Technology"), ("NFLX", "Technology"),
    ("INTC", "Technology"), ("AMD", "Technology"), ("CRM", "Technology"),
    ("JPM", "Banking"), ("BAC", "Banking"), ("C", "Banking"),
    ("MS", "Banking"), ("WFC", "Banking"), ("GS", "Banking"),
    ("V", "Payments"), ("MA", "Payments"), ("AXP", "Payments"),
    ("JNJ", "Healthcare"), ("PFE", "Healthcare"), ("ABBV", "Healthcare"),
    ("MRK", "Healthcare"), ("LLY", "Healthcare"), ("AMGN", "Healthcare"),
    ("GILD", "Healthcare"), ("UNH", "Healthcare"), ("ABT", "Healthcare"),
    ("MDT", "Healthcare"), ("BMY", "Healthcare"),
    ("KO", "Consumer"), ("PEP", "Consumer"), ("PG", "Consumer"),
    ("MDLZ", "Consumer"), ("CL", "Consumer"),
    ("WMT", "Retail"), ("TGT", "Retail"), ("HD", "Retail"),
    ("LOW", "Retail"), ("COST", "Retail"),
    ("BA", "Industrial"), ("CAT", "Industrial"), ("DE", "Industrial"),
    ("MMM", "Industrial"), ("HON", "Industrial"), ("GE", "Industrial"),
    ("UPS", "Transport"), ("FDX", "Transport"),
    ("CVX", "Energy"), ("XOM", "Energy"), ("COP", "Energy"),
    ("T", "Telecom"), ("VZ", "Telecom"), ("TMUS", "Telecom"),
    ("TSLA", "Automotive"), ("F", "Automotive"), ("GM", "Automotive"),
]

# Where the financial statements begin. Used to cut multi-megabyte filings down to
# the section that actually contains the numbers, which both fits the context window
# and removes the risk-factor prose that dominates a 10-K by volume.
# Filers word this heading several ways: "Consolidated Statements of Operations"
# (Apple, Amazon), "...of Income" (Microsoft), "...of Earnings" (Deere),
# "Consolidated Income Statement" (IBM), "Statements of Consolidated Income" (P&G).
_STMT_RE = re.compile(
    r"consolidated\s+statements?\s+of\s+(?:operations|income|earnings)"
    r"|consolidated\s+(?:income|earnings)\s+statements?"
    r"|statements?\s+of\s+consolidated\s+(?:operations|income|earnings)",
    re.IGNORECASE,
)
_SEGMENT_CHARS = 60_000

# A money figure as printed in a statement table: 1,234 / 1,234.5 / (1,234).
_MONEY_RE = re.compile(r"\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?")


def _get(url: str, timeout: int = 60) -> requests.Response:
    r = requests.get(url, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    time.sleep(0.15)  # SEC fair-access: stay under 10 req/s
    return r


def _fetch_submissions(cik: int, ttl_days: int = 30) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"submissions_CIK{cik:010d}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < ttl_days * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    data = _get(SUBMISSIONS_URL.format(cik=cik)).json()
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def _recent_10ks(subs: dict, n: int) -> list[dict]:
    """The n most recent 10-K filings, each with its own reported period end."""
    r = subs.get("filings", {}).get("recent", {})
    out = []
    for form, accn, rep, doc in zip(r.get("form", []), r.get("accessionNumber", []),
                                    r.get("reportDate", []), r.get("primaryDocument", [])):
        if form != "10-K" or not rep or not doc:
            continue
        out.append({"accession": accn, "period_end": rep, "primary_doc": doc})
        if len(out) >= n:
            break
    return out


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """Minimal, dependency-free HTML→text that preserves table row structure.

    Row and cell boundaries matter: the anchoring detector (E5) checks whether an
    amount sits near its label *on the same or an adjacent line*, so collapsing a
    table into one blob would destroy the signal being measured.
    """
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)</t[dh]>", "  ", s)
    s = re.sub(r"(?i)</tr>|<br\s*/?>|</p>|</div>|</table>", "\n", s)
    s = _TAG_RE.sub(" ", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#8217;", "'"), ("&#8220;", '"'), ("&#8221;", '"'),
                 ("&#151;", "—"), ("&#8212;", "—"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    s = re.sub(r"&#\d+;", " ", s)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    return _NL_RE.sub("\n\n", "\n".join(ln for ln in lines if ln))


def _statements_section(text: str) -> tuple[str, bool]:
    """Slice around the *actual* consolidated income statement.

    Returns ``(slice, found)``. The heading appears several times in a 10-K — in the
    table of contents, in Item 15's financial-statement index, in cross-references —
    and only once above the real table. Neither "first" nor "last" is reliable:
    taking the last match landed Apple's slice inside Part III (proxy
    cross-references) with no figures at all.

    So each candidate is scored by the density of printed money figures in the window
    that follows it, and the densest wins. That is what distinguishes a real table
    from a mention of one, and it needs no per-filer special-casing.
    """
    matches = list(_STMT_RE.finditer(text))
    if not matches:
        return text[:_SEGMENT_CHARS], False
    best_start, best_score = None, -1
    for m in matches:
        window = text[m.start(): m.start() + 8_000]
        score = len(_MONEY_RE.findall(window))
        if score > best_score:
            best_score, best_start = score, m.start()
    start = max(0, (best_start or 0) - 3_000)
    return text[start:start + _SEGMENT_CHARS], best_score > 0


def _fy_from_period_end(period_end: str) -> int:
    """Fiscal year keyed on period-end year — the same convention as the XBRL side.

    Both sides must agree, otherwise D14 returns through the back door.
    """
    return int(period_end[:4])


def build(years: int, limit: int) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    FILING_CACHE.mkdir(parents=True, exist_ok=True)
    tmap = load_ticker_cik_map()

    corpus: list[dict] = []
    gt_records: list[dict] = []
    gaps: list[str] = []

    for ticker, sector in TICKERS:
        if len({c["ticker"] for c in corpus}) >= limit and ticker not in {c["ticker"] for c in corpus}:
            break
        cik = tmap.get(ticker)
        if not cik:
            gaps.append(f"{ticker}: CIK not found"); continue
        try:
            subs = _fetch_submissions(cik)
            facts = fetch_company_facts(cik)
        except Exception as e:
            gaps.append(f"{ticker}: metadata fetch failed — {e}"); continue

        for filing in _recent_10ks(subs, years):
            fy = _fy_from_period_end(filing["period_end"])
            accn_nodash = filing["accession"].replace("-", "")
            stem = f"{ticker}_FY{fy}"
            txt_path = CORPUS_DIR / f"{stem}.txt"

            if not txt_path.exists():
                url = ARCHIVE_URL.format(cik=cik, accn_nodash=accn_nodash,
                                         doc=filing["primary_doc"])
                try:
                    html = _get(url).text
                except Exception as e:
                    gaps.append(f"{stem}: download failed — {e}"); continue
                full = _html_to_text(html)
                section, found = _statements_section(full)
                if not found:
                    gaps.append(f"{stem}: no 'consolidated statements of operations' heading")
                txt_path.write_text(section, encoding="utf-8")

            text = txt_path.read_text(encoding="utf-8")
            scale = infer_document_scale(text)

            # Quality gate. A filing whose reporting scale cannot be read from its own
            # text is not scoreable against absolute XBRL values (D13) and would be
            # skipped downstream anyway; admitting it would only inflate the reported
            # corpus size. Excluded here instead, with the reason logged.
            if not scale["confident"]:
                gaps.append(f"{stem}: no declared reporting scale in the statement "
                            f"section (financial statements likely filed as a "
                            f"separate exhibit) — excluded")
                continue

            # Ground truth for THIS filing's fiscal year — no nearest-year fallback,
            # so a document is either aligned with its XBRL facts or excluded.
            fields = build_fields_from_facts(facts, fy)
            if not fields:
                gaps.append(f"{stem}: no XBRL facts for FY{fy}"); continue

            corpus.append({
                "path": str(txt_path).replace("\\", "/"),
                "ticker": ticker, "sector": sector, "cik": cik, "fy": fy,
                "period_end": filing["period_end"], "accession": filing["accession"],
                "doc_type": "compte_resultat",
                "chars": len(text),
                "scale_label": scale["label"], "scale_confident": scale["confident"],
            })
            gt_records.append({
                "cik": cik, "fy": fy, "fp": "FY", "form": "10-K",
                "ticker": ticker, "entity": facts.get("entityName", ""),
                "accession": filing["accession"], "period_end": filing["period_end"],
                "fields": {k: {"val": v["val"], "concept": v["concept"],
                               "end": v.get("end"), "accn": v.get("accn"),
                               "derived": bool(v.get("derived", False))}
                           for k, v in fields.items()},
            })
            print(f"  ✓ {stem:<14} {len(text):>7,} chars  scale={scale['label']:<9} "
                  f"{len(fields)} GT fields")

    CORPUS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_JSONL.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n"
                                    for c in corpus), encoding="utf-8")
    GT_JSONL.write_text("".join(json.dumps(g, ensure_ascii=False) + "\n"
                                for g in gt_records), encoding="utf-8")

    digest = hashlib.sha256(CORPUS_JSONL.read_bytes()).hexdigest()[:16]
    n_scale = sum(1 for c in corpus if c["scale_confident"])
    print(f"\n  corpus:       {len(corpus):>4} filings  -> {CORPUS_JSONL} (sha256:{digest})")
    print(f"  ground truth: {len(gt_records):>4} records  -> {GT_JSONL}")
    print(f"  companies:    {len({c['ticker'] for c in corpus}):>4}"
          f"   fiscal years: {sorted({c['fy'] for c in corpus})}")
    print(f"  declared reporting scale found: {n_scale}/{len(corpus)}")
    if gaps:
        print(f"\n  {len(gaps)} gap(s), not silently dropped:")
        for g in gaps[:40]:
            print(f"    - {g}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build a FY-aligned SEC 10-K corpus.")
    ap.add_argument("--years", type=int, default=2, help="10-K filings per company")
    ap.add_argument("--limit", type=int, default=60, help="max companies")
    a = ap.parse_args()
    build(a.years, a.limit)
