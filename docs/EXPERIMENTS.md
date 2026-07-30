# Experiments — how to run E0–E7

> **Honesty contract.** These scripts do not fabricate results. They require the
> XBRL ground truth (E0) and, for E1–E3, LLM access. Missing inputs produce an
> explicit message, never an invented number. Run them to *earn* the paper's tables.

## Prerequisites

```bash
pip install -r requirements.txt
export SF_SEED=42                                  # reproducibility
export SEC_USER_AGENT="Your Name (you@example.com)"  # SEC requires this
# LLM access (for E1–E3): set the client env vars read by extraction/llm_extractor.py
#   (see .env.example — NIM/OpenAI-compatible endpoint + key)
```

Two small data contracts:

- `data/cik_fy.csv` — rows `cik,fy` (e.g. `320193,2023` for Apple FY2023).
- `data/corpus.jsonl` — one JSON per line:
  `{"path": "samples/.../10k.txt", "cik": 320193, "fy": 2023, "doc_type": "compte_resultat"}`

## Palier 0 — the unblock

### E0 — XBRL ground truth
```bash
make ground-truth MANIFEST=data/cik_fy.csv GT=data/ground_truth.jsonl
# or:
python -m semantic_firewall.evaluation.run_experiment e0 --manifest data/cik_fy.csv --out data/ground_truth.jsonl
```
Produces a frozen, checksummable JSONL: each line records CIK, accession, and the
exact `us-gaap` concept per field. EBITDA is *derived* (`OperatingIncomeLoss +
DepreciationDepletionAndAmortization`) — which is exactly why the arithmetic-identity
detector is testable against it. Aim for 500–2000 filings.

### E1 — the detector alone (the paper)
```bash
make e1 CORPUS=data/corpus.jsonl GT=data/ground_truth.jsonl
```
Freezes the raw extraction, computes identity-violation magnitude
(`evaluation/detector.py`), and reports **ROC-AUC, PR-AUC, precision@k** against the
"value is wrong vs XBRL" label (`evaluation/metrics.py`). Add `score_confiance`
calibration (ECE) with `metrics.expected_calibration_error`.

### E2 — baseline ladder
```bash
make e2 CORPUS=... GT=... 
# B0 Oracle · B1 regex (same patterns, D4) · B2 zero-shot · B3 self-consistency
# B4 CoT · B5 identities/corrector-OFF · B6 +correction. Run at ≥2 model tiers (D10):
python -m semantic_firewall.evaluation.run_experiment e2 --corpus ... --ground-truth ... --model <frontier-id>
```

### E3 — does CoT self-verification fix arithmetic?
```bash
make e3 CORPUS=... GT=...
```
Emits the confusion matrix "CoT changed the value" × "the value became correct".
A clean negative result is publishable and motivates the deterministic layer.

## Palier 1

- **E4 — real drift + JSD threshold sweep.** Delete the synthetic drift studies
  (done). Build two real corpora: diachronic (10-K Item 7/8, 2015–2019 vs 2020–2021)
  and synchronic (hold out an SIC sector). Sweep `config.JSD_ALERT_THRESHOLD`
  (marked `# TO SWEEP`) and publish an AUROC vs baselines (TF-IDF cosine, LM
  perplexity, sentence-embedding MMD).
- **E5 — anchoring detector.** Already wired (D6). Score it like E1 against XBRL and
  sweep `ANCHOR_WINDOW_BEFORE/AFTER` (±0/2/5/10). Report `identity ∨ anchoring`.
- **E6 — correction ablation.** `apply_corrections(disabled_rules={...})`; every rule
  is named in `INCOME_STATEMENT_RULES`. Drop one at a time, measure vs E0. The first
  suspect for a *harmful* rule is `ebit_below_da_big_gap`.
- **E7 — sector-stratified error analysis.** Cross the sector taxonomy (in
  `benchmark_compare` doc list) with E0 labels: accuracy × sector × field.

## Palier 2
E8 robustness (OCR/table-flatten noise), E9 real French filings (BALO / Euronext
IFRS), E10 cost/latency Pareto (`time_s` already logged; the sleep contamination is
removed).
