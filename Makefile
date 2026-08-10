# Reproduction entry points for "When the Label Is the Method".
#
# The audit targets are offline: the model outputs are committed under
# data/extractions/, so every number in the paper rebuilds without an API key and
# without network access. Only `corpus` and `extract` reach outside.
#
# Windows: run in Git Bash. Override the interpreter with:  make test PY=python

PY ?= python
export PYTHONPATH := .
export SF_SEED ?= 42

MODEL   ?= meta/llama-3.1-8b-instruct
WORKERS ?= 3
YEARS   ?= 2
LIMIT   ?= 62

.PHONY: help install install-dev test compile corpus extract audit figures paper clean

help:
	@echo "install / install-dev   dependencies (add pytest with -dev)"
	@echo "test                    49 offline unit tests"
	@echo "audit                   every number in the paper, from cached outputs"
	@echo "figures                 all five figures as vector PDF"
	@echo "paper                   compile paper/main.pdf"
	@echo ""
	@echo "corpus                  rebuild from SEC EDGAR   (needs SEC_USER_AGENT)"
	@echo "extract MODEL=...       run extraction           (needs OPENAI_API_KEY)"

install:
	$(PY) -m pip install -r requirements.txt

install-dev:
	$(PY) -m pip install -r requirements-dev.txt

test:
	$(PY) -m pytest -q

compile:
	$(PY) -m compileall -q semantic_firewall scripts

# ── Rebuilding from source data (external access required) ────────────────────

corpus:
	@test -n "$$SEC_USER_AGENT" || (echo "set SEC_USER_AGENT='Name (you@example.org)'"; exit 1)
	$(PY) -m scripts.build_benchmark_corpus --years $(YEARS) --limit $(LIMIT)

extract:
	@test -n "$$OPENAI_API_KEY" || (echo "set OPENAI_API_KEY (and OPENAI_BASE_URL)"; exit 1)
	$(PY) -m scripts.run_paper_experiments extract --model $(MODEL) --workers $(WORKERS)

# ── The audit: offline, reads only the committed extractions ──────────────────

audit:
	@echo "== corpus reconciliation (Appendix B) =="
	$(PY) -m scripts.corpus_reconcile
	@echo "\n== coupled-field rescoring and per-rule execution counts (Tables 2, 4) =="
	$(PY) -m scripts.reviewer_checks
	@echo "\n== predicting the inflated gain from the label definition (Sec. 5.2) =="
	$(PY) -m scripts.coupling_index
	@echo "\n== injected couplings (Sec. 5.3) =="
	$(PY) -m scripts.coupling_injection
	@echo "\n== discordant pairs and exact McNemar tests (Table 3) =="
	$(PY) -m scripts.mcnemar_ladder
	@echo "\n== detector, coupled identity (Tables 5, 6) =="
	$(PY) -m scripts.detector_and_bs --bs-method BS
	@echo "\n== detector, uncoupled identity (Sec. 5.6) =="
	$(PY) -m scripts.detector_and_bs --bs-method BSEN
	@echo "\n== scale inference validation (Sec. 3) =="
	$(PY) scripts/validate_scale.py
	@echo "\n== per-sector accuracy (Figure 4) =="
	$(PY) -m scripts.sector_table

# Needs network: re-reads the complete filing text rather than the cached slice.
ebitda-coverage:
	$(PY) -m scripts.ebitda_coverage

figures:
	$(PY) -m scripts.make_figures
	$(PY) -m scripts.coupling_injection

paper:
	cd paper && pdflatex -interaction=nonstopmode main.tex >/dev/null \
	  && bibtex main >/dev/null \
	  && pdflatex -interaction=nonstopmode main.tex >/dev/null \
	  && pdflatex -interaction=nonstopmode main.tex >/dev/null \
	  && rm -f main.aux main.log main.out main.blg \
	  && echo "paper/main.pdf"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f paper/main.aux paper/main.log paper/main.out paper/main.blg
