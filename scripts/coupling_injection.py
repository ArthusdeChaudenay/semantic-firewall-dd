"""coupling_injection.py -- controlled demonstration that label coupling is a general
mechanism, not a quirk of one field.

The EBITDA case is observational: the field happened to be untagged, a label had to be
constructed, and a rule happened to reconstruct it. That leaves open whether the effect
is peculiar to that field. Here it is produced deliberately and measured.

For every ordered pair (a, b) of independently tagged fields we define a SYNTHETIC
field S with reference value L(S) = truth(a) + truth(b), declare it absent from the raw
extraction (as an untagged, unreported quantity would be), and add a corrector rule
S := extracted(a) + extracted(b). This is exactly the EBITDA construction with
different operands. We then measure the phantom gain -- the share of documents on which
the injected field is scored correct -- against the theoretical prediction, which is the
joint accuracy of the two constituents.

If the mechanism is what we claim, observed phantom gain should track joint constituent
accuracy across all pairs, with no dependence on which fields were chosen. The severity
of a coupled label is thus fully determined by how accurate its constituents already
are: the better the system is on the parts, the larger the free gain on the whole.
"""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import infer_document_scale, to_absolute
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
FIGDIR = Path("paper/figures")
MAX_CHARS, REL_TOL = 60_000, 0.01
# Independently tagged fields only: the whole point is that none of these is derived.
FIELDS = ["chiffre_affaires", "ebit", "dotations_amortissements", "resultat_net",
          "actif_total", "capitaux_propres"]
PRETTY = {"chiffre_affaires": "rev", "ebit": "opinc",
          "dotations_amortissements": "D&A", "resultat_net": "NI",
          "actif_total": "assets", "capitaux_propres": "equity"}
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]
LABEL = {MODELS[0]: "8B", MODELS[1]: "49B"}
C_8B, C_49B = "#2f6f9f", "#c1666b"

plt.rcParams.update({
    "font.family": "serif", "font.size": 8, "axes.labelsize": 8,
    "axes.titlesize": 8.5, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})


def load():
    corpus = [json.loads(l) for l in Path("data/corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    gt = {}
    for l in Path("data/ground_truth.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    from semantic_firewall.evaluation.eval_sets import filter_corpus
    return filter_corpus(corpus, "detector_coupled"), gt


def main():
    docs, gt_all = load()
    FIGDIR.mkdir(parents=True, exist_ok=True)
    results = {}
    fig, ax = plt.subplots(figsize=(3.3, 2.5))

    for model, col in ((MODELS[0], C_8B), (MODELS[1], C_49B)):
        safe = model.replace("/", "__")
        # per document, per field: extracted absolute value and reference
        table = []
        for d in docs:
            stem = f"{d['ticker']}_FY{d['fy']}"
            gtf = gt_all[(d["cik"], d["fy"])]["fields"]
            text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
            sc = infer_document_scale(text)
            raw = json.loads((EXTRACT_DIR / safe / "B2" / (stem + ".json")).read_text(encoding="utf-8"))["fields"]
            bs = json.loads((EXTRACT_DIR / safe / "BS" / (stem + ".json")).read_text(encoding="utf-8"))["fields"]
            src = dict(raw)
            src.update(bs)
            row = {}
            for f in FIELDS:
                if f in gtf:
                    t = float(gtf[f]["val"])
                    v = to_absolute(DDTaxonomy._f(src.get(f)), sc)
                    row[f] = {"v": v, "t": t,
                              "ok": abs(v - t) <= max(abs(t) * REL_TOL, 1.0)}
            table.append(row)

        pred, obs, names = [], [], []
        for a, b in combinations(FIELDS, 2):
            usable = [r for r in table if a in r and b in r]
            if len(usable) < 25:
                continue
            joint = np.mean([r[a]["ok"] and r[b]["ok"] for r in usable])
            # inject the coupling and score the synthetic field
            hits = 0
            for r in usable:
                truth_s = r[a]["t"] + r[b]["t"]
                got_s = r[a]["v"] + r[b]["v"]           # the rule, applied
                hits += int(abs(got_s - truth_s) <= max(abs(truth_s) * REL_TOL, 1.0))
            pred.append(joint)
            obs.append(hits / len(usable))
            names.append(f"{PRETTY[a]}+{PRETTY[b]}")
        ax.scatter(pred, obs, s=16, color=col, alpha=0.85, edgecolor="white",
                   linewidth=0.4, label=f"{LABEL[model]} ({len(pred)} pairs)", zorder=3)
        r = float(np.corrcoef(pred, obs)[0, 1]) if len(pred) > 2 else float("nan")
        mae = float(np.mean(np.abs(np.array(pred) - np.array(obs))))
        results[model] = {"pairs": names, "predicted": pred, "observed": obs,
                          "pearson_r": r, "mae": mae}
        print(f"  {model}: {len(pred)} injected couplings  "
              f"r={r:.3f}  MAE={mae:.3f}")
        print(f"    phantom gain range: {min(obs):.2f} to {max(obs):.2f}")

    ax.plot([0, 1], [0, 1], color="#888888", lw=0.9, ls="--", zorder=1)
    ax.text(0.635, 0.605, "identity", rotation=38, color="#888888", fontsize=6.5)
    ax.set_xlim(0.58, 1.02)
    ax.set_ylim(0.58, 1.02)
    ax.set_xlabel("joint accuracy of the two constituents")
    ax.set_ylabel("phantom gain on the injected field")
    ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(0.99, 0.02),
              handlelength=1.2, borderaxespad=0.0)
    ax.set_title("injected couplings behave as predicted", loc="left")
    fig.savefig(FIGDIR / "coupling_injection.pdf")
    plt.close(fig)
    print("  -> coupling_injection.pdf")
    Path("data/coupling_injection.json").write_text(json.dumps(results, indent=2),
                                                    encoding="utf-8")


if __name__ == "__main__":
    main()
