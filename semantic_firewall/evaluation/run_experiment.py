"""
run_experiment.py — One CLI to run experiments E0–E3 (and hooks for E4–E7).

    python -m semantic_firewall.evaluation.run_experiment e0 --manifest data/cik_fy.csv
    python -m semantic_firewall.evaluation.run_experiment e1 --corpus data/corpus.jsonl \\
           --ground-truth data/ground_truth.jsonl
    python -m semantic_firewall.evaluation.run_experiment e2 --corpus data/corpus.jsonl \\
           --ground-truth data/ground_truth.jsonl --baselines B0,B1,B2,B5 --model <id>
    python -m semantic_firewall.evaluation.run_experiment e3 --corpus data/corpus.jsonl \\
           --ground-truth data/ground_truth.jsonl

Data contracts (kept deliberately small and explicit):
  corpus.jsonl       one JSON object per line:
                     {"path": "...txt", "cik": 320193, "fy": 2023, "doc_type": "compte_resultat"}
  ground_truth.jsonl output of xbrl_ground_truth.build_manifest (E0).

Nothing here fabricates results: E1/E2/E3 require model access and the E0 manifest.
Missing inputs produce an explicit message, never a silent or invented number.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from semantic_firewall.config import seed_everything


def _load_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _gt_index(gt_path: str) -> dict:
    idx = {}
    for rec in _load_jsonl(gt_path):
        idx[(rec["cik"], rec["fy"])] = rec["fields"]
    return idx


def _read_text(path: str) -> str:
    from semantic_firewall.extraction.llm_extractor import extract_text_from_file
    return extract_text_from_file(path)


def _read_text_and_scale(path: str) -> tuple[str, dict]:
    """Text plus the reporting scale inferred FROM THAT TEXT (D13).

    XBRL facts are absolute USD; statement tables print thousands or millions. The
    multiplier must come from the document, never from the ground truth — deriving
    it from the label would leak the answer into the prediction.
    """
    from semantic_firewall.extraction.scale import infer_document_scale
    text = _read_text(path)
    return text, infer_document_scale(text)


# ── E0 ──────────────────────────────────────────────────────────────────────
def cmd_e0(args) -> None:
    from semantic_firewall.evaluation.xbrl_ground_truth import build_manifest
    pairs = []
    for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("cik"):
            continue
        c, y = line.split(",")[:2]
        pairs.append((int(c), int(y)))
    build_manifest(pairs, Path(args.out))


# ── E1 — detector metrics (does a violation predict a wrong value?) ──────────
def cmd_e1(args) -> None:
    from semantic_firewall.evaluation import metrics
    from semantic_firewall.evaluation.baselines import KEY_FIELDS
    from semantic_firewall.evaluation.benchmark_compare import method2_zeroshot
    from semantic_firewall.evaluation.detector import build_detection_dataset

    gt_index = _gt_index(args.ground_truth)
    corpus = _load_jsonl(args.corpus)
    records = []
    n_no_scale = 0
    for doc in corpus:
        text, scale = _read_text_and_scale(doc["path"])
        n_no_scale += int(not scale["confident"])
        raw = method2_zeroshot(text)  # frozen raw extraction (B2)
        records.append({"cik": doc["cik"], "fy": doc["fy"],
                        "doc_type": doc.get("doc_type", "compte_resultat"),
                        "scale": scale,
                        "raw_fields": {k: raw.get(k) for k in KEY_FIELDS}})
    scores, labels, skipped = build_detection_dataset(records, gt_index)
    if not scores:
        print("No overlap between corpus and ground truth — nothing to score.")
        print(f"({skipped} documents had no matching XBRL record.)")
        return
    summary = metrics.summarize(scores, labels, k=min(10, len(scores)))
    print("E1 — accounting-identity violation as an error detector")
    print(json.dumps(summary, indent=2))
    print(f"n scored: {len(scores)}  positives: {sum(labels)}  "
          f"negatives: {len(labels) - sum(labels)}")
    print(f"skipped (no GT match, or scale not established): {skipped}")
    if n_no_scale:
        print(f"documents with no declared reporting scale: {n_no_scale} (D13)")
    if sum(labels) in (0, len(labels)):
        print("WARNING: one class is empty — ROC-AUC is undefined. Do not report a "
              "number here; report the class imbalance instead.")


# ── E2 — baseline ladder correctness vs XBRL ─────────────────────────────────
def cmd_e2(args) -> None:
    from semantic_firewall.evaluation.baselines import BASELINES, BaselineContext, KEY_FIELDS
    from semantic_firewall.evaluation.xbrl_ground_truth import score_extraction

    seed_everything()
    gt_index = _gt_index(args.ground_truth)
    corpus = _load_jsonl(args.corpus)
    refs = args.baselines.split(",") if args.baselines else list(BASELINES)

    table = {r: {"correct": 0, "total": 0} for r in refs}
    for doc in corpus:
        gt = gt_index.get((doc["cik"], doc["fy"]))
        if not gt:
            continue
        text, scale = _read_text_and_scale(doc["path"])
        for ref in refs:
            b = BASELINES[ref]
            ctx = BaselineContext(text=text, doc_name=doc["path"],
                                  doc_type=doc.get("doc_type", "compte_resultat"),
                                  ground_truth=gt, model=args.model)
            fields = b.run(ctx)
            scored = score_extraction({k: fields.get(k) for k in KEY_FIELDS}, gt,
                                      scale=scale)
            table[ref]["correct"] += sum(1 for v in scored.values() if v["within_tol"])
            table[ref]["total"] += len(scored)

    print("E2 — correctness (within tolerance) vs XBRL, per baseline")
    for ref in refs:
        t = table[ref]
        acc = 100 * t["correct"] / t["total"] if t["total"] else float("nan")
        print(f"  {ref} {BASELINES[ref].label:<38} {acc:6.1f}%  ({t['correct']}/{t['total']})")


# ── E3 — B4 (CoT) vs B5 (identity layer): does CoT fix arithmetic? ───────────
def cmd_e3(args) -> None:
    from semantic_firewall.evaluation.baselines import BaselineContext, KEY_FIELDS, _b2_zeroshot, _b4_cot
    from semantic_firewall.evaluation.xbrl_ground_truth import score_extraction

    gt_index = _gt_index(args.ground_truth)
    corpus = _load_jsonl(args.corpus)
    # Confusion matrix: "CoT changed the value" x "the value became correct".
    changed_better = changed_worse = changed_same = unchanged = 0
    for doc in corpus:
        gt = gt_index.get((doc["cik"], doc["fy"]))
        if not gt:
            continue
        text, scale = _read_text_and_scale(doc["path"])
        ctx = BaselineContext(text=text, doc_type=doc.get("doc_type", "compte_resultat"))
        base = _b2_zeroshot(ctx)
        cot = _b4_cot(ctx)
        sb = score_extraction({k: base.get(k) for k in KEY_FIELDS}, gt, scale=scale)
        sc = score_extraction({k: cot.get(k) for k in KEY_FIELDS}, gt, scale=scale)
        for fld in KEY_FIELDS:
            if str(base.get(fld)) == str(cot.get(fld)):
                unchanged += 1
                continue
            before = sb.get(fld, {}).get("within_tol", False)
            after = sc.get(fld, {}).get("within_tol", False)
            if after and not before:
                changed_better += 1
            elif before and not after:
                changed_worse += 1
            else:
                changed_same += 1
    print("E3 — CoT self-verification effect on arithmetic (per field)")
    print(f"  changed & became correct : {changed_better}")
    print(f"  changed & became wrong   : {changed_worse}")
    print(f"  changed, no correctness Δ: {changed_same}")
    print(f"  unchanged                : {unchanged}")
    print("  (A clean negative result here is publishable and motivates the "
          "deterministic layer — audit E3.)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Semantic Firewall experiments E0–E3.")
    sub = ap.add_subparsers(dest="exp", required=True)

    p0 = sub.add_parser("e0", help="build XBRL ground truth")
    p0.add_argument("--manifest", required=True)
    p0.add_argument("--out", default="data/ground_truth.jsonl")
    p0.set_defaults(func=cmd_e0)

    for name, fn in (("e1", cmd_e1), ("e2", cmd_e2), ("e3", cmd_e3)):
        p = sub.add_parser(name)
        p.add_argument("--corpus", required=True)
        p.add_argument("--ground-truth", required=True)
        p.add_argument("--model", default=None)
        if name == "e2":
            p.add_argument("--baselines", default=None, help="comma list, e.g. B0,B1,B2,B5")
        p.set_defaults(func=fn)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
