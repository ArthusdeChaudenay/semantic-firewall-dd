"""
generate_report.py — Dashboards HTML + PDF à partir des rapports JSON

Lit :  output/benchmark_report.json  (accuracy, FinVerBench par doc)
       output/auditwen_report.json   (score AuditWen, accord LLM/FinVerBench)
       output/dd_report.json         (résultats Due Diligence VC/M&A)

Écrit trois dashboards indépendants :
  output/benchmark_dashboard.html/.pdf   — vue combinée (existant)
  output/auditwen_dashboard.html/.pdf    — factures + AuditWen uniquement
  output/dd_dashboard.html/.pdf          — Due Diligence uniquement

Lance avec :
    .\\venv\\Scripts\\python.exe generate_report.py
"""

import json
from datetime import datetime
from pathlib import Path

BENCHMARK_PATH   = Path("output/benchmark_report.json")
AUDITWEN_PATH    = Path("output/auditwen_report.json")
DD_PATH          = Path("output/dd_report.json")
DASHBOARD_PATH   = Path("output/benchmark_dashboard.html")
PDF_PATH         = Path("output/benchmark_dashboard.pdf")
AUDITWEN_HTML    = Path("output/auditwen_dashboard.html")
AUDITWEN_PDF     = Path("output/auditwen_dashboard.pdf")
DD_HTML          = Path("output/dd_dashboard.html")
DD_PDF           = Path("output/dd_dashboard.pdf")


# ==========================================
# UTILITAIRES HTML
# ==========================================

def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def badge(statut: str) -> str:
    c = {"PASS": "#10b981", "FAIL": "#f43f5e", "ERROR": "#f59e0b"}.get(statut, "#64748b")
    return f'<span style="background:{c};color:#fff;padding:2px 9px;border-radius:5px;font-size:.72rem;font-weight:700;letter-spacing:.03em">{statut}</span>'


def score_color(v) -> str:
    if not isinstance(v, (int, float)):
        return "#64748b"
    if v >= 90: return "#10b981"
    if v >= 70: return "#f59e0b"
    return "#f43f5e"


def stacked_bar(p: int, f: int, e: int) -> str:
    total = p + f + e or 1
    pw, fw, ew = p/total*100, f/total*100, e/total*100
    return f"""
    <div style="display:flex;height:18px;border-radius:5px;overflow:hidden;background:#252840;margin:10px 0 6px">
      <div style="width:{pw:.1f}%;background:#10b981" title="PASS {p}"></div>
      <div style="width:{fw:.1f}%;background:#f43f5e" title="FAIL {f}"></div>
      <div style="width:{ew:.1f}%;background:#f59e0b" title="ERROR {e}"></div>
    </div>
    <div style="font-size:.74rem;color:#94a3b8">
      <span style="color:#10b981">■ PASS {p}</span>&nbsp;&nbsp;
      <span style="color:#f43f5e">■ FAIL {f}</span>&nbsp;&nbsp;
      <span style="color:#f59e0b">■ ERROR {e}</span>
    </div>"""


def kpi(label: str, value, unit: str = "") -> str:
    c = score_color(value)
    disp = value if value is not None else "N/A"
    u = unit if value is not None else ""
    return f"""
    <div style="background:#161929;border:1px solid #252840;border-radius:12px;padding:24px;text-align:center">
      <div style="font-size:.78rem;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">{label}</div>
      <div style="font-size:2.4rem;font-weight:800;color:{c};line-height:1">{disp}<span style="font-size:.95rem;font-weight:400;color:#64748b">{u}</span></div>
    </div>"""


# ==========================================
# SECTIONS HTML
# ==========================================

def section_kpi(bench: dict | None, audit: dict | None, accuracy_override: float | None = None) -> str:
    m = audit["metriques_globales"] if audit else {}
    bm = bench["metriques"] if bench else {}
    acc = bm.get("accuracy_champs_pct")
    if acc is None:
        acc = accuracy_override
    return f"""
    <section>
      <h2>Métriques clés</h2>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px">
        {kpi("Score AuditWen moyen", m.get("auditwen_score_moyen"), "/100")}
        {kpi("Exactitude des champs", acc, "%")}
        {kpi("Complétude moyenne", m.get("completeness_moyenne_pct"), "%")}
        {kpi("Accord LLM / FinVerBench", m.get("accord_llm_finverbench_pct"), "%")}
      </div>
    </section>"""


