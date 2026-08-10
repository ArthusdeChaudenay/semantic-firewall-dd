"""Validate the scale inference itself, which the paper never does.

Everything downstream depends on the multiplier read off the filing header. If it is
wrong, every field of that document compares as wrong, and the paper would attribute
that to the model. The check does not need the inference to be trusted: for each
document, the ratio between the reference value and the value printed in the table is
an observable, and a correct multiplier is the one that makes that ratio 1.
"""
import json
import math
import pathlib
from collections import Counter

from semantic_firewall.extraction.scale import infer_document_scale
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT = pathlib.Path("data/extractions/meta__llama-3.1-8b-instruct/B2")
corpus = [json.loads(l) for l in open("data/corpus.jsonl", encoding="utf-8")]
gt = {}
for l in open("data/ground_truth.jsonl", encoding="utf-8"):
    r = json.loads(l)
    gt[(r["cik"], r["fy"])] = r["fields"]

agree, disagree, nodata = 0, 0, 0
implied = Counter()
mismatches = []
for d in corpus:
    stem = "%s_FY%d" % (d["ticker"], d["fy"])
    p = EXTRACT / (stem + ".json")
    if not p.exists():
        continue
    text = pathlib.Path(d["path"]).read_text(encoding="utf-8")[:60000]
    sc = infer_document_scale(text)
    raw = json.loads(p.read_text(encoding="utf-8"))["fields"]
    g = gt[(d["cik"], d["fy"])]
    # implied multiplier from the field the model is most reliable on
    ratios = []
    for f in ("resultat_net", "chiffre_affaires", "ebit"):
        v = DDTaxonomy._f(raw.get(f))
        if f in g and v:
            ratios.append(float(g[f]["val"]) / v)
    if not ratios:
        nodata += 1
        continue
    ratios.sort()
    med = ratios[len(ratios) // 2]
    # snap to the nearest power of ten; a correct inference reproduces it
    k = round(math.log10(med)) if med > 0 else None
    if k is None:
        nodata += 1
        continue
    obs = 10.0 ** k
    implied["1e%d" % k] += 1
    if abs(math.log10(obs) - math.log10(sc["multiplier"])) < 0.5:
        agree += 1
    else:
        disagree += 1
        mismatches.append((stem, sc["label"], sc["multiplier"], obs, med))

tot = agree + disagree
print("scale inference validated against the observed reference/printed ratio")
print("  documents checked        : %d   (%d without usable fields)" % (tot, nodata))
print("  inference agrees         : %d  (%.1f%%)" % (agree, 100 * agree / max(tot, 1)))
print("  inference disagrees      : %d" % disagree)
print("  implied multipliers      : %s" % dict(implied))
for stem, lab, mult, obs, med in mismatches[:12]:
    print("     %-14s inferred %-9s (%.0e)  observed %.0e   median ratio %.3g"
          % (stem, lab, mult, obs, med))
