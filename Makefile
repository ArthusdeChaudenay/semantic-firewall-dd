# Semantic Firewall — reproducible entry points (D11).
# Windows: run these in Git Bash, or copy the command bodies into PowerShell.
# Override the interpreter with:  make test PY=python

PY ?= venv/Scripts/python.exe
export PYTHONPATH := .
export SF_SEED ?= 42

.PHONY: help install install-dev test lint compile ground-truth e1 e2 e3 clean

help:
	@echo "install       install core research deps (requirements.txt)"
	@echo "install-dev   + pytest"
	@echo "test          run fast offline unit tests"
	@echo "compile       byte-compile the whole package"
	@echo "ground-truth  E0: build XBRL ground truth   (needs SEC_USER_AGENT, MANIFEST=...)"
	@echo "e1 / e2 / e3  run experiments               (needs CORPUS=... GT=... API key)"

install:
	$(PY) -m pip install -r requirements.txt

install-dev:
	$(PY) -m pip install -r requirements-dev.txt

test:
	$(PY) -m pytest

compile:
	$(PY) -m py_compile $$(git ls-files 'semantic_firewall/*.py')

# ── Experiments ────────────────────────────────────────────────────────────
MANIFEST ?= data/cik_fy.csv
GT        ?= data/ground_truth.jsonl
CORPUS    ?= data/corpus.jsonl

ground-truth:
	$(PY) -m semantic_firewall.evaluation.run_experiment e0 --manifest $(MANIFEST) --out $(GT)

e1:
	$(PY) -m semantic_firewall.evaluation.run_experiment e1 --corpus $(CORPUS) --ground-truth $(GT)

e2:
	$(PY) -m semantic_firewall.evaluation.run_experiment e2 --corpus $(CORPUS) --ground-truth $(GT) --baselines B0,B1,B2,B4,B5,B6

e3:
	$(PY) -m semantic_firewall.evaluation.run_experiment e3 --corpus $(CORPUS) --ground-truth $(GT)

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
