"""run_paper_experiments.py — produce every number in the paper, reproducibly.

Architecture, and why it is split this way: LLM calls are expensive and
non-deterministic to schedule, metrics are cheap and must be re-derivable. So the
driver has two phases.

  Phase 1 (``extract``)  one call per (document, model, method), cached to
                         ``data/extractions/<model>/<method>/<stem>.json``. Re-runs
                         are free and offline. Nothing is ever recomputed silently.
  Phase 2 (``report``)   reads the cache and derives E1/E2/E3/E5/E6 plus the
                         per-sector breakdown. No LLM access needed, so every table
                         in the paper can be rebuilt from the committed cache.

Methods. B5/B6 issue no new calls: the firewall does not re-extract, it validates and
corrects an existing extraction. That is a property of the design, not an
optimisation — and it is why the firewall's marginal cost over B2 is ~0.

    B1  regex, good faith (deterministic, no LLM)
    B2  zero-shot JSON extraction                      1 call
    B3  B2 + self-consistency, k=5, median vote        5 calls
    B4  B2 + chain-of-thought self-verification        2 calls
    B5  B2 + deterministic detector (no correction)    0 extra
    B6  B5 + corrector                                 0 extra

Run:
    export OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
    export OPENAI_API_KEY=$NVIDIA_API_KEY
    python -m scripts.run_paper_experiments extract --model meta/llama-3.1-8b-instruct
    python -m scripts.run_paper_experiments report
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from semantic_firewall.config import seed_everything
from semantic_firewall.extraction.scale import classify_error, infer_document_scale, to_absolute

CORPUS = Path("data/corpus.jsonl")
GT = Path("data/ground_truth.jsonl")
EXTRACT_DIR = Path("data/extractions")
RESULTS = Path("data/paper_results.json")

KEY_FIELDS = ["chiffre_affaires", "ebit", "ebitda",
              "dotations_amortissements", "resultat_net"]
BS_FIELDS = ["actif_total", "passif_total", "capitaux_propres"]
BSEN_FIELDS = ["total_assets", "total_liabilities", "total_equity",
               "total_liabilities_and_equity"]

MAX_CHARS = 60_000
REL_TOL = 0.01
SC_K = 5  # self-consistency draws for B3


# ══════════════════════════════════════════════════════════════════════════════
# Prompts. B2's wording is held fixed across every method and model so that
# differences in the ladder are attributable to the scaffolding, not the prompt.
# ══════════════════════════════════════════════════════════════════════════════

_P_ZEROSHOT = """You are reading the consolidated income statement of a US 10-K filing.
Extract these 5 metrics for the MOST RECENT fiscal year shown.

Return ONLY a JSON object with exactly these keys (use null when absent):
  chiffre_affaires            total revenue / net sales / total net revenues
  ebit                        operating income / income from operations
  ebitda                      EBITDA if stated, else null
  dotations_amortissements    depreciation and amortization (D&A)
  resultat_net                net income

Rules:
- Copy each number EXACTLY as printed in the table. Do NOT rescale or convert units.
- Consolidated group totals only, never a single segment.
- Most recent fiscal year column only, never a comparative year.
- No markdown, no commentary. JSON only.

Document:
{text}
"""

# Balance-sheet pass. Its purpose is to give the identity detector a case where it
# CAN fire: on the income statement the model correctly returns null for EBITDA (not a
# GAAP line), so EBITDA = EBIT + D&A is never computable and the detector has zero
# coverage. Assets = Liabilities + Equity, by contrast, is printed in every filing, so
# a misread breaks an identity the detector can actually see.
_P_BALANCE = """You are reading the consolidated BALANCE SHEET of a US 10-K filing.
Extract these 3 metrics for the MOST RECENT balance-sheet date shown.

Return ONLY a JSON object with exactly these keys (use null when absent):
  actif_total         total assets
  passif_total        total liabilities and stockholders' equity
  capitaux_propres    total stockholders' equity

Rules:
- Copy each number EXACTLY as printed in the table. Do NOT rescale or convert units.
- Consolidated group totals only.
- Most recent balance-sheet date column only, never the prior-year column.
- No markdown, no commentary. JSON only.

