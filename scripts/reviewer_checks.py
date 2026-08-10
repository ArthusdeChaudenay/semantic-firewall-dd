"""reviewer_checks.py -- Reviewer blocking issues (1) and (2), run before anything else.

(1) Rescore the ladder with EBITDA EXCLUDED from the scored field set.
    The charge: EBITDA's ground-truth label is defined as EBIT + D&A, and the
    corrector's first rule derives EBITDA as EBIT + D&A. So B6's EBITDA scores correct
    exactly when EBIT and D&A were each already correct -- the label construction
    executed twice. If so, the +11.2 / +13.8 headline collapses.

(2) Per-rule FIRE COUNTS for the corrector (not accuracy deltas).
    Table 8 reported every rule inert under leave-one-out and concluded the rules are
    "mutually redundant". An equally consistent explanation: ONE rule does all the work
    and seven never fire. Accuracy deltas cannot separate these; fire counts can.

Offline -- reads only cached extractions.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import classify_error, infer_document_scale, to_absolute
from semantic_firewall.validation.corrector import INCOME_STATEMENT_RULES, apply_corrections
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
MAX_CHARS = 60_000
REL_TOL = 0.01
ALL_FIELDS = ["chiffre_affaires", "ebit", "ebitda",
              "dotations_amortissements", "resultat_net"]
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]
LADDER = ("B1", "B2", "B3", "B4", "B5", "B6")


def load_inputs():
    corpus = [json.loads(l)
              for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines()
              if l.strip()]
    gt = {}
    for l in Path("data/ground_truth.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    return corpus, gt


def paired(corpus):
    """The frozen ladder set, not whatever happens to be cached (see eval_sets)."""
    from semantic_firewall.evaluation.eval_sets import filter_corpus
    return filter_corpus(corpus, "ladder")


def score(fields, gt_fields, scale, keep):
    out = {}
    for f, meta in gt_fields.items():
        if f not in keep:
            continue
        truth = float(meta["val"])
        got = to_absolute(DDTaxonomy._f(fields.get(f)), scale)
        tol = max(abs(truth) * REL_TOL, 1.0)
        out[f] = {"ok": abs(got - truth) <= tol,
                  "kind": classify_error(got, truth, REL_TOL)}
    return out


def candidates(model, stem, text, raw_fields):
    from semantic_firewall.evaluation.benchmark_compare import method1_regex
    safe = model.replace("/", "__")
    c = {"B1": method1_regex(text), "B2": raw_fields}
    for meth in ("B3", "B4"):
        p = EXTRACT_DIR / safe / meth / (stem + ".json")
        if p.exists():
            c[meth] = json.loads(p.read_text(encoding="utf-8"))["fields"]
    flat = {k: ("" if raw_fields.get(k) is None else str(raw_fields[k])) for k in ALL_FIELDS}
    c["B5"] = dict(flat)
    corrected, _log = apply_corrections(flat, "compte_resultat", text, use_regex_backfill=True)
    c["B6"] = corrected
    return c


def iter_docs(model, docs, gt_all, keep):
    """Yield (stem, gtf, scale, candidates) for documents this model extracted."""
    safe = model.replace("/", "__")
    for d in docs:
        stem = d["ticker"] + "_FY" + str(d["fy"])
        gtf = {k: v for k, v in gt_all[(d["cik"], d["fy"])]["fields"].items() if k in keep}
        p2 = EXTRACT_DIR / safe / "B2" / (stem + ".json")
        if not gtf or not p2.exists():
            continue
        text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
        raw = json.loads(p2.read_text(encoding="utf-8"))["fields"]
        yield stem, gtf, infer_document_scale(text), candidates(model, stem, text, raw), text, raw


def part1(docs, gt_all):
    ladder_acc = {}
    no_ebitda = [f for f in ALL_FIELDS if f != "ebitda"]
    summary = {}
    for label, keep in (("WITH ebitda (as published)", ALL_FIELDS),
                        ("WITHOUT ebitda", no_ebitda)):
        print("=" * 78)
        print("  (1) LADDER -- " + label)
        print("=" * 78)
        for model in MODELS:
            tot = {b: [0, 0] for b in LADDER}
            flips = Counter()
            for _stem, gtf, sc, cands, _text, _raw in iter_docs(model, docs, gt_all, keep):
                s2 = score(cands["B2"], gtf, sc, keep)
                s6 = score(cands["B6"], gtf, sc, keep)
                for f in gtf:
                    if f in s2 and f in s6 and s6[f]["ok"] and not s2[f]["ok"]:
                        flips[f] += 1
                for b, fl in cands.items():
                    s = score(fl, gtf, sc, keep)
                    tot[b][0] += sum(1 for v in s.values() if v["ok"])
                    tot[b][1] += len(s)
            print("\n  " + model)
            base = 100 * tot["B2"][0] / tot["B2"][1] if tot["B2"][1] else 0.0
            for b in LADDER:
                c, n = tot[b]
                if not n:
                    continue
                acc = 100 * c / n
                delta = "" if b == "B2" else "   B2%+5.1f" % (acc - base)
                print("    %-3s %5.1f%%   %4d/%-4d%s" % (b, acc, c, n, delta))
                if model == MODELS[0]:
                    ladder_acc.setdefault(label, {})[b] = round(acc, 1)
                if b == "B6":
                    summary[(label, model)] = acc - base
            print("    fields flipped wrong->right by B6: " + str(dict(flips)))
        print()
    # Emit the figure input rather than maintaining it by hand: a repository whose
    # subject is evaluation integrity should not carry transcribed numbers.
    fig_in = {"with": {}, "without": {}}
    for label, key in (("WITH ebitda (as published)", "with"),
                       ("WITHOUT ebitda", "without")):
        fig_in[key] = dict(ladder_acc.get(label, {}))
    Path("data/ladder_for_figure.json").write_text(
        json.dumps(fig_in, indent=2), encoding="utf-8")
    print("  wrote data/ladder_for_figure.json (input to Figure 1b)")
    print()

    print("=" * 78)
    print("  (1) VERDICT -- B6 minus B2")
    print("=" * 78)
    print("  %-46s %10s %10s" % ("model", "with", "without"))
    for model in MODELS:
        w = summary.get(("WITH ebitda (as published)", model))
        wo = summary.get(("WITHOUT ebitda", model))
        print("  %-46s %+9.1f %+9.1f" % (model[:46], w, wo))
    print()


def part2(docs, gt_all):
    print("=" * 78)
    print("  (2) PER-RULE FIRE COUNTS (field instances actually changed)")
    print("=" * 78)
    for model in MODELS:
        fires = Counter()
        docs_touched = Counter()
        n_docs = 0
        for _stem, _gtf, _sc, _cands, text, raw in iter_docs(model, docs, gt_all, ALL_FIELDS):
            n_docs += 1
            flat = {k: ("" if raw.get(k) is None else str(raw[k])) for k in ALL_FIELDS}
            _c, log = apply_corrections(flat, "compte_resultat", text, use_regex_backfill=True)
            seen = set()
            for e in log:
                fires[e["rule"]] += 1
                if e["rule"] not in seen:
                    docs_touched[e["rule"]] += 1
                    seen.add(e["rule"])
        print("\n  %s   (full fixed-point iteration, n=%d docs)" % (model, n_docs))
        print("    %-28s %6s %6s" % ("rule", "fires", "docs"))
        names = [n for n, _ in INCOME_STATEMENT_RULES] + ["regex_backfill", "recompute_margin"]
        for name in names:
            print("    %-28s %6d %6d" % (name, fires[name], docs_touched[name]))
        active = [n for n, _ in INCOME_STATEMENT_RULES if fires[n] > 0]
        print("    -> arithmetic rules that EVER fire: %d of 8  %s"
              % (len(active), active))

        print("\n    leave-one-out: which rule picks up the slack?")
        for drop in sorted(n for n, _ in INCOME_STATEMENT_RULES):
            f2 = Counter()
            for _stem, _gtf, _sc, _cands, text, raw in iter_docs(model, docs, gt_all, ALL_FIELDS):
                flat = {k: ("" if raw.get(k) is None else str(raw[k])) for k in ALL_FIELDS}
                _c, log = apply_corrections(flat, "compte_resultat", text,
                                            use_regex_backfill=True, disabled_rules={drop})
                for e in log:
                    if e["rule"] not in ("regex_backfill", "recompute_margin"):
                        f2[e["rule"]] += 1
            print("      drop %-26s -> %s" % (drop, dict(f2) or "{} (nothing fires)"))


def main():
    corpus, gt_all = load_inputs()
    docs = paired(corpus)
    print("paired subset: %d filings\n" % len(docs))
    part1(docs, gt_all)
    part2(docs, gt_all)


if __name__ == "__main__":
    main()
