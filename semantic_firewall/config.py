"""
config.py — Single source of truth for every tunable constant.

Fixes D7 (the JSD threshold was hard-coded — sometimes as the string "0.55" — in
eight places and could silently desynchronise). Import the constant from here;
never retype the literal.

All thresholds are documented with their justification status. A value marked
``# TO SWEEP`` has NOT yet been justified by a ROC/AUROC sweep (see E4) and must
be reported as a chosen operating point, not a validated optimum.
"""

from __future__ import annotations

import os
import random

# ── Reproducibility (D11) ────────────────────────────────────────────────────
# One fixed seed for every stochastic step (sampling, shuffling, k-fold, LLM
# self-consistency draws). Override with SF_SEED=<int> for seed-sensitivity runs.
RANDOM_SEED: int = int(os.environ.get("SF_SEED", "42"))


def seed_everything(seed: int | None = None) -> int:
    """Seed all RNGs used across the pipeline. Returns the seed actually applied."""
    s = RANDOM_SEED if seed is None else seed
    random.seed(s)
    try:
        import numpy as np

        np.random.seed(s)
    except Exception:
        pass
    return s


# ── Semantic drift monitor (D7) ───────────────────────────────────────────────
# Operating point for the min-JSD drift alert. NOT yet justified by a sweep;
# E4 replaces this literal with an AUROC-selected threshold on real distribution
# shifts. Until then treat it as a declared operating point.
JSD_ALERT_THRESHOLD: float = 0.55  # TO SWEEP (E4)

# ── Accounting-identity tolerances ─────────────────────────────────────────────
# Relative tolerance for the EBITDA = EBIT + D&A identity (used by BOTH the
# detector and the — now separate — corrector, so they cannot disagree).
EBITDA_REL_TOLERANCE: float = 0.15
# Absolute tolerance (in percentage points) for the EBITDA-margin identity.
MARGIN_ABS_TOLERANCE_PT: float = 1.0
# Balance-sheet / cap-table absolute tolerance (currency units).
BALANCE_ABS_TOLERANCE: float = 1.0
# Price-per-share relative tolerance.
PRICE_PER_SHARE_REL_TOLERANCE: float = 0.01

# ── Text-length limits (D-minor: truncation must be logged, never silent) ──────
# Hard cap applied while preparing text; anything beyond is dropped but the loss
# is now recorded in the certification result (see pipeline._prepare_text).
MAX_DOC_CHARS: int = 400_000
# Cap on the slice actually sent to the LLM (context-window safety).
MAX_LLM_CHARS: int = 200_000

# ── Anchoring hallucination detector (D6 / E5) ─────────────────────────────────
# Window (in lines) around an accounting anchor label in which the extracted
# amount must appear. Swept in E5; the default below is the chosen operating point.
ANCHOR_WINDOW_BEFORE: int = 2
ANCHOR_WINDOW_AFTER: int = 10
