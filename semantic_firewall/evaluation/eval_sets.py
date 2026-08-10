"""eval_sets.py -- frozen document sets, so results do not drift with the cache.

Every analysis in this repository was originally scoped by intersecting whatever
extractions happened to be on disk. That is convenient and wrong: extraction ran over
several sessions, so the population grew between runs and the same script produced
different numbers on different days. A paper about evaluation integrity cannot ship
that.

The sets are therefore computed once, written to ``data/eval_sets.json``, committed, and
read by every analysis. Re-running an analysis reproduces the published numbers exactly;
extending the corpus is a deliberate act that regenerates the file and is visible in the
diff.

    python -m semantic_firewall.evaluation.eval_sets --freeze
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXTRACT_DIR = Path("data/extractions")
SETS_PATH = Path("data/eval_sets.json")

MODELS = ["meta/llama-3.1-8b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"]

# set name -> methods every model must have for a document to be admissible
REQUIREMENTS = {
    "ladder": ["B2"],            # the six-configuration comparison
    "detector_coupled": ["B2", "BS"],
    "detector_uncoupled": ["B2", "BSEN"],
    "full_fields": ["B2", "BSEN"],   # income statement + balance sheet scoring
}


def _available(model: str, method: str) -> set[str]:
    d = EXTRACT_DIR / model.replace("/", "__") / method
    return {p.stem for p in d.glob("*.json")} if d.exists() else set()


def compute() -> dict:
    out = {}
    for name, methods in REQUIREMENTS.items():
        common: set[str] | None = None
        for m in MODELS:
            have: set[str] | None = None
            for meth in methods:
                a = _available(m, meth)
                have = a if have is None else (have & a)
            common = have if common is None else (common & (have or set()))
        out[name] = sorted(common or set())
    return out


def freeze() -> dict:
    sets = compute()
    SETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETS_PATH.write_text(json.dumps(
        {"models": MODELS, "requirements": REQUIREMENTS,
         "sets": {k: v for k, v in sets.items()},
         "sizes": {k: len(v) for k, v in sets.items()}},
        indent=2), encoding="utf-8")
    return sets


def load(name: str) -> set[str]:
    """The frozen document set for one analysis.

    Falls back to computing it only if the file is absent, and says so, because a
    silently recomputed set is exactly the failure this module exists to prevent.
    """
    if not SETS_PATH.exists():
        print(f"  [eval_sets] {SETS_PATH} missing; computing from the cache. "
              f"Run --freeze to pin it.", file=sys.stderr)
        return set(compute()[name])
    data = json.loads(SETS_PATH.read_text(encoding="utf-8"))
    return set(data["sets"][name])


def filter_corpus(corpus: list[dict], name: str) -> list[dict]:
    keep = load(name)
    return [d for d in corpus if f"{d['ticker']}_FY{d['fy']}" in keep]


def _main() -> None:
    ap = argparse.ArgumentParser(description="Freeze or inspect the evaluation sets.")
    ap.add_argument("--freeze", action="store_true")
    a = ap.parse_args()
    sets = freeze() if a.freeze else compute()
    for k, v in sets.items():
        print(f"  {k:<20} {len(v):>4} documents")
    if a.freeze:
        print(f"\n  wrote {SETS_PATH}")


if __name__ == "__main__":
    _main()
