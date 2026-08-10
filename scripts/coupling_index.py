"""coupling_index.py -- new experiment: is method coupling predictable, or only
detectable after the fact?

The audit showed that removing one field removes the entire reported gain. That is a
post-hoc diagnosis. This experiment asks the stronger question: given only the label
definition and the system's rules, can the inflated gain be PREDICTED before any
rescoring?

For a derived label L(f) = g(f_1, ..., f_k) and a system rule that computes the same
field by the same g, the field is scored correct exactly when g(extracted) matches
g(truth). When g is injective in each argument -- as a sum is -- that happens (up to
tolerance interactions) exactly when every constituent is correct. So the predicted
number of "repaired" instances is the count of documents where all constituents are
correct and the field was previously absent:

    predicted_gain = |{d : field absent in raw(d), all constituents correct in d}|

We compute this prediction from the raw extraction alone, compare it against the
observed gain, and report a per-field coupling index

    C(f) = (observed gain on f) / (total observed gain)

so that a reviewer can see which fields carry an improvement before trusting it.

Offline; reads cached extractions only.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import infer_document_scale, to_absolute
from semantic_firewall.validation.corrector import apply_corrections
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
MAX_CHARS, REL_TOL = 60_000, 0.01
FIELDS = ["chiffre_affaires", "ebit", "ebitda", "dotations_amortissements", "resultat_net"]
CONSTITUENTS = {"ebitda": ["ebit", "dotations_amortissements"]}
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]
PRETTY = {"chiffre_affaires": "revenue", "ebit": "operating income",
          "ebitda": "EBITDA", "dotations_amortissements": "D&A",
          "resultat_net": "net income"}


def load():
    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    gt = {}
    for l in Path("data/ground_truth.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    from semantic_firewall.evaluation.eval_sets import filter_corpus
    return filter_corpus(corpus, "ladder"), gt


def ok(v, t):
    return abs(v - t) <= max(abs(t) * REL_TOL, 1.0)


def main():
    docs, gt_all = load()
    out = {}
    print(f"paired documents: {len(docs)}\n")
    for model in MODELS:
        safe = model.replace("/", "__")
        gain_by_field = Counter()
        predicted, observed, absent_and_constituents_ok = 0, 0, 0
        n_scored = 0
        for d in docs:
            stem = f"{d['ticker']}_FY{d['fy']}"
            p2 = EXTRACT_DIR / safe / "B2" / (stem + ".json")
            if not p2.exists():
                continue
            gtf = gt_all[(d["cik"], d["fy"])]["fields"]
            text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
            sc = infer_document_scale(text)
            raw = json.loads(p2.read_text(encoding="utf-8"))["fields"]
            flat = {k: ("" if raw.get(k) is None else str(raw[k])) for k in FIELDS}
            corr, _ = apply_corrections(flat, "compte_resultat", text, use_regex_backfill=True)

            for f in FIELDS:
                if f not in gtf:
                    continue
                n_scored += 1
                t = float(gtf[f]["val"])
                v2 = to_absolute(DDTaxonomy._f(flat.get(f)), sc)
                v6 = to_absolute(DDTaxonomy._f(corr.get(f)), sc)
                if ok(v6, t) and not ok(v2, t):
                    gain_by_field[f] += 1
                    observed += 1

            # prediction, from the raw extraction and the rule definition only
            for f, parts in CONSTITUENTS.items():
                if f not in gtf:
                    continue
                was_absent = raw.get(f) in (None, "", 0)
                parts_ok = all(
                    p in gtf and ok(to_absolute(DDTaxonomy._f(raw.get(p)), sc),
                                    float(gtf[p]["val"]))
                    for p in parts)
                if was_absent and parts_ok:
                    predicted += 1
                    absent_and_constituents_ok += 1

        print("=" * 70)
        print(f"  {model}")
        print("=" * 70)
        print(f"  scored instances                       {n_scored}")
        print(f"  observed instances repaired by B6      {observed}")
        print(f"  predicted from label definition alone  {predicted}")
        err = abs(predicted - observed)
        print(f"  prediction error                       {err} "
              f"({100*err/max(observed,1):.1f}%)")
        print(f"\n  coupling index C(f) = share of the gain carried by field f")
        for f in FIELDS:
            g = gain_by_field[f]
            c = g / observed if observed else 0.0
            bar = "#" * int(round(40 * c))
            flag = "  <- derived label" if f in CONSTITUENTS else ""
            print(f"    {PRETTY[f]:<18} {g:>3}  C={c:5.3f} {bar}{flag}")
        out[model] = {"n_scored": n_scored, "observed": observed,
                      "predicted": predicted,
                      "gain_by_field": {PRETTY[k]: v for k, v in gain_by_field.items()},
                      "coupling_index": {PRETTY[f]: (gain_by_field[f] / observed
                                                     if observed else 0.0)
                                         for f in FIELDS}}
        print()

    Path("data/coupling_index.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("-> data/coupling_index.json")


if __name__ == "__main__":
    main()