def section_finverbench(bench: dict | None) -> str:
    if not bench:
        return ""
    fb = bench["metriques"].get("finverbench", {})
    labels = {"completeness": "Complétude", "arithmetic": "Arithmétique", "temporal": "Temporel", "vat_rate": "Taux TVA", "transcription": "Transcription"}
    cards = ""
    for key, label in labels.items():
        if key not in fb:
            continue
        d = fb[key]["detail"]
        pr = fb[key].get("pass_rate_pct") or 0
        c = score_color(pr)
        cards += f"""
        <div style="background:#161929;border:1px solid #252840;border-radius:12px;padding:22px;flex:1;min-width:180px">
          <div style="font-size:.78rem;color:#64748b;text-transform:uppercase;letter-spacing:.07em">{label}</div>
          <div style="font-size:2rem;font-weight:800;color:{c};margin:6px 0 0">{pr:.0f}%</div>
          {stacked_bar(d.get("PASS",0), d.get("FAIL",0), d.get("ERROR",0))}
        </div>"""
    return f"""
    <section>
      <h2>Contrôles FinVerBench</h2>
      <div style="display:flex;gap:16px;flex-wrap:wrap">{cards}</div>
    </section>"""


def section_documents(bench: dict | None, audit: dict | None) -> str:
    docs_a = {r["document"]: r for r in (audit["resultats"] if audit else [])}
    docs_b = {r["document"]: r for r in (bench["resultats_par_document"] if bench else [])}
    all_docs = sorted(set(docs_a) | set(docs_b))

    rows = ""
    for doc in all_docs:
        a = docs_a.get(doc, {})
        b = docs_b.get(doc, {})

        if a.get("statut") == "error":
            rows += f'<tr><td style="font-family:monospace;font-size:.82rem">{doc}</td><td colspan="6" style="color:#f43f5e">Erreur : {a.get("message","")}</td></tr>'
            continue

        sc = a.get("scores", {})
        aw_sc = sc.get("auditwen_score", "N/A")
        comp = sc.get("completeness_pct", "N/A")
        acc  = sc.get("accuracy_pct")
        acc_str = f"{acc}%" if acc is not None else "<span style='color:#64748b'>N/A</span>"

        fb_doc = a.get("finverbench", {})
        compl_s = fb_doc.get("completeness", {}).get("statut", "N/A")
        arith_s = fb_doc.get("arithmetic",   {}).get("statut", "N/A")
        temp_s  = fb_doc.get("temporal",     {}).get("statut", "N/A")

        c = score_color(aw_sc)
        rows += f"""<tr>
          <td style="font-family:monospace;font-size:.82rem">{doc}</td>
          <td style="text-align:center;font-weight:800;color:{c}">{aw_sc}/100</td>
          <td style="text-align:center">{comp}%</td>
          <td style="text-align:center">{acc_str}</td>
          <td style="text-align:center">{badge(compl_s)}</td>
          <td style="text-align:center">{badge(arith_s)}</td>
          <td style="text-align:center">{badge(temp_s)}</td>
        </tr>"""

    return f"""
    <section>
      <h2>Résultats par document</h2>
      <div style="background:#161929;border:1px solid #252840;border-radius:12px;overflow:hidden;overflow-x:auto">
        <table>
          <thead><tr>
            <th>Document</th>
            <th style="text-align:center">Score AuditWen</th>
            <th style="text-align:center">Complétude</th>
            <th style="text-align:center">Exactitude</th>
            <th style="text-align:center">Compl. FVB</th>
            <th style="text-align:center">Arithmétique</th>
            <th style="text-align:center">Temporel</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>"""


