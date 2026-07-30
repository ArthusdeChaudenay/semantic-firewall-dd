"""
Deterministic validation layer.

Three strictly separated concerns (D1):
  * detector   — measures accounting-identity violations on the RAW extraction,
                 before any auto-correction. This is the paper's contribution.
  * corrector  — an optional, separately-evaluated second system that rewrites
                 values; measured only on the precision gain it actually adds.
  * anchoring  — textual-anchor hallucination detector (D6/E5).
"""