Document:
{text}
"""

# Uncoupled balance-sheet pass (English schema keys, four fields).
# Two defects in the earlier balance-sheet pass motivate this one. (a) Its keys were
# French on English filings, and the 49B model followed the key name over its English
# description, reading the liabilities field as liabilities-excluding-equity. (b) It
# had only Assets and LiabilitiesAndStockholdersEquity, which XBRL defines to be
# EQUAL, so an identity over them cannot be violated by a correct reading and a
# corrector filling one from the other is scored right whenever the other is right --
# the same label/method coupling that voided the EBITDA result. Adding total
# liabilities makes Assets = Liabilities + Equity an identity over three
# independently tagged concepts, none derived from the others.
_P_BALANCE_EN = """You are reading the consolidated BALANCE SHEET of a US 10-K filing.
Extract these 4 metrics for the MOST RECENT balance-sheet date shown.

Return ONLY a JSON object with exactly these keys (use null when absent):
  total_assets        Total assets
  total_liabilities   Total liabilities (EXCLUDING stockholders equity)
  total_equity        Total stockholders equity
  total_liabilities_and_equity   Total liabilities and stockholders equity

Rules:
- Copy each number EXACTLY as printed in the table. Do NOT rescale or convert units.
- Consolidated group totals only.
- Most recent balance-sheet date column only, never the prior-year column.
- No markdown, no commentary. JSON only.

Document:
{text}
"""

_P_COT_VERIFY = """You extracted these figures from an income statement:
{json_data}

Check ONE rule: EBITDA = ebit + dotations_amortissements.
- If it holds within 5%, return the JSON unchanged.
- If ebitda is null and both ebit and dotations_amortissements are present, set
  ebitda = ebit + dotations_amortissements.
- If it is violated, recompute ebitda = ebit + dotations_amortissements.
- Change NOTHING else. Do not change units.