def _normalize_val(v) -> str:
    if v is None:
        return ""
    s = str(v).strip().replace(",", ".").lower()
    if s in ("none", "null", "n/a", "inconnu", ""):
        return ""
    try:
        return str(float(s))
    except ValueError:
        return s


def _compute_field_stats(bench: dict | None, audit: dict | None) -> tuple[dict, float | None]:
    """Calcule l'exactitude par champ. Retourne (stats, accuracy_pct)."""
    stats: dict[str, dict] = {}

    # Source 1 : comparaison_champs pré-calculée dans benchmark_report
    if bench:
        for r in bench.get("resultats_par_document", []):
            for field, comp in r.get("comparaison_champs", {}).items():
                s = stats.setdefault(field, {"ok": 0, "total": 0})
                s["total"] += 1
                if comp.get("match"):
                    s["ok"] += 1

    if not stats:
        gt_dir = Path("output")
        # Source 2 : champs_extraits (auditwen_report) + fichiers GT
        sources: list[tuple[str, dict]] = []
        if audit:
            for r in audit.get("resultats", []):
                if r.get("statut") == "ok":
                    sources.append((r["document"], r.get("champs_extraits", {})))
        # Source 3 : champs_llm (benchmark_report) + fichiers GT si pas d'audit
        if not sources and bench:
            for r in bench.get("resultats_par_document", []):
                if r.get("statut_extraction") == "ok":
                    sources.append((r["document"], r.get("champs_llm", {})))

        for doc_name, champs in sources:
            gt_path = gt_dir / f"{doc_name}.json"
            if not gt_path.exists():
                continue
            try:
                gt_champs = json.loads(gt_path.read_text(encoding="utf-8")).get("champs", {})
            except Exception:
                continue
            for key in set(gt_champs) | set(champs):
                gt_raw  = gt_champs.get(key, {})
                gt_v    = _normalize_val(gt_raw.get("valeur", "") if isinstance(gt_raw, dict) else gt_raw)
                llm_raw = champs.get(key, {})
                llm_v   = _normalize_val(llm_raw.get("valeur") if isinstance(llm_raw, dict) else llm_raw)
                s = stats.setdefault(key, {"ok": 0, "total": 0})
                s["total"] += 1
                if llm_v == gt_v:
                    s["ok"] += 1

    if not stats:
        return {}, None
    total = sum(s["total"] for s in stats.values())
    ok    = sum(s["ok"]    for s in stats.values())
    return stats, (round(ok / total * 100, 1) if total else None)


def section_field_accuracy(bench: dict | None, audit: dict | None = None) -> str:
    stats, _ = _compute_field_stats(bench, audit)

    if not stats:
        return ""

    rows = ""
    for field, s in sorted(stats.items(), key=lambda x: x[1]["ok"] / max(x[1]["total"], 1)):
        pct = s["ok"] / max(s["total"], 1) * 100
        c   = score_color(pct)
        rows += f"""<tr>
          <td style="font-family:monospace;font-size:.85rem">{field}</td>
          <td style="width:60%">
            <div style="display:flex;align-items:center;gap:10px">
              <div style="flex:1;background:#252840;border-radius:4px;height:12px;overflow:hidden">
                <div style="width:{pct:.0f}%;background:{c};height:100%;border-radius:4px"></div>
              </div>
              <span style="color:{c};font-weight:700;min-width:36px;text-align:right">{pct:.0f}%</span>
            </div>
          </td>
          <td style="text-align:center;color:#64748b;font-size:.82rem">{s["ok"]}/{s["total"]}</td>
        </tr>"""

    return f"""
    <section>
      <h2>Exactitude par champ (vs ground truth)</h2>
      <div style="background:#161929;border:1px solid #252840;border-radius:12px;overflow:hidden;overflow-x:auto">
        <table>
          <thead><tr>
            <th>Champ</th><th>Taux de correspondance</th><th style="text-align:center">Correct / Total</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>"""


# ==========================================
# SECTIONS DUE DILIGENCE
# ==========================================

