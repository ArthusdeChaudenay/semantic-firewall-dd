"""
semantic_firewall — Neuro-symbolic firewall for financial due-diligence extraction.

Research artifact (submission scope): extraction, validation, monitoring, evaluation.
Product code (API, connectors, HTML/PDF report generators) lives in the separate
top-level ``product/`` package and is NOT part of the research contribution.

Sub-packages
------------
extraction   text extraction, document-type detection, statement parsing
validation   deterministic accounting-identity detector + (separate) corrector + anchoring
monitoring   bilingual TF-IDF / Jensen-Shannon semantic drift monitor
evaluation   benchmark harness, XBRL ground truth (E0), metrics, experiment runners
"""

__version__ = "1.0.0"
