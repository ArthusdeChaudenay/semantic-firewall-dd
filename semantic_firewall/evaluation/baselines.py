"""
baselines.py — Experiment E2: the full baseline ladder, honestly configured.

The audit requires the M1 regex baseline to receive the SAME patterns as the
system's internal fallback (D4 — done in benchmark_compare/​corrector) and a proper
ladder from an oracle upper bound to the full hybrid, at ≥ 2 model tiers (an open
8B and a frontier model, D10).

    B0  Oracle XBRL (upper bound)                       — from ground truth, no LLM
    B1  Regex, tuned in good faith                      — same patterns as the system
    B2  LLM zero-shot, JSON-schema-constrained decoding — the fair LLM baseline
    B3  B2 + self-consistency (k=5, majority vote)
    B4  B2 + CoT self-verification  (the old M3)        — expected confirmation bias
    B5  B2 + deterministic identity layer, CORRECTOR OFF (detection only)
    B6  B5 + correction, gains attributed rule-by-rule  (E6)

Each entry is a callable ``run(ctx) -> {field: value}`` plus metadata. LLM-backed
baselines need API access; B0 needs the E0 ground truth. Nothing here fabricates
numbers — running them is what produces results (see run_experiment.py).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from semantic_firewall.config import seed_everything
from semantic_firewall.validation.dd_base import DDTaxonomy

KEY_FIELDS = ["chiffre_affaires", "ebit", "ebitda", "dotations_amortissements", "resultat_net"]


@dataclass
class BaselineContext:
    """Everything a baseline might need for one document."""
    text: str
    doc_name: str = ""
    doc_type: str = "compte_resultat"
    ground_truth: dict = field(default_factory=dict)   # for B0
    model: str | None = None                            # model tier override (D10)
    k: int = 5                                          # self-consistency draws


@dataclass
class Baseline:
    ref: str
    label: str
    run: Callable[[BaselineContext], dict]
    uses_llm: bool
    needs_ground_truth: bool = False


# ── B0 — Oracle XBRL (upper bound) ─────────────────────────────────────────────
def _b0_oracle(ctx: BaselineContext) -> dict:
    return {k: ctx.ground_truth.get(k, {}).get("val") for k in KEY_FIELDS}


# ── B1 — Regex, good-faith (same patterns as the system fallback, D4) ──────────
def _b1_regex(ctx: BaselineContext) -> dict:
    from semantic_firewall.evaluation.benchmark_compare import method1_regex
    return method1_regex(ctx.text)


# ── B2 — LLM zero-shot, schema-constrained ─────────────────────────────────────
def _b2_zeroshot(ctx: BaselineContext) -> dict:
    from semantic_firewall.evaluation.benchmark_compare import method2_zeroshot
    return method2_zeroshot(ctx.text)


# ── B3 — B2 + self-consistency (k draws, majority vote per field) ──────────────
def _b3_self_consistency(ctx: BaselineContext) -> dict:
    from semantic_firewall.evaluation.benchmark_compare import method2_zeroshot
    seed_everything()
    draws = [method2_zeroshot(ctx.text) for _ in range(ctx.k)]
    out = {}
    for fld in KEY_FIELDS:
        vals = [str(d.get(fld)) for d in draws if d.get(fld) is not None]
        out[fld] = Counter(vals).most_common(1)[0][0] if vals else None
    return out


# ── B4 — B2 + CoT self-verification (the old M3; E3 measures the bias) ─────────
def _b4_cot(ctx: BaselineContext) -> dict:
    from semantic_firewall.evaluation.benchmark_compare import method3_cot
    return method3_cot(ctx.text)


# ── B5 — B2 + deterministic identity layer, CORRECTOR OFF (detection only) ─────
def _b5_detect_only(ctx: BaselineContext) -> dict:
    """Zero-shot extraction, then DETECT identity violations (no rewriting).

    Returns the raw fields plus a ``_violations`` residual map — this is the
    honest "firewall without corrector" system (B5).
    """
    from semantic_firewall.evaluation.benchmark_compare import method2_zeroshot
    from semantic_firewall.evaluation.detector import identity_residuals

    fields = method2_zeroshot(ctx.text)
    fields["_violations"] = identity_residuals(
        {k: fields.get(k) for k in KEY_FIELDS}, ctx.doc_type
    )
    return fields


# ── B6 — B5 + correction, gains attributable rule-by-rule (E6) ─────────────────
def _b6_correct(ctx: BaselineContext) -> dict:
    from semantic_firewall.evaluation.benchmark_compare import method2_zeroshot
    from semantic_firewall.validation.corrector import apply_corrections

    fields = method2_zeroshot(ctx.text)
    flat = {k: ("" if fields.get(k) is None else str(fields.get(k))) for k in KEY_FIELDS}
    corrected, log = apply_corrections(flat, ctx.doc_type, ctx.text)
    corrected["_corrections"] = log
    return corrected


BASELINES: dict[str, Baseline] = {
    "B0": Baseline("B0", "Oracle XBRL (upper bound)", _b0_oracle, uses_llm=False, needs_ground_truth=True),
    "B1": Baseline("B1", "Regex, good-faith", _b1_regex, uses_llm=False),
    "B2": Baseline("B2", "LLM zero-shot (schema-constrained)", _b2_zeroshot, uses_llm=True),
    "B3": Baseline("B3", "B2 + self-consistency (k=5)", _b3_self_consistency, uses_llm=True),
    "B4": Baseline("B4", "B2 + CoT self-verification", _b4_cot, uses_llm=True),
    "B5": Baseline("B5", "B2 + identities, corrector OFF", _b5_detect_only, uses_llm=True),
    "B6": Baseline("B6", "B5 + correction (rule-attributed)", _b6_correct, uses_llm=True),
}
