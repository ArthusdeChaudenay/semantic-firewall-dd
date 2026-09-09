# A Derived Label Cannot Test the Rule That Derives It

Official implementation and data release for the FinNLP 2026 paper *A Derived Label
Cannot Test the Rule That Derives It* (`paper/main.pdf`), accepted at the 11th Workshop
on Financial Technology and Natural Language Processing, co-located with EMNLP 2026.

The repository contains the extraction system the paper studies, the evaluation
protocol built on regulatory tags, and the audit that shows the system's reported
improvement to be an artifact of how one field was scored.

---

## What the paper reports

We built the system the setting invites: accounting-identity residuals as a
reference-free error detector, a deterministic corrector, and a ladder of six
configurations over two model scales, evaluated on 104 annual reports against reference
values taken from SEC XBRL tags. It reported +10.7 and +13.0 points over zero-shot
extraction at no additional inference cost.

The improvement is not real. EBITDA is not covered by the reporting standard, so its
reference value has to be *computed* — as operating income plus depreciation — which is
the same formula the corrector applies. Accuracy on that field therefore cannot fail.
Rescored without it, the gain is **+0.0 on both model scales**, four of the six
configurations produce **bit-identical values**, and no adjacent pair of configurations
differs significantly.

The paper generalises this into a checkable condition:

> **Method-coupled label.** A field whose reference value is produced by a formula that
> the system under test also applies is method-coupled. Accuracy on such a field cannot
> be falsified by the system's behaviour, and any gain attributed to it is
> uninterpretable.

Everything reported was obtained by rescoring the cached model outputs in this
repository, offline, with no further inference.

---

## Reproducing the paper

Every table and figure can be rebuilt **without network access and without an API key**,
because the model outputs are committed. Only rebuilding the corpus or re-running
extraction needs external access.

```bash
make install-dev            # dependencies + pytest
make test                   # 49 offline unit tests
make audit                  # every number in the paper, from cached outputs
make figures                # all five figures as vector PDF
make paper                  # compile paper/main.pdf
```

Without `make`, each target is a short sequence of module invocations; run
`PYTHONPATH=. python -m scripts.<name>` for any row of the table below.

`make audit` runs, in order:

| Command | Produces |
|---|---|
| `scripts/corpus_reconcile.py` | Corpus arithmetic: 124 − 8 − 12 = 104 (Appendix B) |
| `scripts/reviewer_checks.py` | Coupled-field rescoring and per-rule execution counts (Tables 2, 4) |
| `scripts/coupling_index.py` | Prediction of the inflated gain from the label definition (§5.2) |
| `scripts/coupling_injection.py` | 15 injected couplings per model (§5.3, Figure 2) |
| `scripts/mcnemar_ladder.py` | Discordant pairs and exact McNemar tests (Table 3) |
| `scripts/detector_and_bs.py` | Detector confusion matrices, matched thresholds, tolerance sweep (Tables 5, 6) |
| `scripts/validate_scale.py` | Scale inference validated against observed ratios (§3) |
| `scripts/sector_table.py` | Per-sector accuracy (Figure 4) |
| `scripts/ebitda_coverage.py` | EBITDA coverage in full filing text (§6) — *needs network* |

Results are written under `data/` and echoed to stdout.

### Rebuilding from scratch

```bash
export SEC_USER_AGENT="Your Name (you@example.org)"     # SEC fair-access policy
make corpus                                             # download filings, build ground truth

export OPENAI_BASE_URL=...                              # any OpenAI-compatible endpoint
export OPENAI_API_KEY=...
make extract MODEL=meta/llama-3.1-8b-instruct           # cached; re-runs are free
make extract MODEL=nvidia/llama-3.3-nemotron-super-49b-v1
make audit
```

Extraction is cached per `(document, model, method)`. Re-running only issues calls for
what is missing, and a failed call is never cached: a cached null is indistinguishable
from a model that extracted nothing, and an early run that did cache failures would have
depressed every configuration equally and resembled a model result.

---

## Released artifacts