def section_dd_documents(dd: dict | None) -> str:
    if not dd:
        return ""
    TYPE_LABEL = {"bilan": "Bilan", "compte_resultat": "Compte de résultat", "captable": "Cap Table"}
    rows = ""
    for r in dd.get("resultats", []):
        if r.get("statut") == "error":
            rows += f'<tr><td style="font-family:monospace;font-size:.82rem">{r["document"]}</td><td colspan="4" style="color:#f43f5e">Erreur : {r.get("message","")}</td></tr>'
            continue
        doc  = r["document"]
        typ  = TYPE_LABEL.get(r.get("doc_type", ""), r.get("doc_type", ""))
        acc  = r.get("accuracy_pct")
        acc_str = f"{acc}%" if acc is not None else '<span style="color:#64748b">N/A</span>'
        comp = r.get("completeness", {}).get("statut", "N/A")
        checks = r.get("dd_audit", {})
        fail_count = sum(1 for v in checks.values() if v.get("statut") == "FAIL")
        pass_count = sum(1 for v in checks.values() if v.get("statut") == "PASS")
        total_checks = len(checks)
        c = score_color(acc if acc is not None else 0)
        rows += f"""<tr>
          <td style="font-family:monospace;font-size:.82rem">{doc}</td>
          <td style="text-align:center;color:#64748b;font-size:.8rem">{typ}</td>
          <td style="text-align:center">{acc_str}</td>
          <td style="text-align:center">{badge(comp)}</td>
          <td style="text-align:center;font-size:.82rem"><span style="color:#10b981">{pass_count} PASS</span> / <span style="color:#f43f5e">{fail_count} FAIL</span> / {total_checks} checks</td>
        </tr>"""

    acc_moy = dd.get("accuracy_moyenne_pct")
    acc_moy_str = f"{acc_moy}%" if acc_moy is not None else "N/A"
    return f"""
    <section>
      <h2>Documents Due Diligence VC/M&amp;A</h2>
      <div style="background:#161929;border:1px solid #252840;border-radius:12px;overflow:hidden;overflow-x:auto">
        <table>
          <thead><tr>
            <th>Document</th>
            <th style="text-align:center">Type</th>
            <th style="text-align:center">Exactitude</th>
            <th style="text-align:center">Complétude</th>
            <th style="text-align:center">Contrôles DDTaxonomy</th>
          </tr></thead>
          <tbody>
            {rows}
            <tr style="background:#0d0f1a;font-weight:700">
              <td colspan="2" style="color:#64748b;font-size:.8rem">Moyenne ({dd.get("nb_documents","?")} docs)</td>
              <td style="text-align:center;color:{score_color(acc_moy or 0)}">{acc_moy_str}</td>
              <td colspan="2"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>"""


def _compute_dd_field_stats(dd: dict | None) -> tuple[dict, float | None]:
    """Calcule l'exactitude par champ pour les documents DD. Retourne (stats, accuracy_pct)."""
    if not dd:
        return {}, None
    stats: dict[str, dict] = {}

    # Source 1 : comparaison_champs pré-calculée (disponible après re-run de dd_eval.py avec GT files)
    for r in dd.get("resultats", []):
        if r.get("statut") != "ok":
            continue
        for field, comp in r.get("comparaison_champs", {}).items():
            s = stats.setdefault(field, {"ok": 0, "total": 0})
            s["total"] += 1
            if comp.get("match"):
                s["ok"] += 1

    # Source 2 (fallback) : champs_extraits + fichiers GT dans output/
    if not stats:
        gt_dir = Path("output")
        for r in dd.get("resultats", []):
            if r.get("statut") != "ok":
                continue
            doc_name = r.get("document", "")
            champs   = r.get("champs_extraits", {})
            gt_path  = gt_dir / f"{doc_name}.json"
            if not gt_path.exists():
                continue
            try:
                gt_champs = json.loads(gt_path.read_text(encoding="utf-8")).get("champs", {})
            except Exception:
                continue
            for key in set(gt_champs) | set(champs):
                gt_raw = gt_champs.get(key, {})
                gt_v   = _normalize_val(gt_raw.get("valeur", "") if isinstance(gt_raw, dict) else gt_raw)
                llm_v  = _normalize_val(champs.get(key, ""))
                s = stats.setdefault(key, {"ok": 0, "total": 0})
                s["total"] += 1
                if llm_v == gt_v:
                    s["ok"] += 1

    if not stats:
        return {}, None
    total = sum(s["total"] for s in stats.values())
    ok    = sum(s["ok"]    for s in stats.values())
    return stats, (round(ok / total * 100, 1) if total else None)


