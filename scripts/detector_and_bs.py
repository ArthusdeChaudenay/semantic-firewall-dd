"""detector_and_bs.py -- Reviewer items 3, 5 and parts of Tier 2.

Item 3  Rescore the ladder INCLUDING balance-sheet fields. Table 4 scored only the
        five income-statement fields (296/69 = 4.29 fields/doc) and never said so,
        while the paper's thesis is "verify over what the filing must print" -- and
        then declined to score exactly those fields.

Item 5  Restate the balance-sheet detector as a 2x2 at threshold residual > 0 with
        Clopper-Pearson intervals, print the positive-label definition explicitly,
        and explain every false positive individually. AUC over a distribution that
        is 73% ties is the wrong summary.

Also   tolerance sweep (exact / 0.1% / 1% / 5%) and whether recompute_margin ever
       writes into a scored field.

--bs-method BS    original French-key 3-field pass (actif/passif/capitaux)
--bs-method BSEN  English-key 4-field pass incl. total_liabilities (uncoupled)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import infer_document_scale, to_absolute
from semantic_firewall.validation.corrector import apply_corrections
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
MAX_CHARS = 60_000
IS_FIELDS = ["chiffre_affaires", "ebit", "ebitda", "dotations_amortissements", "resultat_net"]
NO_EBITDA = [f for f in IS_FIELDS if f != "ebitda"]
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]

# extraction key -> ground-truth field
BS_MAP = {"BS": {"actif_total": "actif_total", "passif_total": "passif_total",
                 "capitaux_propres": "capitaux_propres"},
          "BSEN": {"total_assets": "actif_total", "total_liabilities": "total_liabilities",
                   "total_equity": "capitaux_propres",
                   "total_liabilities_and_equity": "passif_total"}}


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial confidence interval. 6/6 gives a lower bound well below 1."""
    if n == 0:
        return (0.0, 1.0)
    try:
        from scipy.stats import beta
        lo = 0.0 if k == 0 else beta.ppf(alpha / 2, k, n - k + 1)
        hi = 1.0 if k == n else beta.ppf(1 - alpha / 2, k + 1, n - k)
        return float(lo), float(hi)
    except ImportError:
        pass
    # bisection on the exact binomial tails, no scipy required
    from math import comb

    def bin_cdf(p, k_, n_):
        return sum(comb(n_, i) * p ** i * (1 - p) ** (n_ - i) for i in range(k_ + 1))

    def solve(target, k_, upper):
        lo_, hi_ = 0.0, 1.0
        for _ in range(200):
            mid = (lo_ + hi_) / 2
            v = bin_cdf(mid, k_, n) if upper else 1 - bin_cdf(k_ - 1, mid, n)
            if (v > target) == upper:
                lo_ = mid
            else:
                hi_ = mid
        return (lo_ + hi_) / 2

    lo = 0.0 if k == 0 else solve(1 - alpha / 2, k - 1, False)
    hi = 1.0 if k == n else solve(alpha / 2, k, True)
    return lo, hi


