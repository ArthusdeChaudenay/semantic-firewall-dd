"""mcnemar_ladder.py -- Reviewer item 4: is the zero exact, or merely equal in aggregate?

Equal correct-field COUNTS are not the same as identical per-field OUTCOMES: two
configurations could each fix and break the same number of fields and still differ
everywhere. This computes, for every adjacent ladder pair, the discordant-pair counts
in both directions and the exact (binomial) McNemar p-value, under EBITDA-excluded
scoring. It also verifies field-by-field identity of the extracted VALUES, which is a
stronger statement than identity of outcomes.

Exact McNemar: under H0 the b discordant pairs favouring A and c favouring B are
Binomial(b+c, 1/2). With b = c = 0 there is no evidence of difference and p = 1.
"""
from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import infer_document_scale, to_absolute
from semantic_firewall.validation.corrector import apply_corrections
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
MAX_CHARS, REL_TOL = 60_000, 0.01
ALL_IS = ["chiffre_affaires", "ebit", "ebitda", "dotations_amortissements", "resultat_net"]
NO_EBITDA = [f for f in ALL_IS if f != "ebitda"]
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]
PAIRS = [("B2", "B3"), ("B2", "B4"), ("B2", "B5"), ("B2", "B6"),
         ("B4", "B6"), ("B1", "B2")]


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def load():
    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    gt = {}
    for l in Path("data/ground_truth.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    from semantic_firewall.evaluation.eval_sets import filter_corpus
    return filter_corpus(corpus, "ladder"), gt


def candidates(model, stem, text, raw):
    from semantic_firewall.evaluation.benchmark_compare import method1_regex
    safe = model.replace("/", "__")
    c = {"B1": method1_regex(text), "B2": raw}
    for meth in ("B3", "B4"):
        p = EXTRACT_DIR / safe / meth / (stem + ".json")
        if p.exists():
            c[meth] = json.loads(p.read_text(encoding="utf-8"))["fields"]
    flat = {k: ("" if raw.get(k) is None else str(raw[k])) for k in ALL_IS}
    c["B5"] = dict(flat)
    c["B6"], _ = apply_corrections(flat, "compte_resultat", text, use_regex_backfill=True)
    return c


def main():
    docs, gt_all = load()
    print(f"paired subset: {len(docs)} filings   scoring: EBITDA EXCLUDED")
    print()
    for model in MODELS:
        safe = model.replace("/", "__")
        # Keyed by (document, field) rather than positional: a configuration that is
        # missing for some document would otherwise shorten its list and silently
        # misalign every subsequent comparison.
        rec: dict[tuple, dict] = {}
        for d in docs:
            stem = f"{d['ticker']}_FY{d['fy']}"
            p2 = EXTRACT_DIR / safe / "B2" / (stem + ".json")
            gtf = {k: v for k, v in gt_all[(d["cik"], d["fy"])]["fields"].items() if k in NO_EBITDA}
            if not p2.exists() or not gtf:
                continue
            text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
            sc = infer_document_scale(text)
            raw = json.loads(p2.read_text(encoding="utf-8"))["fields"]
            cands = candidates(model, stem, text, raw)
            for f, meta in gtf.items():
                truth = float(meta["val"])
                tol = max(abs(truth) * REL_TOL, 1.0)
                cell = {}
                for b, fl in cands.items():
                    got = to_absolute(DDTaxonomy._f(fl.get(f)), sc)
                    cell[b] = {"ok": abs(got - truth) <= tol, "val": got}
                rec[(stem, f)] = cell

        print("=" * 74)
        print(f"  {model}   ({len(rec)} scored field instances)")
        print("=" * 74)
        print(f"  {'pair':<12} {'n':>5} {'A only':>7} {'B only':>7} {'both':>6} "
              f"{'neither':>8} {'p (exact)':>10}")
        for a, b in PAIRS:
            common = [c for c in rec.values() if a in c and b in c]
            if not common:
                continue
            b_cnt = sum(1 for c in common if c[a]["ok"] and not c[b]["ok"])
            c_cnt = sum(1 for c in common if c[b]["ok"] and not c[a]["ok"])
            both = sum(1 for c in common if c[a]["ok"] and c[b]["ok"])
            neither = sum(1 for c in common if not c[a]["ok"] and not c[b]["ok"])
            p_val = exact_mcnemar(b_cnt, c_cnt)
            print(f"  {a}->{b:<9} {len(common):>5} {b_cnt:>7} {c_cnt:>7} {both:>6} "
                  f"{neither:>8} {p_val:>10.3f}")

        print()
        print("  value-level identity vs B2 (stronger than equal outcome counts):")
        for b in ("B3", "B4", "B5", "B6"):
            common = [(k, c) for k, c in rec.items() if "B2" in c and b in c]
            if not common:
                continue
            diff = [(k, c["B2"]["val"], c[b]["val"]) for k, c in common
                    if abs(c["B2"]["val"] - c[b]["val"]) > 1e-6]
            if not diff:
                print(f"    B2 vs {b}: IDENTICAL field-by-field on all {len(common)}")
            else:
                flips = sum(1 for k, c in common
                            if c["B2"]["ok"] != c[b]["ok"])
                print(f"    B2 vs {b}: {len(diff)}/{len(common)} values differ, "
                      f"{flips} change correctness")
                for (stem, f), v0, v1 in diff[:3]:
                    print(f"       {stem:<14} {f:<26} {v0:,.0f} -> {v1:,.0f}")
        print()


if __name__ == "__main__":
    main()
