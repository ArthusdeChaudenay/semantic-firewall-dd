"""sector_table.py -- per-sector accuracy under EBITDA-excluded scoring.

The table's purpose changes once the corrector is known to add nothing: it no longer
shows "where the firewall helps" but "where zero-shot extraction fails", which is the
finance-specific content worth keeping. Cells are small and no per-sector claim is
made; the table is descriptive.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import infer_document_scale, to_absolute
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
MAX_CHARS, REL_TOL = 60_000, 0.01
NO_EBITDA = ["chiffre_affaires", "ebit", "dotations_amortissements", "resultat_net"]
BS_GT = ["actif_total", "passif_total", "capitaux_propres"]
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]
BSEN_MAP = {"total_assets": "actif_total", "total_equity": "capitaux_propres",
            "total_liabilities_and_equity": "passif_total"}


def main():
    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    gt = {}
    for l in Path("data/ground_truth.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    from semantic_firewall.evaluation.eval_sets import filter_corpus
    docs = filter_corpus(corpus, "full_fields")

    print(f"paired docs (B2 and BSEN on both tiers): {len(docs)}")
    print(f"scoring: EBITDA excluded, income statement + balance sheet, +-1%\n")
    print(f"  {'sector':<13} {'docs':>4} {'8B':>7} {'49B':>7}")
    agg = defaultdict(lambda: {"docs": 0, "m": defaultdict(lambda: [0, 0])})
    for d in docs:
        stem = f"{d['ticker']}_FY{d['fy']}"
        gtf = gt[(d["cik"], d["fy"])]["fields"]
        text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
        sc = infer_document_scale(text)
        e = agg[d["sector"]]
        e["docs"] += 1
        for model in MODELS:
            safe = model.replace("/", "__")
            raw = json.loads((EXTRACT_DIR / safe / "B2" / (stem + ".json")).read_text(encoding="utf-8"))["fields"]
            bs = json.loads((EXTRACT_DIR / safe / "BSEN" / (stem + ".json")).read_text(encoding="utf-8"))["fields"]
            for f in NO_EBITDA:
                if f not in gtf:
                    continue
                t = float(gtf[f]["val"])
                v = to_absolute(DDTaxonomy._f(raw.get(f)), sc)
                e["m"][model][1] += 1
                e["m"][model][0] += int(abs(v - t) <= max(abs(t) * REL_TOL, 1.0))
            for ek, gf in BSEN_MAP.items():
                if gf not in gtf:
                    continue
                t = float(gtf[gf]["val"])
                v = to_absolute(DDTaxonomy._f(bs.get(ek)), sc)
                e["m"][model][1] += 1
                e["m"][model][0] += int(abs(v - t) <= max(abs(t) * REL_TOL, 1.0))

    rows = []
    for sec, e in agg.items():
        a = 100 * e["m"][MODELS[0]][0] / max(e["m"][MODELS[0]][1], 1)
        b = 100 * e["m"][MODELS[1]][0] / max(e["m"][MODELS[1]][1], 1)
        rows.append((sec, e["docs"], a, b))
    for sec, n, a, b in sorted(rows, key=lambda r: -r[2]):
        print(f"  {sec:<13} {n:>4} {a:>6.1f}% {b:>6.1f}%")
    tot8 = sum(agg[s]["m"][MODELS[0]][0] for s in agg), sum(agg[s]["m"][MODELS[0]][1] for s in agg)
    tot4 = sum(agg[s]["m"][MODELS[1]][0] for s in agg), sum(agg[s]["m"][MODELS[1]][1] for s in agg)
    print(f"  {'ALL':<13} {len(docs):>4} {100*tot8[0]/tot8[1]:>6.1f}% {100*tot4[0]/tot4[1]:>6.1f}%"
          f"   ({tot8[1]} instances)")


if __name__ == "__main__":
    main()