def load(bs_method: str):
    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    gt = {}
    for l in Path("data/ground_truth.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    from semantic_firewall.evaluation.eval_sets import filter_corpus
    name = "detector_coupled" if bs_method == "BS" else "detector_uncoupled"
    return filter_corpus(corpus, name), gt


def ok(val, truth, tol_rel):
    tol = max(abs(truth) * tol_rel, 1.0) if tol_rel > 0 else 1.0
    return abs(val - truth) <= tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bs-method", default="BS", choices=["BS", "BSEN"])
    args = ap.parse_args()
    bsm = args.bs_method
    keymap = BS_MAP[bsm]

    docs, gt_all = load(bsm)
    print(f"balance-sheet pass: {bsm}    paired docs with both tiers: {len(docs)}\n")

    for model in MODELS:
        safe = model.replace("/", "__")
        rows = []          # per (doc, field) scored cells
        det = []           # per-doc detector records
        margin_writes = 0
        n_partial_label = 0
        n_gt_imbalanced = 0
        for d in docs:
            stem = f"{d['ticker']}_FY{d['fy']}"
            p2 = EXTRACT_DIR / safe / "B2" / (stem + ".json")
            pb = EXTRACT_DIR / safe / bsm / (stem + ".json")
            if not p2.exists() or not pb.exists():
                continue
            gtf = gt_all[(d["cik"], d["fy"])]["fields"]
            text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
            sc = infer_document_scale(text)
            raw = json.loads(p2.read_text(encoding="utf-8"))["fields"]
            bs = json.loads(pb.read_text(encoding="utf-8"))["fields"]

            flat = {k: ("" if raw.get(k) is None else str(raw[k])) for k in IS_FIELDS}
            corrected, log = apply_corrections(flat, "compte_resultat", text,
                                               use_regex_backfill=True)
            margin_writes += sum(1 for e in log if e["rule"] == "recompute_margin"
                                 and e["field"] in IS_FIELDS)

            for f in NO_EBITDA:
                if f not in gtf:
                    continue
                t = float(gtf[f]["val"])
                rows.append({"doc": stem, "field": f, "truth": t, "part": "IS",
                             "B2": to_absolute(DDTaxonomy._f(raw.get(f)), sc),
                             "B6": to_absolute(DDTaxonomy._f(corrected.get(f)), sc)})
            for ek, gf in keymap.items():
                if gf not in gtf:
                    continue
                t = float(gtf[gf]["val"])
                v = to_absolute(DDTaxonomy._f(bs.get(ek)), sc)
                rows.append({"doc": stem, "field": gf, "truth": t, "part": "BS",
                             "B2": v, "B6": v})

            # ---- detector ------------------------------------------------------
            if bsm == "BSEN":
                a = to_absolute(DDTaxonomy._f(bs.get("total_assets")), sc)
                li = to_absolute(DDTaxonomy._f(bs.get("total_liabilities")), sc)
                eq = to_absolute(DDTaxonomy._f(bs.get("total_equity")), sc)
                resid = abs(a - (li + eq)) / max(abs(a), 1.0) if (a and li and eq) else None
                inputs = ["actif_total", "total_liabilities", "capitaux_propres"]
                ekeys = {"actif_total": "total_assets", "total_liabilities": "total_liabilities",
                         "capitaux_propres": "total_equity"}
            else:
                a = to_absolute(DDTaxonomy._f(bs.get("actif_total")), sc)
                li = to_absolute(DDTaxonomy._f(bs.get("passif_total")), sc)
                resid = abs(a - li) / max(abs(a), abs(li), 1.0) if (a and li) else None
                inputs = ["actif_total", "passif_total"]
                ekeys = {"actif_total": "actif_total", "passif_total": "passif_total"}
            if resid is None:
                continue
            # A detector can only be scored where EVERY input of its identity is
            # independently tagged. Amazon and Amgen, for instance, do not tag
            # us-gaap:Liabilities at all: the model's liabilities figure is
            # unscoreable, so a residual it causes would be recorded as a false
            # positive when in fact the label is simply missing. Documents with an
            # incomplete label set are excluded and counted, not scored.
            per = {}
            for gf in inputs:
                if gf in gtf:
                    per[gf] = {"got": to_absolute(DDTaxonomy._f(bs.get(ekeys[gf])), sc),
                               "truth": float(gtf[gf]["val"])}
            if len(per) < len(inputs):
                n_partial_label += 1
                continue
            # The identity must also hold IN THE REFERENCE DATA. Assets = Liabilities +
            # StockholdersEquity is not a US-GAAP identity: the consolidated balance
            # sheet balances only once non-controlling and redeemable interests are
            # included, and the obvious concept (StockholdersEquity) is parent-only.
            # It fails on 47% of our filings by up to 2%. Where the reference itself
            # does not balance, a nonzero residual proves nothing about the
            # extraction, so those documents are excluded and counted.
            if bsm == "BSEN":
                gA, gL = float(gtf["actif_total"]["val"]), float(gtf["total_liabilities"]["val"])
                gE = float(gtf["capitaux_propres"]["val"])
                if abs(gA - (gL + gE)) / max(abs(gA), 1.0) > 1e-6:
                    n_gt_imbalanced += 1
                    continue
            label = any(not ok(v["got"], v["truth"], 0.01) for v in per.values())
            det.append({"doc": stem, "resid": resid, "label": label, "per": per})

        print("=" * 76)
        print(f"  {model}")
        print("=" * 76)

        # ---- item 3: ladder with balance-sheet fields ------------------------
        n_is = sum(1 for r in rows if r["part"] == "IS")
        n_bs = sum(1 for r in rows if r["part"] == "BS")
        print(f"\n  (3) LADDER, EBITDA excluded, balance-sheet fields INCLUDED")
        print(f"      scored instances: {n_is} income-statement + {n_bs} balance-sheet "
              f"= {len(rows)}")
        for tol, lbl in ((0.0, "exact"), (0.001, "+-0.1%"), (0.01, "+-1%"), (0.05, "+-5%")):
            line = f"      {lbl:<7}"
            for cfg in ("B2", "B6"):
                for part, nm in (("IS", "IS"), ("BS", "BS"), (None, "all")):
                    sel = [r for r in rows if part is None or r["part"] == part]
                    c = sum(1 for r in sel if ok(r[cfg], r["truth"], tol))
                    line += f"  {cfg}/{nm} {100*c/max(len(sel),1):5.1f}%"
            print(line)

        # ---- item 5: detector 2x2 --------------------------------------------
        fires = [x for x in det if x["resid"] > 1e-9]
        tp = sum(1 for x in fires if x["label"])
        fp = sum(1 for x in fires if not x["label"])
        fn = sum(1 for x in det if x["resid"] <= 1e-9 and x["label"])
        tn = sum(1 for x in det if x["resid"] <= 1e-9 and not x["label"])
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        pl, ph = clopper_pearson(tp, tp + fp)
        rl, rh = clopper_pearson(tp, tp + fn)
        print(f"\n  (5) BALANCE-SHEET DETECTOR, threshold residual > 0   (n={len(det)})")
        print(f"      positive label := at least one of {inputs} differs from its")
        print(f"                        XBRL value by more than 1%")
        print(f"      identity        := {'A = L + E (3 independent tags)' if bsm=='BSEN' else 'A = L+E as tagged (A and L+E are equal by definition)'}")
        print(f"                    predicted+   predicted-")
        print(f"      actual+   {tp:>10}   {fn:>10}")
        print(f"      actual-   {fp:>10}   {tn:>10}")
        print(f"      precision {100*prec:5.1f}%  95% CI [{100*pl:.1f}, {100*ph:.1f}]")
        print(f"      recall    {100*rec:5.1f}%  95% CI [{100*rl:.1f}, {100*rh:.1f}]")
        print(f"      positives {tp+fn}/{len(det)} ({100*(tp+fn)/max(len(det),1):.1f}%)")

        # Matched thresholds. The detector fires on ANY nonzero residual while
        # correctness forgives 1%, so a 0.3% disagreement is a false positive by
        # construction -- the two thresholds are on incommensurate scales. Firing at
        # residual > 1% puts them on the same footing.
        for thr in (0.001, 0.01):
            f2 = [x for x in det if x["resid"] > thr]
            tp2 = sum(1 for x in f2 if x["label"])
            fp2 = sum(1 for x in f2 if not x["label"])
            fn2 = sum(1 for x in det if x["resid"] <= thr and x["label"])
            pr2 = tp2 / max(tp2 + fp2, 1)
            rc2 = tp2 / max(tp2 + fn2, 1)
            l1, h1 = clopper_pearson(tp2, tp2 + fp2)
            print(f"      matched threshold residual > {thr:<5} : "
                  f"P={100*pr2:5.1f}% [{100*l1:.1f},{100*h1:.1f}]  R={100*rc2:5.1f}%  "
                  f"TP={tp2} FP={fp2} FN={fn2}")

        if fp:
            print(f"\n      the {fp} false positive(s), individually:")
            for x in fires:
                if x["label"]:
                    continue
                worst = max((abs(v["got"] - v["truth"]) / max(abs(v["truth"]), 1)
                             for v in x["per"].values()), default=0.0)
                print(f"        {x['doc']:<14} residual={x['resid']:.4f}  "
                      f"largest field error={100*worst:.3f}%  "
                      f"{'-> tolerance artifact' if worst <= 0.01 else '-> other'}")
        print(f"\n      recompute_margin writes into a scored field: {margin_writes}")
        print()


if __name__ == "__main__":
    main()
