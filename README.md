# Semantic Firewall DD

A **neuro-symbolic firewall** for financial due-diligence extraction. A small LLM
extracts figures from 10-K / balance-sheet / P&L / cap-table documents; a
deterministic layer then checks them against **hard accounting identities** and a
**bilingual semantic-drift monitor**.

## The thesis

> **Accounting identities are free, annotation-less error detectors.**

If `EBITDA ≠ EBIT + D&A`, if `Assets ≠ Liabilities + Equity`, if
`price/share ≠ post-money / shares`, then the LLM's extraction is wrong — and we
know it **without** holding the correct answer. Combined with a second,
independent signal (textual **anchoring**: does the extracted amount appear next to
its accounting label?), this yields an unsupervised error detector whose real
ROC-AUC can be measured against **free XBRL ground truth**.

## Layout

```
semantic_firewall/            ← research artifact (submission scope)
  config.py                   single source of truth: JSD threshold, seed, tolerances
  extraction/                 text extraction, doc-type detection, statement parsing
  validation/
    dd_base.py                DDTaxonomy — the deterministic DETECTOR (runs on RAW)
    corrector.py              the SEPARATE, optional corrector (idempotent, ablatable)
    anchoring.py              textual-anchor hallucination detector (wired in)
    completeness.py           mandatory-field check
  monitoring/semantic_monitor.py   bilingual TF-IDF + Jensen-Shannon drift monitor
  evaluation/
    xbrl_ground_truth.py      E0 — SEC companyfacts → ground truth
    detector.py               E1 — identity-violation scoring
    metrics.py                ROC-AUC / PR-AUC / P@k / calibration (numpy)
    baselines.py              E2 — B0..B6 ladder
    run_experiment.py         CLI for E0–E3
    dd_eval.py / benchmark_compare.py / evaluate.py / run_all_eval.py
  pipeline.py                 certify_document / certify_dossier orchestrator
product/                      NOT part of the artifact: FastAPI, connectors, HTML/PDF
scripts/                      data-prep (SEC download, corpus build, htm→pdf)
tests/                        unit tests (offline) + integration tests (network)
docs/                         AUDIT_RESPONSE.md, EXPERIMENTS.md
```

**Detector vs corrector (the core design rule).** The detector always runs on the
**raw** LLM output, *before* any correction — so `score_confiance` and the reported
violations are earned, not tautological. The corrector is a second system, its
effect reported separately (`champs_corriges`, `corrections_appliquees`,
`controles_post_correction`). See [docs/AUDIT_RESPONSE.md](docs/AUDIT_RESPONSE.md).

## Install

```bash
python -m venv venv && source venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt          # core research deps
pip install -r requirements-product.txt  # optional: run the API
pip install -r requirements-dev.txt      # optional: tests
cp .env.example .env                      # set your LLM endpoint + key
```

## Quick start

```python
from semantic_firewall.pipeline import certify_document
cert = certify_document("samples/dd/compte_resultat_exemple.txt")
print(cert["statut"], cert["score_confiance"])
print(cert["controles"])                 # detector on RAW extraction
print(cert["corrections_appliquees"])    # what the corrector changed (separately)
```

Run the bundled sample suite: `python -m semantic_firewall.pipeline`.

## Tests

```bash
make test           # fast, offline: parser, corrector idempotence/order, metrics
```
19 unit tests cover `DDTaxonomy._f` (accounting negatives, FR/EN decimals,
multipliers, NBSP), corrector idempotence + order-insensitivity + loss-making firms,
and the numpy metrics. Integration tests (`tests/test_parsing.py`, `test_sec.py`,
`test_batch_api.py`) hit the LLM/SEC and are excluded from the default run.

## Experiments (E0–E7)

The evaluation is only honest with external ground truth. See
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md). The unblock is **E0** (XBRL ground truth):

```bash
export SEC_USER_AGENT="Your Name (you@example.com)"
make ground-truth MANIFEST=data/cik_fy.csv        # E0
make e1 CORPUS=data/corpus.jsonl GT=data/ground_truth.jsonl   # detector ROC-AUC
make e2 CORPUS=... GT=...                          # B0..B6 ladder (≥2 model tiers)
make e3 CORPUS=... GT=...                          # CoT vs identity layer
```

> These require SEC data + LLM access and are what *produce* the paper's numbers.
> No results are committed or fabricated.

## Ground truth (D2)

The benchmark is scored on **correctness against real SEC XBRL ground truth**, not
the old non-null "recall". Build/refresh it with:

```bash
export SEC_USER_AGENT="Your Name (you@example.com)"
python -m semantic_firewall.evaluation.benchmark_ground_truth --full
```

This resolves each company (ticker→CIK) and pulls the tagged `us-gaap` values from
SEC `companyfacts`, keyed by **period-end date** (not the misleading XBRL `fy` tag,
which returns comparative columns). The committed manifest
`data/benchmark_xbrl_ground_truth.json` (46 companies, checksummed) lets
`benchmark_compare` report `field_accuracy_pct` — the share of fields correct within
±1 % of the SEC value — while `coverage_pct` keeps the old non-null rate, clearly
labelled as coverage. With no ground truth, accuracy is `None`, never fabricated.

## Reproducibility (D11)

Fixed seed (`SF_SEED`, default 42, via `config.seed_everything`), pinned
`requirements*.txt`, `Makefile` targets per table, and a frozen, checksummable XBRL
ground-truth manifest (`xbrl_ground_truth.build_manifest`). Large corpora
(`samples/real_world/`) and `output/` stay out of git; commit the `cik_fy.csv`
manifest + a fetch script instead.

## Provenance & scope notes

- `samples/fnspid/*.txt` are **synthetic** unit-test fixtures (headers say so); they
  are not from the FNSPID dataset.
- No FR/EN comparison conclusion should be drawn until length/register are matched
  on real filings (audit D8 / experiment E9).
- Verify `samples/references/bilan/bilan_devoteam_2022.txt` against the published
  annual report before distributing any open artifact.

See [docs/AUDIT_RESPONSE.md](docs/AUDIT_RESPONSE.md) for the full defect-by-defect
status (D1–D12, E0–E7).