Return ONLY the corrected JSON.
"""


def _client():
    from openai import OpenAI
    return OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY") or os.environ["NVIDIA_API_KEY"],
        timeout=180.0,
    )


_FENCE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)


def _parse_json(raw: str) -> dict | None:
    s = _FENCE.sub("", raw or "").strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _call(cli, model: str, prompt: str, temperature: float = 0.0,
          max_tokens: int = 400, retries: int = 6) -> tuple[dict | None, float, int]:
    """One chat completion. Returns (parsed_json, seconds, completion_tokens).

    Rate limiting needs a much longer backoff than ordinary transient errors: the
    hosted endpoint enforces a per-minute quota, so a 2 s retry is still inside the
    same window and simply burns another rejection. A first pass with an aggressive
    worker count silently cached 48/104 documents as all-null this way, which would
    have depressed every baseline equally and looked like a genuine model result.
    """
    last = None
    for attempt in range(retries):
        t0 = time.time()
        try:
            r = cli.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            dt = time.time() - t0
            txt = r.choices[0].message.content or ""
            tok = getattr(getattr(r, "usage", None), "completion_tokens", 0) or 0
            return _parse_json(txt), dt, tok
        except Exception as e:
            last = e
            msg = str(e).lower()
            if "429" in msg or "too many requests" in msg or "rate" in msg:
                time.sleep(min(60, 10 * (attempt + 1)))   # clear the quota window
            else:
                time.sleep(2 * (attempt + 1))
    print(f"    ! call failed after {retries} tries: {str(last)[:110]}")
    return None, 0.0, 0


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — extraction (cached)
# ══════════════════════════════════════════════════════════════════════════════

def _slot(model: str, method: str, stem: str) -> Path:
    safe = model.replace("/", "__")
    return EXTRACT_DIR / safe / method / f"{stem}.json"


def _median_vote(draws: list[dict]) -> dict:
    """Field-wise median across self-consistency draws (B3).

    Median, not majority: these are continuous quantities, so a majority vote over
    exact strings would almost always be a 5-way tie and collapse to the first draw.
    """
    out: dict = {}
    for f in KEY_FIELDS:
        vals = []
        for d in draws:
            v = (d or {}).get(f)
            try:
                if v is not None:
                    vals.append(float(str(v).replace(",", "")))
            except ValueError:
                pass
        out[f] = statistics.median(vals) if vals else None
    return out


def extract_one(cli, model: str, doc: dict, methods: set[str] | None = None) -> dict:
    stem = f"{doc['ticker']}_FY{doc['fy']}"
    text = Path(doc["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
    prompt = _P_ZEROSHOT.format(text=text)
    log: dict = {"stem": stem, "calls": 0, "seconds": 0.0, "tokens": 0}
    want = methods or {"B2", "B3", "B4", "BS"}

    # ── B2 : zero-shot ────────────────────────────────────────────────────────
    p2 = _slot(model, "B2", stem)
    if p2.exists():
        b2 = json.loads(p2.read_text(encoding="utf-8"))
    else:
        fields, dt, tok = _call(cli, model, prompt)
        log["calls"] += 1; log["seconds"] += dt; log["tokens"] += tok
        if fields is None:
            # Never cache a failed call. A cached null is indistinguishable from a
            # model that genuinely extracted nothing, and would silently depress
            # every downstream metric. Report the gap and let a re-run retry it.
            log["failed"] = True
            return log
        b2 = {"fields": fields, "seconds": round(dt, 2), "tokens": tok, "parsed": True}
        p2.parent.mkdir(parents=True, exist_ok=True)
        p2.write_text(json.dumps(b2, indent=2), encoding="utf-8")

    # ── B4 : CoT self-verification on top of a fresh draw ─────────────────────
    p4 = _slot(model, "B4", stem)
    if "B4" in want and not p4.exists():
        base = b2["fields"]
        ver, dt, tok = _call(cli, model,
                             _P_COT_VERIFY.format(json_data=json.dumps(base, indent=2)))
        # Anti-degradation guard, as in the original system: never accept a
        # verification pass that drops fields.
        n_before = sum(1 for k in KEY_FIELDS if base.get(k) is not None)
        n_after = sum(1 for k in KEY_FIELDS if (ver or {}).get(k) is not None)
        if ver is None and dt == 0.0:
            log["failed_b4"] = True      # do not cache; retry on the next pass
            return log
        chosen = ver if (ver is not None and n_after >= n_before) else base
        p4.parent.mkdir(parents=True, exist_ok=True)
        p4.write_text(json.dumps({"fields": {k: chosen.get(k) for k in KEY_FIELDS},
                                  "seconds": round(dt, 2), "tokens": tok,
                                  "accepted_verification": chosen is ver},
                                 indent=2), encoding="utf-8")
        log["calls"] += 1; log["seconds"] += dt; log["tokens"] += tok

    # ── BS : balance-sheet pass (feeds the detector arm that has coverage) ────
    pbs = _slot(model, "BS", stem)
    if "BS" in want and not pbs.exists():
        f, dt, tok = _call(cli, model, _P_BALANCE.format(text=text), max_tokens=300)
        log["calls"] += 1; log["seconds"] += dt; log["tokens"] += tok
        if f is None:
            log["failed_bs"] = True
            return log
        pbs.parent.mkdir(parents=True, exist_ok=True)
        pbs.write_text(json.dumps({"fields": {k: f.get(k) for k in BS_FIELDS},
                                   "seconds": round(dt, 2), "tokens": tok},
                                  indent=2), encoding="utf-8")

    # ── BSEN : uncoupled balance-sheet pass, English keys, 4 fields ───────────
    pben = _slot(model, "BSEN", stem)
    if "BSEN" in want and not pben.exists():
        f, dt, tok = _call(cli, model, _P_BALANCE_EN.format(text=text), max_tokens=320)
        log["calls"] += 1; log["seconds"] += dt; log["tokens"] += tok
        if f is None:
            log["failed_bsen"] = True
            return log
        pben.parent.mkdir(parents=True, exist_ok=True)
        pben.write_text(json.dumps({"fields": {k: f.get(k) for k in BSEN_FIELDS},
                                    "seconds": round(dt, 2), "tokens": tok},
                                   indent=2), encoding="utf-8")

    # ── B3 : self-consistency, k draws at temperature 0.7 ─────────────────────
    # Costs SC_K calls per document, i.e. ~70 % of the whole extraction budget. It is
    # therefore run on the small tier only; the corpus is held identical across tiers,
    # which matters more for the comparison than having B3 on both.
    p3 = _slot(model, "B3", stem)
    if "B3" in want and not p3.exists():
        draws, secs, toks = [], 0.0, 0
        for _ in range(SC_K):
            f, dt, tok = _call(cli, model, prompt, temperature=0.7)
            draws.append(f or {}); secs += dt; toks += tok
        p3.parent.mkdir(parents=True, exist_ok=True)
        p3.write_text(json.dumps({"fields": _median_vote(draws), "k": SC_K,
                                  "seconds": round(secs, 2), "tokens": toks},
                                 indent=2), encoding="utf-8")
        log["calls"] += SC_K; log["seconds"] += secs; log["tokens"] += toks

    return log


def cmd_extract(args) -> None:
    seed_everything()
    corpus = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        corpus = corpus[:args.limit]
    cli = _client()
    print(f"  model   : {args.model}")
    print(f"  corpus  : {len(corpus)} filings")
    print(f"  workers : {args.workers}\n")

    t0, done, calls, tokens = time.time(), 0, 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        want = set(args.methods.split(",")) if args.methods else None
        futs = {ex.submit(extract_one, cli, args.model, d, want): d for d in corpus}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                log = fut.result()
            except Exception as e:
                print(f"  ! {d['ticker']}_FY{d['fy']}: {str(e)[:100]}")
                continue
            done += 1; calls += log["calls"]; tokens += log["tokens"]
            if done % 10 == 0 or log["calls"]:
                print(f"  [{done}/{len(corpus)}] {log['stem']:<14} "
                      f"+{log['calls']} calls  {log['seconds']:.1f}s")
    print(f"\n  done in {time.time()-t0:.0f}s — {calls} LLM calls, {tokens:,} completion tokens")


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — metrics (offline, from cache)
# ══════════════════════════════════════════════════════════════════════════════

def _load_inputs():
    corpus = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    gt = {}
    for l in GT.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            gt[(r["cik"], r["fy"])] = r
    return corpus, gt


def _score_fields(fields: dict, gt_fields: dict, scale: dict,
                  allowed: list[str] | None = None) -> dict:
    """Per-field correctness against XBRL, restricted to ``allowed`` field names."""
    from semantic_firewall.validation.dd_base import DDTaxonomy
    keep = allowed if allowed is not None else list(gt_fields)
    out = {}
    for f, meta in gt_fields.items():
        if f not in keep:
            continue
        truth = float(meta["val"])
        raw = DDTaxonomy._f(fields.get(f))
        got = to_absolute(raw, scale)
        tol = max(abs(truth) * REL_TOL, 1.0)
        out[f] = {"raw": raw, "abs": got, "truth": truth,
                  "ok": abs(got - truth) <= tol,
                  "kind": classify_error(got, truth, REL_TOL)}
    return out


def _score(fields: dict, gt_fields: dict, scale: dict) -> dict:
    """Per-field correctness of one extraction, with the document's own scale."""
    from semantic_firewall.validation.dd_base import DDTaxonomy
    out = {}
    for f, meta in gt_fields.items():
        if f not in KEY_FIELDS:
            continue
        truth = float(meta["val"])
        raw = DDTaxonomy._f(fields.get(f))
        got = to_absolute(raw, scale)
        tol = max(abs(truth) * REL_TOL, 1.0)
        out[f] = {"raw": raw, "abs": got, "truth": truth,
                  "ok": abs(got - truth) <= tol,
                  "kind": classify_error(got, truth, REL_TOL)}
    return out