| Path | Contents |
|---|---|
| `data/corpus.jsonl` | 104 filings: path, ticker, CIK, fiscal year, period end, accession number, inferred reporting scale. Checksummed. |
| `data/ground_truth.jsonl` | Reference values with the `us-gaap` concept, accession number and period behind each, plus a `derived` flag marking coupled labels. |
| `data/corpus/` | Extracted filing text, sliced around the consolidated income statement. |
| `data/extractions/<model>/<method>/` | Every cached model output, one JSON per document. |
| `data/corpus_reconciliation.json` | Per-document drop reasons for every excluded filing. |
| `data/eval_sets.json` | The frozen document set for each analysis, with the extraction passes each requires. |
| `paper/` | Source, figures, bibliography with DOIs, compiled PDF. |

The `derived` flag is the machine-readable form of the paper's central caveat: any field
carrying it has a method-coupled label and must be excluded before a gain on it is
interpreted.

`data/eval_sets.json` exists for a related reason. Each analysis was originally scoped
by intersecting whatever extractions happened to be on disk, and extraction ran over
several sessions, so the population grew between runs and the same script produced
different numbers on different days. The sets are now computed once, committed, and read
by every analysis; re-running reproduces the published numbers exactly. Extending the
corpus means regenerating the file with

```bash
python -m semantic_firewall.evaluation.eval_sets --freeze
```

which is a deliberate act, visible in the diff.

---

## Repository layout

```
semantic_firewall/          the system under study
  config.py                 single source of truth for thresholds, tolerances, seed
  extraction/
    scale.py                reporting-scale inference from the document (never the label)
    llm_extractor.py        text extraction, OpenAI-compatible client
    detect_doc_type.py      document routing
  validation/
    dd_base.py              DDTaxonomy — the deterministic detector, runs on RAW output
    corrector.py            the separate corrector: 8 named rules, fixed-point, ablatable
    anchoring.py            textual-anchor check (no whole-document fallback)
    completeness.py         mandatory-field check
  evaluation/
    xbrl_ground_truth.py    field-to-concept mapping, reference-value construction
    detector.py             continuous identity residuals on the raw extraction
    metrics.py              ROC-AUC, PR-AUC, precision@k (numpy only)
    benchmark_compare.py    the six-configuration ladder
  monitoring/               lexical drift monitor — present, NOT evaluated (see below)
  pipeline.py               orchestrator

scripts/                    corpus construction, extraction driver, audit, figures
tests/                      49 offline unit tests
product/                    NOT part of the artifact: FastAPI service, connectors
paper/                      LaTeX source and figures
```

### The detector runs before the corrector

`pipeline._certify_dd` computes identity residuals on the **raw** extraction and only
then applies corrections, keeping the two logged separately. This ordering is the reason
the detector's score means anything: an earlier version corrected EBITDA with
EBIT + D&A and then scored itself on whether EBITDA = EBIT + D&A held, at the same
tolerance, reporting 100 % coherence by construction.

### What is deliberately not evaluated

`monitoring/` contains a Jensen-Shannon lexical drift monitor with a threshold of 0.55.
**No drift results are reported.** The threshold has never been selected by a sweep, and
the only evaluation available used documents written to contain out-of-corpus
vocabulary, which demonstrates nothing. The code is released; the numbers are not
claimed. See the Limitations section of the paper.

---

## Requirements

Python 3.11+. Pinned in `requirements.txt`; `requirements-dev.txt` adds pytest,
`requirements-product.txt` covers the FastAPI service, which the paper does not use.

`scipy` is optional: Clopper-Pearson intervals fall back to a bisection on the exact
binomial tails when it is absent.

## Notes on data access

Filings are retrieved from SEC EDGAR through official interfaces under the fair-access
policy, which requires a descriptive `SEC_USER_AGENT`. All data are public; no personal,
confidential or proprietary information is used. Synthetic documents remaining under
`samples/` are labelled as synthetic in their headers and none entered the 104-filing
corpus.

## Citation

```bibtex
@inproceedings{derivedlabel2026,
  title     = {A Derived Label Cannot Test the Rule That Derives It},
  booktitle = {Proceedings of the Workshop on Financial Technology and Natural
               Language Processing (FinNLP)},
  year      = {2026},
}
```
