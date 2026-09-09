"""make_figures.py -- generate every figure in the paper as vector PDF.

Fig 2 (3D)  Detector precision as a surface over the two thresholds that any such
            evaluation independently chooses: the residual at which the detector
            fires, and the relative tolerance at which a field counts as correct.
            The trap is geometric -- precision is high only near the diagonal, and
            the off-diagonal region is where "false positives" are manufactured by
            the mismatch rather than by the detector.

Fig 3 (2D)  Left: accuracy against correctness tolerance, both scales and the regex
            baseline. Right: coupling index per field.

Fig 4 (2D)  Field x sector accuracy heat map, showing that difficulty is structured
            by disclosure conventions rather than spread uniformly.

Fig 5 (2D)  Empirical CDF of the identity residual, which is what makes an area under
            a curve the wrong summary: most of the mass sits exactly at zero.

Offline; reads cached extractions only.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
from matplotlib.colors import LinearSegmentedColormap

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.extraction.scale import infer_document_scale, to_absolute
from semantic_firewall.validation.dd_base import DDTaxonomy

EXTRACT_DIR = Path("data/extractions")
FIGDIR = Path("paper/figures")
MAX_CHARS = 60_000
IS_FIELDS = ["chiffre_affaires", "ebit", "dotations_amortissements", "resultat_net"]
BS_FIELDS = ["actif_total", "passif_total", "capitaux_propres"]
MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]
LABEL = {MODELS[0]: "8B", MODELS[1]: "49B"}
PRETTY = {"chiffre_affaires": "revenue", "ebit": "op. income",
          "dotations_amortissements": "D&A", "resultat_net": "net income",
          "actif_total": "assets", "passif_total": "liab.+eq.",
          "capitaux_propres": "equity"}

# Muted, colour-blind-safe, legible in greyscale.
C_8B, C_49B, C_REG = "#2f6f9f", "#c1666b", "#7a7a7a"

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


def gather(docs, gt_all):
    """Per-model records: field errors (relative) and the identity residual."""
    from semantic_firewall.evaluation.benchmark_compare import method1_regex
    rec = {m: {"fields": [], "det": []} for m in MODELS}
    rec["regex"] = {"fields": []}
    for d in docs:
        stem = f"{d['ticker']}_FY{d['fy']}"
        gtf = gt_all[(d["cik"], d["fy"])]["fields"]
        text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
        sc = infer_document_scale(text)
        rx = method1_regex(text)
        for f in IS_FIELDS:
            if f in gtf:
                t = float(gtf[f]["val"])
                v = to_absolute(DDTaxonomy._f(rx.get(f)), sc)
                rec["regex"]["fields"].append((d["sector"], f, abs(v - t) / max(abs(t), 1.0)))
        for m in MODELS:
            safe = m.replace("/", "__")
            raw = json.loads((EXTRACT_DIR / safe / "B2" / (stem + ".json")).read_text(encoding="utf-8"))["fields"]
            bs = json.loads((EXTRACT_DIR / safe / "BS" / (stem + ".json")).read_text(encoding="utf-8"))["fields"]
            for f in IS_FIELDS:
                if f in gtf:
                    t = float(gtf[f]["val"])
                    v = to_absolute(DDTaxonomy._f(raw.get(f)), sc)
                    rec[m]["fields"].append((d["sector"], f, abs(v - t) / max(abs(t), 1.0)))
            for f in BS_FIELDS:
                if f in gtf:
                    t = float(gtf[f]["val"])
                    v = to_absolute(DDTaxonomy._f(bs.get(f)), sc)
                    rec[m]["fields"].append((d["sector"], f, abs(v - t) / max(abs(t), 1.0)))
            a = to_absolute(DDTaxonomy._f(bs.get("actif_total")), sc)
            li = to_absolute(DDTaxonomy._f(bs.get("passif_total")), sc)
            if a and li:
                errs = [abs(to_absolute(DDTaxonomy._f(bs.get(f)), sc) - float(gtf[f]["val"]))
                        / max(abs(float(gtf[f]["val"])), 1.0)
                        for f in ("actif_total", "passif_total") if f in gtf]
                if errs:
                    rec[m]["det"].append({"resid": abs(a - li) / max(abs(a), abs(li), 1.0),
                                          "max_err": max(errs)})
    return rec


# -- Figure 2: 3D F1 surface over the two thresholds ------------------------
def fig_surface(rec):
    """F1 of the identity detector as a function of the two thresholds that any such
    evaluation picks independently: the residual at which the detector fires, and the
    relative tolerance at which a field counts as correct.

    F1 rather than precision, because precision alone is near 1 over most of the plane
    and hides the structure. The surface has a ridge: performance is maximal where the
    two thresholds agree, falls away on one side because the detector fires on
    disagreements the metric forgives, and on the other because it stops firing at all.
    """
    det = rec[MODELS[0]]["det"]
    resid = np.array([x["resid"] for x in det])
    err = np.array([x["max_err"] for x in det])
    grid = np.logspace(-5, -1, 40)
    TD, TC = np.meshgrid(grid, grid, indexing="ij")
    F1 = np.zeros_like(TD)
    for i in range(TD.shape[0]):
        for j in range(TD.shape[1]):
            fires, wrong = resid > TD[i, j], err > TC[i, j]
            tp = int(np.sum(fires & wrong))
            fp = int(np.sum(fires & ~wrong))
            fn = int(np.sum(~fires & wrong))
            pr = tp / (tp + fp) if tp + fp else 0.0
            rc = tp / (tp + fn) if tp + fn else 0.0
            F1[i, j] = 2 * pr * rc / (pr + rc) if pr + rc else 0.0

    X, Y = np.log10(TD), np.log10(TC)
    fig = plt.figure(figsize=(3.5, 2.7))
    # No colour bar: colour encodes height, which the z axis already gives.
    # Dropping it lets the axes use the full column width.
    ax = fig.add_axes([0.0, 0.02, 0.90, 0.94], projection="3d")
    cmap = LinearSegmentedColormap.from_list(
        "f1", ["#f7f7f7", "#cfe0ec", "#7fa9c8", "#2f6f9f", "#16405e"])
    surf = ax.plot_surface(X, Y, F1, cmap=cmap, vmin=0, vmax=1,
                           rstride=1, cstride=1, linewidth=0.12,
                           edgecolor="white", antialiased=True)

    # the matched-threshold diagonal, drawn ON the surface
    diag = np.array([F1[k, k] for k in range(len(grid))])
    lg = np.log10(grid)
    ax.plot(lg, lg, diag + 0.012, color="#b03030", lw=1.8, zorder=20,
            label=r"matched: $\tau_d=\tau_c$")
    k = int(np.argmax(diag))
    ax.scatter([lg[k]], [lg[k]], [diag[k] + 0.02], color="#b03030", s=16, zorder=21)

    ax.set_xlabel(r"$\log_{10}\tau_d$", labelpad=-6)
    ax.set_ylabel(r"$\log_{10}\tau_c$", labelpad=-6)
    ax.set_title(r"detector $F_1$", loc="left", pad=-2)
    ax.set_zlim(0, 1.0)
    ax.set_xticks([-5, -3, -1])
    ax.set_yticks([-5, -3, -1])
    ax.set_zticks([0, 0.5, 1.0])
    ax.tick_params(axis="both", pad=-4)
    ax.tick_params(axis="z", pad=-1)
    ax.view_init(elev=27, azim=-52)
    ax.set_box_aspect((1, 1, 0.55))

    ax.legend(loc="upper right", bbox_to_anchor=(0.99, 0.99), frameon=False,
              fontsize=6.8, handlelength=1.5, borderaxespad=0.0)
    fig.savefig(FIGDIR / "threshold_surface.pdf")
    plt.close(fig)
    print("  threshold_surface.pdf  (ridge peak F1=%.2f at tau=%.0e)"
          % (diag[k], grid[k]))


# ── Figure 3: tolerance curves + coupling index ─────────────────────────────
def fig_tolerance_coupling(rec):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(5.4, 2.1))
    tols = np.logspace(-5, -1, 60)
    for key, col, lab, ls in ((MODELS[0], C_8B, "zero-shot 8B", "-"),
                              (MODELS[1], C_49B, "zero-shot 49B", "-"),
                              ("regex", C_REG, "regex baseline", "--")):
        e = np.array([x[2] for x in rec[key]["fields"]])
        acc = [100 * np.mean(e <= t) for t in tols]
        ax1.plot(tols, acc, color=col, label=lab, lw=1.5, ls=ls)
    for t, lab in ((1e-3, "0.1%"), (1e-2, "1%")):
        ax1.axvline(t, color="#cccccc", lw=0.7, zorder=0)
    ax1.set_xscale("log")
    ax1.set_xlabel("relative correctness tolerance")
    ax1.set_ylabel("accuracy (%)")
    ax1.set_ylim(0, 100)
    ax1.legend(frameon=False, loc="lower left", bbox_to_anchor=(0.02, 0.02),
               handlelength=1.6, borderaxespad=0.0)
    ax1.set_title("(a) accuracy is tolerance-sensitive", loc="left")

    # (b) the ladder, scored with and without the coupled field. Hatched bars are the
    # published numbers; solid bars are the same runs rescored with the coupled field
    # removed. The configurations are indistinguishable once it is gone.
    lad = json.loads(Path("data/ladder_for_figure.json").read_text(encoding="utf-8"))
    cfgs = ["B1", "B2", "B3", "B4", "B5", "B6"]
    x = np.arange(len(cfgs))
    w = 0.38
    withv = [lad["with"].get(c) for c in cfgs]
    without = [lad["without"].get(c) for c in cfgs]
    for i, (v, u) in enumerate(zip(withv, without)):
        if v is not None:
            ax2.bar(i - w / 2, v, width=w, color="white", edgecolor=C_49B,
                    hatch="////", linewidth=0.8,
                    label="with coupled field" if i == 1 else None)
        if u is not None:
            ax2.bar(i + w / 2, u, width=w, color=C_8B,
                    label="without" if i == 1 else None)
    ax2.axhline(without[1], color="#444444", lw=0.8, ls=":", zorder=0)
    ax2.set_xticks(x)
    ax2.set_xticklabels(cfgs)
    ax2.set_ylim(50, 106)
    ax2.set_ylabel("accuracy (%)")
    ax2.set_xlabel("configuration (8B)")
    # ylim is extended to 106 so this band is empty: the tallest bar is 86.8
    ax2.legend(frameon=False, loc="upper center", ncol=2, fontsize=6.4,
               handlelength=1.2, columnspacing=1.0, borderaxespad=0.2)
    ax2.set_title("(b) the ladder collapses", loc="left")
    fig.tight_layout(w_pad=1.6)
    fig.savefig(FIGDIR / "tolerance_coupling.pdf")
    plt.close(fig)
    print("  tolerance_coupling.pdf")


# ── Figure 4: field x sector heat map ───────────────────────────────────────
def fig_heatmap(rec):
    order = IS_FIELDS + BS_FIELDS
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for sector, f, e in rec[MODELS[0]]["fields"]:
        agg[sector][f][1] += 1
        agg[sector][f][0] += int(e <= 0.01)
    sectors = sorted(agg, key=lambda s: -sum(agg[s][f][0] for f in order)
                     / max(sum(agg[s][f][1] for f in order), 1))
    M = np.full((len(sectors), len(order)), np.nan)
    for i, s in enumerate(sectors):
        for j, f in enumerate(order):
            c, n = agg[s][f]
            if n:
                M[i, j] = 100 * c / n
    fig, ax = plt.subplots(figsize=(4.6, 2.7))
    cmap = LinearSegmentedColormap.from_list(
        "acc", ["#9c3d3d", "#e8d9a0", "#3f7d4f"])
    im = ax.imshow(np.ma.masked_invalid(M), cmap=cmap, vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([PRETTY[f] for f in order], rotation=35, ha="right")
    ax.set_yticks(range(len(sectors)))
    ax.set_yticklabels(sectors)
    for i in range(len(sectors)):
        for j in range(len(order)):
            if not np.isnan(M[i, j]):
                ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center",
                        fontsize=5.6, color="white" if M[i, j] < 45 or M[i, j] > 80 else "black")
    fig.colorbar(im, ax=ax, label="accuracy (%)", fraction=0.03, pad=0.02)
    ax.set_title("zero-shot accuracy, 8B, by field and sector", loc="left")
    fig.savefig(FIGDIR / "field_sector.pdf")
    plt.close(fig)
    print("  field_sector.pdf")


# ── Figure 5: residual ECDF ─────────────────────────────────────────────────
def fig_ecdf(rec):
    fig, ax = plt.subplots(figsize=(3.2, 2.0))
    # Both curves jump at the left edge and then run flat, so a legend box placed
    # anywhere inside the axes sits on top of a plateau. The series are labelled on
    # their own plateaus instead, which also puts the percentage where it is read.
    for m, col in ((MODELS[0], C_8B), (MODELS[1], C_49B)):
        r = np.array(sorted(x["resid"] for x in rec[m]["det"]))
        zero = float(np.mean(r <= 1e-9))
        rr = np.maximum(r, 1e-6)
        ax.step(rr, np.arange(1, len(rr) + 1) / len(rr), where="post",
                color=col, lw=1.5)
        ax.text(0.42, zero + 0.04, f"{LABEL[m]}, {100*zero:.0f}% at zero",
                transform=ax.get_yaxis_transform(), color=col, fontsize=7,
                ha="center", va="bottom")
    ax.set_xscale("log")
    ax.set_xlabel("identity residual (log scale, floored at $10^{-6}$)")
    ax.set_ylabel("cumulative fraction")
    ax.set_ylim(0, 1.08)
    ax.set_title("most documents satisfy the identity exactly", loc="left")
    fig.savefig(FIGDIR / "residual_ecdf.pdf")
    plt.close(fig)
    print("  residual_ecdf.pdf")


def main():
    FIGDIR.mkdir(parents=True, exist_ok=True)
    docs, gt_all = load()
    print(f"documents: {len(docs)}")
    rec = gather(docs, gt_all)
    fig_surface(rec)
    fig_tolerance_coupling(rec)
    fig_heatmap(rec)
    fig_ecdf(rec)


if __name__ == "__main__":
    main()