def _variants(fields: dict, text: str) -> dict:
    """Derive B5 and B6 from a B2 extraction — no additional LLM calls.

    B5 = B2 + the deterministic detector (values untouched; the detector only judges).
    B6 = B5 + the corrector (values may change).
    """
    from semantic_firewall.validation.corrector import apply_corrections
    flat = {k: ("" if fields.get(k) is None else str(fields[k])) for k in KEY_FIELDS}
    corrected, log = apply_corrections(flat, "compte_resultat", text,
                                       use_regex_backfill=True)
    return {"B5": dict(flat), "B6": corrected, "corrections": log}


def cmd_report(args) -> None:
    from semantic_firewall.evaluation import metrics as M
    from semantic_firewall.evaluation.benchmark_compare import method1_regex
    from semantic_firewall.evaluation.detector import document_anomaly_score, identity_residuals
    from semantic_firewall.validation.anchoring import check_transcription_divergence
    from semantic_firewall.validation.corrector import INCOME_STATEMENT_RULES, apply_corrections

    corpus, gt_all = _load_inputs()
    models = args.models.split(",")

    # Paired evaluation. Model tiers must be compared on the SAME documents: two
    # tiers scored on two different subsets would confound tier with sector, and our
    # partial extractions are strongly sector-skewed (the corpus is ordered by
    # sector). Restricting to the intersection keeps the comparison valid; the
    # resulting composition is reported so the reduced coverage is explicit.
    if args.paired and len(models) > 1:
        avail = None
        for m in models:
            safe_m = m.replace("/", "__")
            have = {p.stem for p in (EXTRACT_DIR / safe_m / "B2").glob("*.json")}
            avail = have if avail is None else (avail & have)
        before = len(corpus)
        corpus = [d for d in corpus if f"{d['ticker']}_FY{d['fy']}" in (avail or set())]
        import collections as _c
        out_note = {"paired": True, "n_paired": len(corpus), "n_full": before,
                    "sectors": dict(sorted(_c.Counter(d["sector"] for d in corpus).items()))}
        print(f"  paired subset: {len(corpus)}/{before} filings common to "
              f"{len(models)} models")
        print(f"  composition: {out_note['sectors']}")
    else:
        out_note = {"paired": False, "n_paired": len(corpus), "n_full": len(corpus)}
    out: dict = {"config": {"models": models, "rel_tol": REL_TOL, "sc_k": SC_K,
                            "max_chars": MAX_CHARS, "n_filings": len(corpus)},
                 "pairing": out_note,
                 "ladder": {}, "detector": {}, "cot": {}, "anchoring": {},
                 "ablation": {}, "sector": {}, "cost": {}, "coverage": {}}

    # ── field-level GT coverage (D15) ─────────────────────────────────────────
    cov: dict = {}
    for d in corpus:
        for f, m in gt_all[(d["cik"], d["fy"])]["fields"].items():
            if f in KEY_FIELDS:
                e = cov.setdefault(f, {"n": 0, "derived": 0})
                e["n"] += 1; e["derived"] += int(bool(m.get("derived")))
    out["coverage"] = {"n_filings": len(corpus), "per_field": cov}

    for model in models:
        safe = model.replace("/", "__")
        ladder: dict = {b: {"correct": 0, "scored": 0, "kinds": {}, "docs": 0}
                        for b in ("B1", "B2", "B3", "B4", "B5", "B6")}
        det_scores, det_labels = [], []
        det_scores_pf, det_labels_pf = [], []
        # An accounting identity can only fire when the model actually emitted the
        # terms it relates. How often that happens is the ceiling on the detector's
        # recall, and must be reported alongside its AUROC.
        n_computable = {"any": 0, "ebitda": 0, "balance": 0}
        bs_scores, bs_labels = [], []
        cot = {"better": 0, "worse": 0, "neutral": 0, "unchanged": 0}
        anchor = {w: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for w in (0, 2, 5, 10)}
        abl = {name: {"correct": 0, "scored": 0} for name, _ in INCOME_STATEMENT_RULES}
        # Leave-one-out cannot see a redundant rule SET: with eight overlapping rules
        # and fixed-point iteration, disabling one lets another fire and the delta is
        # zero even when the whole block is doing the work. So the two components of
        # the corrector are also ablated as blocks.
        for arm in ("__none_disabled__", "__no_backfill__",
                    "__no_arithmetic__", "__neither__"):
            abl[arm] = {"correct": 0, "scored": 0}
        sector: dict = {}
        cost = {"calls": 0, "seconds": 0.0, "tokens": 0}
        n_missing = 0

        for d in corpus:
            stem = f"{d['ticker']}_FY{d['fy']}"
            gt_rec = gt_all[(d["cik"], d["fy"])]
            gtf = {k: v for k, v in gt_rec["fields"].items() if k in KEY_FIELDS}
            if not gtf:
                continue
            text = Path(d["path"]).read_text(encoding="utf-8")[:MAX_CHARS]
            scale = infer_document_scale(text)

            p2 = EXTRACT_DIR / safe / "B2" / f"{stem}.json"
            if not p2.exists():
                n_missing += 1
                continue
            b2 = json.loads(p2.read_text(encoding="utf-8"))
            cost["calls"] += 1
            cost["seconds"] += b2.get("seconds", 0.0)
            cost["tokens"] += b2.get("tokens", 0)

            cand: dict = {"B1": method1_regex(text), "B2": b2["fields"]}
            for meth in ("B3", "B4"):
                p = EXTRACT_DIR / safe / meth / f"{stem}.json"
                if p.exists():
                    j = json.loads(p.read_text(encoding="utf-8"))
                    cand[meth] = j["fields"]
                    cost["calls"] += j.get("k", 1)
                    cost["seconds"] += j.get("seconds", 0.0)
                    cost["tokens"] += j.get("tokens", 0)
            var = _variants(b2["fields"], text)
            cand["B5"], cand["B6"] = var["B5"], var["B6"]

            for b, fields in cand.items():
                sc = _score(fields, gtf, scale)
                ladder[b]["docs"] += 1
                ladder[b]["scored"] += len(sc)
                ladder[b]["correct"] += sum(1 for v in sc.values() if v["ok"])
                for v in sc.values():
                    ladder[b]["kinds"][v["kind"]] = ladder[b]["kinds"].get(v["kind"], 0) + 1

            # ── E1 : the detector, on the RAW B2 extraction ───────────────────
            raw_flat = {k: ("" if b2["fields"].get(k) is None else str(b2["fields"][k]))
                        for k in KEY_FIELDS}
            s2 = _score(b2["fields"], gtf, scale)
            # Scale errors are excluded from the label: they leave every identity
            # exactly satisfied, so no arithmetic check can see them.
            wrong_doc = any(v["kind"] not in ("correct", "scale") for v in s2.values())
            det_scores.append(document_anomaly_score(raw_flat, "compte_resultat"))
            det_labels.append(int(wrong_doc))
            res = identity_residuals(raw_flat, "compte_resultat")
            n_computable["any"] += int(bool(res))
            n_computable["ebitda"] += int("ebitda_identity" in res)
            if "ebitda_identity" in res:
                trio = [s2[f] for f in ("ebit", "ebitda", "dotations_amortissements")
                        if f in s2]
                if trio:
                    det_scores_pf.append(res["ebitda_identity"])
                    det_labels_pf.append(int(any(v["kind"] not in ("correct", "scale")
                                                 for v in trio)))

            # ── E1b : the balance-sheet arm, where the identity CAN fire ──────
            pbs = EXTRACT_DIR / safe / "BS" / f"{stem}.json"
            gt_bs = {k: v for k, v in gt_rec["fields"].items() if k in BS_FIELDS}
            if pbs.exists() and gt_bs:
                bs = json.loads(pbs.read_text(encoding="utf-8"))
                cost["calls"] += 1
                cost["seconds"] += bs.get("seconds", 0.0)
                cost["tokens"] += bs.get("tokens", 0)
                bs_flat = {k: ("" if bs["fields"].get(k) is None else str(bs["fields"][k]))
                           for k in BS_FIELDS}
                bres = identity_residuals(bs_flat, "bilan")
                if "balance_equilibrium" in bres:
                    n_computable["balance"] += 1
                    sbs = _score_fields(bs["fields"], gt_bs, scale)
                    bs_scores.append(bres["balance_equilibrium"])
                    bs_labels.append(int(any(v["kind"] not in ("correct", "scale")
                                             for v in sbs.values())))

            # ── E3 : did CoT verification help? ───────────────────────────────
            if "B4" in cand:
                s4 = _score(cand["B4"], gtf, scale)
                for f in gtf:
                    if f not in s2 or f not in s4:
                        continue
                    if abs(s2[f]["raw"] - s4[f]["raw"]) < 1e-9:
                        cot["unchanged"] += 1
                    elif s4[f]["ok"] and not s2[f]["ok"]:
                        cot["better"] += 1
                    elif s2[f]["ok"] and not s4[f]["ok"]:
                        cot["worse"] += 1
                    else:
                        cot["neutral"] += 1

            # ── E5 : anchoring as a hallucination detector, window swept ──────
            for w in anchor:
                a = check_transcription_divergence(raw_flat, text, "compte_resultat",
                                                   window_before=w, window_after=w)
                pred = a["statut"] == "FAIL"
                key = ("tp" if pred else "fn") if wrong_doc else ("fp" if pred else "tn")
                anchor[w][key] += 1

            # ── E6 : leave-one-rule-out ablation of the corrector ─────────────
            base_ok = sum(1 for v in _score(cand["B6"], gtf, scale).values() if v["ok"])
            abl["__none_disabled__"]["correct"] += base_ok
            abl["__none_disabled__"]["scored"] += len(gtf)

            all_rules = {n for n, _ in INCOME_STATEMENT_RULES}
            for arm, kw in (("__no_backfill__", dict(use_regex_backfill=False)),
                            ("__no_arithmetic__", dict(use_regex_backfill=True,
                                                       disabled_rules=all_rules)),
                            ("__neither__", dict(use_regex_backfill=False,
                                                 disabled_rules=all_rules))):
                c, _l = apply_corrections(dict(raw_flat), "compte_resultat", text, **kw)
                s_arm = _score(c, gtf, scale)
                abl[arm]["correct"] += sum(1 for v in s_arm.values() if v["ok"])
                abl[arm]["scored"] += len(s_arm)
            for name, _ in INCOME_STATEMENT_RULES:
                c, _log = apply_corrections(dict(raw_flat), "compte_resultat", text,
                                            use_regex_backfill=True,
                                            disabled_rules={name})
                s = _score(c, gtf, scale)
                abl[name]["correct"] += sum(1 for v in s.values() if v["ok"])
                abl[name]["scored"] += len(s)

            # ── per-sector breakdown (E7) ─────────────────────────────────────
            e = sector.setdefault(d["sector"], {"docs": 0, "B2": [0, 0], "B6": [0, 0]})
            e["docs"] += 1
            for b in ("B2", "B6"):
                s = _score(cand[b], gtf, scale)
                e[b][0] += sum(1 for v in s.values() if v["ok"])
                e[b][1] += len(s)

        # ── aggregate ─────────────────────────────────────────────────────────
        for b, v in ladder.items():
            v["accuracy_pct"] = round(100 * v["correct"] / v["scored"], 1) if v["scored"] else None
        out["ladder"][model] = ladder
        out["detector"][model] = {
            "document_level": M.summarize(det_scores, det_labels,
                                          k=max(1, len(det_scores) // 10)),
            "n": len(det_scores), "positives": sum(det_labels),
            "negatives": len(det_labels) - sum(det_labels),
            "ebitda_triplet": (M.summarize(det_scores_pf, det_labels_pf,
                                           k=max(1, len(det_scores_pf) // 10))
                               if det_scores_pf else None),
            "n_ebitda_triplet": len(det_scores_pf),
            "positives_ebitda_triplet": sum(det_labels_pf),
            "n_computable_any": n_computable["any"],
            "n_computable_ebitda": n_computable["ebitda"],
            "n_computable_balance": n_computable["balance"],
            "balance_sheet": (M.summarize(bs_scores, bs_labels,
                                          k=max(1, len(bs_scores) // 10))
                              if bs_scores else None),
            "n_balance": len(bs_scores), "positives_balance": sum(bs_labels),
        }
        out["cot"][model] = cot
        for w, c in anchor.items():
            p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
            r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
            c["precision"] = round(100 * p, 1); c["recall"] = round(100 * r, 1)
            c["f1"] = round(100 * 2 * p * r / (p + r), 1) if p + r else 0.0
        out["anchoring"][model] = anchor
        for name, v in abl.items():
            v["accuracy_pct"] = round(100 * v["correct"] / v["scored"], 1) if v["scored"] else None
        base = abl["__none_disabled__"]["accuracy_pct"]
        for name, v in abl.items():
            if name != "__none_disabled__" and v["accuracy_pct"] is not None:
                v["delta_when_disabled"] = round(v["accuracy_pct"] - base, 1)
        out["ablation"][model] = abl
        for s, v in sector.items():
            for b in ("B2", "B6"):
                v[f"{b}_accuracy_pct"] = round(100 * v[b][0] / v[b][1], 1) if v[b][1] else None
        out["sector"][model] = sector
        cost["seconds"] = round(cost["seconds"], 1)
        cost["per_doc_calls"] = round(cost["calls"] / max(len(corpus), 1), 2)
        out["cost"][model] = cost
        if n_missing:
            out["cost"][model]["missing_extractions"] = n_missing

    RESULTS.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    _print_report(out)


def _print_report(o: dict) -> None:
    W = 78
    print("=" * W)
    print("  PAPER RESULTS")
    print("=" * W)
    c = o["config"]
    print(f"  filings={c['n_filings']}  rel_tol={c['rel_tol']}  k={c['sc_k']}  "
          f"max_chars={c['max_chars']:,}")
    pg = o.get("pairing", {})
    if pg.get("paired"):
        print(f"  PAIRED on {pg['n_paired']}/{pg['n_full']} filings — {pg['sectors']}")

    print(f"\n  Ground-truth coverage per field (n={o['coverage']['n_filings']}):")
    for f, v in sorted(o["coverage"]["per_field"].items(), key=lambda x: -x[1]["n"]):
        d = f"  ({v['derived']} derived)" if v["derived"] else ""
        print(f"    {f:<28} {v['n']:>4}  {100*v['n']/o['coverage']['n_filings']:5.1f}%{d}")

    for model, lad in o["ladder"].items():
        print(f"\n{'─'*W}\n  MODEL: {model}\n{'─'*W}")
        print(f"  E2 — field accuracy vs XBRL (±{100*o['config']['rel_tol']:.0f}%)")
        print(f"    {'':4} {'accuracy':>10}  {'correct/scored':>16}   error kinds")
        for b in ("B1", "B2", "B3", "B4", "B5", "B6"):
            v = lad[b]
            if v["accuracy_pct"] is None:
                continue
            kinds = " ".join(f"{k}={n}" for k, n in sorted(v["kinds"].items()))
            print(f"    {b:4} {v['accuracy_pct']:>9.1f}%  {v['correct']:>7}/{v['scored']:<8}  {kinds}")

        d = o["detector"][model]
        dl = d["document_level"]
        print(f"\n  E1 — identity violation as an unsupervised error detector")
        print(f"    document level : n={d['n']}  pos={d['positives']}  neg={d['negatives']}")
        pk = next((f"{k}={v}" for k, v in dl.items() if k.startswith("precision_at_")), "P@k=n/a")
        print(f"                     ROC-AUC={dl.get('roc_auc')}  PR-AUC={dl.get('pr_auc')}  {pk}")
        print(f"    identity computable on the raw extraction: "
              f"any={d['n_computable_any']}/{d['n']}  "
              f"ebitda={d['n_computable_ebitda']}/{d['n']}")
        if d.get("balance_sheet"):
            b = d["balance_sheet"]
            print(f"    balance sheet  : n={d['n_balance']} pos={d['positives_balance']}"
                  f"  computable={d['n_computable_balance']}/{d['n']}")
            pkb = next((f"{k}={v}" for k, v in b.items()
                        if k.startswith("precision_at_")), "")
            print(f"                     ROC-AUC={b.get('roc_auc')}  "
                  f"PR-AUC={b.get('pr_auc')}  {pkb}")
        if d["ebitda_triplet"]:
            t = d["ebitda_triplet"]
            print(f"    EBITDA triplet : n={d['n_ebitda_triplet']} "
                  f"pos={d['positives_ebitda_triplet']}  "
                  f"ROC-AUC={t.get('roc_auc')}  PR-AUC={t.get('pr_auc')}")

        ct = o["cot"][model]
        print(f"\n  E3 — CoT self-verification, per field")
        print(f"    changed & became correct : {ct['better']}")
        print(f"    changed & became wrong   : {ct['worse']}")
        print(f"    changed, no change in    : {ct['neutral']}")
        print(f"    unchanged                : {ct['unchanged']}")

        print(f"\n  E5 — anchoring detector, window sweep")
        print(f"    {'±lines':>7} {'P':>7} {'R':>7} {'F1':>7}   TP/FP/FN/TN")
        for w, a in sorted(o["anchoring"][model].items(), key=lambda x: int(x[0])):
            print(f"    {w:>7} {a['precision']:>6.1f}% {a['recall']:>6.1f}% {a['f1']:>6.1f}%"
                  f"   {a['tp']}/{a['fp']}/{a['fn']}/{a['tn']}")

        ab = o["ablation"][model]
        print(f"\n  E6 — leave-one-rule-out (full corrector = {ab['__none_disabled__']['accuracy_pct']}%)")
        rows = [(n, v) for n, v in ab.items() if n != "__none_disabled__"
                and v.get("delta_when_disabled") is not None]
        for n, v in sorted(rows, key=lambda x: x[1]["delta_when_disabled"]):
            tag = "HARMFUL" if v["delta_when_disabled"] > 0 else ("useful" if v["delta_when_disabled"] < 0 else "inert")
            print(f"    {n:<26} {v['accuracy_pct']:>6.1f}%   Δ={v['delta_when_disabled']:+5.1f} pt  {tag}")

        print(f"\n  E7 — per sector (B2 raw → B6 firewall)")
        for s, v in sorted(o["sector"][model].items(),
                           key=lambda x: -(x[1].get("B6_accuracy_pct") or 0)):
            print(f"    {s:<14} n={v['docs']:>3}   B2={v['B2_accuracy_pct']:>5.1f}%"
                  f"  →  B6={v['B6_accuracy_pct']:>5.1f}%")

        co = o["cost"][model]
        print(f"\n  Cost: {co['calls']} calls  {co['seconds']:.0f}s  "
              f"{co['tokens']:,} completion tokens  ({co['per_doc_calls']} calls/doc)")
    print("=" * W)
    print(f"  written -> {RESULTS}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the paper's experiments.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="phase 1: cached LLM extraction")
    pe.add_argument("--model", required=True)
    pe.add_argument("--workers", type=int, default=6)
    pe.add_argument("--limit", type=int, default=None)
    pe.add_argument("--methods", default=None,
                    help="comma list, e.g. B2,B4 (default: B2,B3,B4)")
    pe.set_defaults(func=cmd_extract)

    pr = sub.add_parser("report", help="phase 2: offline metrics")
    pr.add_argument("--models", default="meta/llama-3.1-8b-instruct")
    pr.add_argument("--paired", action="store_true",
                    help="restrict to documents all listed models have (valid tier "
                         "comparison)")
    pr.set_defaults(func=cmd_report)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