def section_dd_field_accuracy(dd: dict | None) -> str:
    if not dd:
        return ""
    stats, _ = _compute_dd_field_stats(dd)
    if not stats:
        return ""
    rows = ""
    for field, s in sorted(stats.items(), key=lambda x: x[1]["ok"] / max(x[1]["total"], 1)):
        pct = s["ok"] / max(s["total"], 1) * 100
        c   = score_color(pct)
        rows += f"""<tr>
          <td style="font-family:monospace;font-size:.85rem">{field}</td>
          <td style="width:60%">
            <div style="display:flex;align-items:center;gap:10px">
              <div style="flex:1;background:#252840;border-radius:4px;height:12px;overflow:hidden">
                <div style="width:{pct:.0f}%;background:{c};height:100%;border-radius:4px"></div>
              </div>
              <span style="color:{c};font-weight:700;min-width:36px;text-align:right">{pct:.0f}%</span>
            </div>
          </td>
          <td style="text-align:center;color:#64748b;font-size:.82rem">{s["ok"]}/{s["total"]}</td>
        </tr>"""
    return f"""
    <section>
      <h2>Exactitude par champ — Documents DD</h2>
      <div style="background:#161929;border:1px solid #252840;border-radius:12px;overflow:hidden;overflow-x:auto">
        <table>
          <thead><tr>
            <th>Champ</th><th>Taux de correspondance</th><th style="text-align:center">Correct / Total</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>"""


# ==========================================
# ASSEMBLAGE DU DASHBOARD
# ==========================================

def generate_dashboard() -> Path | None:
    bench = load_json(BENCHMARK_PATH)
    audit = load_json(AUDITWEN_PATH)
    dd    = load_json(DD_PATH)

    if not bench and not audit and not dd:
        print("Aucun rapport trouvé dans output/. Lance d'abord evaluate.py, auditwen_eval.py et dd_eval.py.")
        return None

    modele      = (bench or audit or dd).get("modele", "N/A")
    nb_factures = (bench or audit or {}).get("nb_documents", 0)
    nb_dd       = dd.get("nb_documents", 0) if dd else 0
    nb_total    = nb_factures + nb_dd
    date_gen    = datetime.now().strftime("%d/%m/%Y %H:%M")

    doc_label = f"{nb_total} documents"
    if nb_dd:
        doc_label += f" ({nb_factures} factures + {nb_dd} DD)"

    _, field_acc = _compute_field_stats(bench, audit)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FinVerBench × AuditWen — Rapport global</title>
  <style>{_base_css()}</style>
</head>
<body>
<div class="container">
  {_page_header(
      'FinVerBench <span style="color:#6366f1">×</span> AuditWen',
      "benchmark · auditwen · dd",
      modele, doc_label, date_gen
  )}
  {section_kpi(bench, audit, field_acc)}
  {section_finverbench(bench)}
  {section_documents(bench, audit)}
  {section_field_accuracy(bench, audit)}
  <hr class="divider">
  {section_dd_documents(dd)}
  {section_dd_field_accuracy(dd)}
  {_page_footer(modele, date_gen)}
</div>
</body>
</html>"""

    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    print(f"HTML généré : {DASHBOARD_PATH}")
    _export_pdf(DASHBOARD_PATH, PDF_PATH)
    return DASHBOARD_PATH


def _base_css() -> str:
    return """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
           background: #0d0f1a; color: #e2e8f0; min-height: 100vh; }
    .container { max-width: 1200px; margin: 0 auto; padding: 36px 24px; }
    h2 { font-size: .78rem; font-weight: 700; color: #475569; text-transform: uppercase;
         letter-spacing: .1em; margin-bottom: 16px; }
    section { margin-bottom: 44px; }
    table { width: 100%; border-collapse: collapse; font-size: .88rem; }
    th { padding: 11px 14px; color: #64748b; font-size: .72rem; text-transform: uppercase;
         letter-spacing: .06em; border-bottom: 1px solid #252840; font-weight: 600; text-align: left; }
    td { padding: 13px 14px; border-bottom: 1px solid #1a1d2e; vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #11142080; }
    .divider { border: none; border-top: 1px solid #252840; margin: 44px 0; }
    """


def _page_header(title: str, subtitle: str, modele: str, doc_label: str, date_gen: str) -> str:
    return f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-start;
                margin-bottom:36px;flex-wrap:wrap;gap:12px">
      <div>
        <h1 style="font-size:1.65rem;font-weight:800;letter-spacing:-.02em">{title}</h1>
        <p style="margin-top:7px;color:#64748b;font-size:.88rem">
          Modèle&nbsp;: <strong style="color:#e2e8f0">{modele}</strong>
          &nbsp;·&nbsp; {doc_label}
          &nbsp;·&nbsp; Généré le {date_gen}
        </p>
      </div>
      <div style="background:#161929;border:1px solid #252840;border-radius:8px;
                  padding:9px 14px;font-size:.75rem;color:#64748b">{subtitle}</div>
    </div>"""


def _page_footer(modele: str, date_gen: str) -> str:
    return f"""
    <div style="text-align:center;color:#334155;font-size:.74rem;
                margin-top:48px;padding-top:20px;border-top:1px solid #1a1d2e">
      FinVerBench × AuditWen &nbsp;·&nbsp; {modele} &nbsp;·&nbsp; {date_gen}
    </div>"""


def _section_dd_kpi(dd: dict, accuracy_override: float | None = None) -> str:
    nb   = dd.get("nb_documents", 0)
    nerr = dd.get("nb_erreurs", 0)
    acc  = dd.get("accuracy_moyenne_pct")
    if acc is None:
        acc = accuracy_override
    controles = dd.get("controles", {})
    total_pass  = sum(v.get("PASS", 0)  for v in controles.values())
    total_fail  = sum(v.get("FAIL", 0)  for v in controles.values())
    total_error = sum(v.get("ERROR", 0) for v in controles.values())
    total_checks = total_pass + total_fail + total_error
    pass_rate = round(total_pass / total_checks * 100, 1) if total_checks else None
    return f"""
    <section>
      <h2>Métriques clés — Due Diligence</h2>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px">
        {kpi("Documents analysés",   nb,        "")}
        {kpi("Taux de contrôles OK", pass_rate, "%")}
        {kpi("Exactitude champs",    acc,       "%")}
        {kpi("Erreurs extraction",   nerr,      "")}
      </div>
    </section>"""


def _section_dd_controls_breakdown(dd: dict) -> str:
    controles = dd.get("controles", {})
    if not controles:
        return ""
    LABELS = {
        "equilibre_bilan":       "Équilibre bilan",
        "decomposition_actif":   "Décomposition actif",
        "total_pct":             "Total % cap table",
        "valorisation_post":     "Valorisation post-money",
        "prix_par_action":       "Prix par action",
        "ebitda_coherence":      "Cohérence EBITDA",
        "marge_brute":           "Marge brute",
        "completeness":          "Complétude",
    }
    cards = ""
    for key, v in controles.items():
        label = LABELS.get(key, key.replace("_", " ").title())
        p, f, e = v.get("PASS", 0), v.get("FAIL", 0), v.get("ERROR", 0)
        total = p + f + e or 1
        pr = round(p / total * 100)
        c  = score_color(pr)
        cards += f"""
        <div style="background:#161929;border:1px solid #252840;border-radius:12px;padding:22px;flex:1;min-width:180px">
          <div style="font-size:.78rem;color:#64748b;text-transform:uppercase;letter-spacing:.07em">{label}</div>
          <div style="font-size:2rem;font-weight:800;color:{c};margin:6px 0 0">{pr}%</div>
          {stacked_bar(p, f, e)}
        </div>"""
    return f"""
    <section>
      <h2>Contrôles DDTaxonomy</h2>
      <div style="display:flex;gap:16px;flex-wrap:wrap">{cards}</div>
    </section>"""


# ==========================================
# DASHBOARD AUDITWEN (factures)
# ==========================================

def generate_auditwen_dashboard() -> Path | None:
    bench = load_json(BENCHMARK_PATH)
    audit = load_json(AUDITWEN_PATH)
    if not audit:
        print("auditwen_report.json introuvable — dashboard AuditWen ignoré.")
        return None

    modele   = (bench or audit).get("modele", "N/A")
    nb_docs  = audit.get("nb_documents", 0)
    date_gen = datetime.now().strftime("%d/%m/%Y %H:%M")

    _, field_acc = _compute_field_stats(bench, audit)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AuditWen — Dashboard Factures</title>
  <style>{_base_css()}</style>
</head>
<body>
<div class="container">
  {_page_header(
      'AuditWen <span style="color:#6366f1">—</span> Factures',
      "auditwen · finverbench",
      modele, f"{nb_docs} factures", date_gen
  )}
  {section_kpi(bench, audit, field_acc)}
  {section_finverbench(bench)}
  {section_documents(bench, audit)}
  {section_field_accuracy(bench, audit)}
  {_page_footer(modele, date_gen)}
</div>
</body>
</html>"""

    AUDITWEN_HTML.write_text(html, encoding="utf-8")
    print(f"HTML généré : {AUDITWEN_HTML}")
    _export_pdf(AUDITWEN_HTML, AUDITWEN_PDF)
    return AUDITWEN_HTML


# ==========================================
# DASHBOARD DD
# ==========================================

def generate_dd_dashboard() -> Path | None:
    dd = load_json(DD_PATH)
    if not dd:
        print("dd_report.json introuvable — dashboard DD ignoré.")
        return None

    modele   = dd.get("modele", "N/A")
    nb_docs  = dd.get("nb_documents", 0)
    date_gen = datetime.now().strftime("%d/%m/%Y %H:%M")

    _, dd_field_acc = _compute_dd_field_stats(dd)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pare-feu DD — Due Diligence VC/M&A</title>
  <style>{_base_css()}</style>
</head>
<body>
<div class="container">
  {_page_header(
      'Pare-feu DD <span style="color:#6366f1">—</span> Due Diligence VC/M&amp;A',
      "dd · ddtaxonomy",
      modele, f"{nb_docs} documents", date_gen
  )}
  {_section_dd_kpi(dd, dd_field_acc)}
  {_section_dd_controls_breakdown(dd)}
  {section_dd_documents(dd)}
  {section_dd_field_accuracy(dd)}
  {_page_footer(modele, date_gen)}
</div>
</body>
</html>"""

    DD_HTML.write_text(html, encoding="utf-8")
    print(f"HTML généré : {DD_HTML}")
    _export_pdf(DD_HTML, DD_PDF)
    return DD_HTML


# ==========================================
# EXPORT PDF
# ==========================================

def _export_pdf(html_path: Path, pdf_path: Path) -> None:
    """Génère le PDF via Edge headless (déjà installé sur Windows 11)."""
    import subprocess

    edge_candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge = next((p for p in edge_candidates if Path(p).exists()), None)

    if not edge:
        print("PDF non généré : Edge introuvable. Ouvre le HTML dans un navigateur et fais Ctrl+P → Enregistrer en PDF.")
        return

    abs_html = html_path.resolve()
    abs_pdf  = pdf_path.resolve()

    try:
        subprocess.run(
            [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--print-to-pdf={abs_pdf}", "--print-to-pdf-no-header",
             f"file:///{abs_html}"],
            check=True, capture_output=True, timeout=30,
        )
        print(f"PDF généré  : {pdf_path}")
    except Exception as e:
        print(f"PDF non généré : {e}")


if __name__ == "__main__":
    generate_dashboard()
    generate_auditwen_dashboard()
    generate_dd_dashboard()
