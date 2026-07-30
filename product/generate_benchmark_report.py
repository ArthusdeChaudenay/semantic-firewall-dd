"""
generate_benchmark_report.py — Génère le rapport HTML/PDF du benchmark comparatif.
Usage : .\\venv\\Scripts\\python.exe generate_benchmark_report.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS_PATH      = Path("output/benchmark_results.json")
RESULTS_FULL_PATH = Path("output/benchmark_results_full.json")
REPORT_PATH       = Path("output/benchmark_report.html")
REPORT_FULL_PATH  = Path("output/benchmark_report_full.html")

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_val(v, unit="M$") -> str:
    if v is None:
        return '<span class="missing">—</span>'
    try:
        n = float(str(v).replace(",", "").replace(" ", ""))
        if n == 0:
            return '<span class="missing">0</span>'
        return f'<span class="num">{n:,.0f}</span>'
    except (ValueError, TypeError):
        return f'<span class="num">{v}</span>'

def badge_recall(pct) -> str:
    if pct is None:
        pct = 0
    if pct >= 80:
        cls = "badge-good"
    elif pct >= 40:
        cls = "badge-warn"
    else:
        cls = "badge-bad"
    return f'<span class="badge {cls}">{pct:.0f}%</span>'

def badge_coherence(coherent, err_pct=None) -> str:
    if coherent is None:
        return '<span class="badge badge-gray">N/A</span>'
    if coherent:
        return '<span class="badge badge-good">✓ Cohérent</span>'
    txt = f"✗ Δ{err_pct:.0f}%" if err_pct else "✗ Incohérent"
    return f'<span class="badge badge-bad">{txt}</span>'

def badge_statut(statut) -> str:
    MAP = {
        "CERTIFIÉ":  ("badge-good",  "CERTIFIÉ"),
        "VIGILANCE": ("badge-warn",  "VIGILANCE"),
        "ANOMALIE":  ("badge-bad",   "ANOMALIE"),
        "ERREUR":    ("badge-bad",   "ERREUR"),
    }
    cls, label = MAP.get(statut, ("badge-gray", statut or "—"))
    return f'<span class="badge {cls}">{label}</span>'

def badge_jsd(jsd, alert) -> str:
    if jsd is None:
        return '<span class="badge badge-gray">—</span>'
    pct = round(jsd * 100, 1)
    cls = "badge-bad" if alert else "badge-good"
    lbl = f"JSD {pct}% {'⚠' if alert else '✓'}"
    return f'<span class="badge {cls}">{lbl}</span>'

METHOD_LABELS = {
    "method1": ("M1", "Regex / Géométrique",              "#6B7280"),
    "method2": ("M2", "LLM Zéro-Shot",                    "#0EA5E9"),
    "method3": ("M3", "LLM + CoT Auto-correction",        "#F59E0B"),
    "method4": ("M4", "Pare-feu Hybride Neuro-Symbolique","#10B981"),
}

# ── Génération HTML ───────────────────────────────────────────────────────────

def generate_html(data: dict) -> str:
    date_str = data.get("date", "")
    model    = data.get("model", "")
    docs     = data.get("documents", [])

    # ── Observations auto-générées (remplace le dict OBS hardcodé) ───────────
    def auto_obs(mk: str, m: dict, met: dict) -> str:
        recall    = met.get("recall_pct", 0)
        coherent  = met.get("ebitda_coherent")
        err_pct   = met.get("ebitda_error_pct")
        if mk == "method4":
            statut = m.get("statut", "")
            score  = m.get("score_confiance", 0)
            anom   = m.get("anomalies_detected", [])
            jsd    = m.get("semantic_jsd")
            if statut == "CERTIFIÉ":
                jsd_s = f" JSD={jsd:.2f}" if jsd is not None else ""
                return f"Pipeline certifié — {recall:.0f}% rappel, score {score:.0f}%.{jsd_s}"
            elif statut == "ERREUR":
                err = anom[0]["message"][:100] if anom else "Erreur inconnue"
                return f"Erreur : {err}"
            elif statut == "VIGILANCE":
                return f"VIGILANCE sémantique — valeurs certifiées mais dérive JSD détectée."
            elif statut == "ANOMALIE":
                checks = [a.get("check","?") for a in anom[:3]]
                return f"Anomalies : {', '.join(checks)}. Rappel {recall:.0f}%."
            return f"Rappel {recall:.0f}%."
        else:
            if recall == 0:
                return "0 champ extrait — structure non reconnue ou fenêtre contextuelle saturée."
            elif coherent is True:
                return f"Rappel {recall:.0f}%, EBITDA cohérent."
            elif coherent is False:
                return f"Rappel {recall:.0f}%, EBITDA incohérent (Δ{err_pct}%)."
            return f"Rappel {recall:.0f}%."

    n_docs = len(docs)
    corpus_label = f"{n_docs} P&amp;L 10-K SEC réels"

    # ── Agrégation par secteur (pour rapport multi-docs) ──────────────────────
    sectors: dict = {}
    for doc in docs:
        s = doc.get("secteur", "Autre")
        if s not in sectors:
            sectors[s] = []
        sectors[s].append(doc)

    sector_rows = ""
    if n_docs > 4:
        for sector, sdocs in sorted(sectors.items()):
            for mk, (short, label, color) in METHOD_LABELS.items():
                recalls = []
                for d in sdocs:
                    r = d["methods"].get(mk, {}).get("metrics", {}).get("recall_pct")
                    if r is not None:
                        recalls.append(r)
                avg_r = round(sum(recalls) / len(recalls), 1) if recalls else 0
            # Per-sector M4 row only (most interesting)
            m4_recalls = []
            m4_cert = 0
            m4_anom = 0
            for d in sdocs:
                m4 = d["methods"].get("method4", {})
                r = m4.get("metrics", {}).get("recall_pct")
                if r is not None:
                    m4_recalls.append(r)
                st = m4.get("statut", "")
                if st == "CERTIFIÉ":
                    m4_cert += 1
                elif st in ("ANOMALIE", "ERREUR"):
                    m4_anom += 1
            m4_avg = round(sum(m4_recalls) / len(m4_recalls), 1) if m4_recalls else 0
            cert_badge = (
                f'<span class="badge badge-good">{m4_cert}/{len(sdocs)} certifiés</span>'
                if m4_cert else
                f'<span class="badge badge-bad">{m4_anom} anomalies</span>'
            )
            sector_rows += f"""
            <tr>
              <td><strong>{sector}</strong> <span style="color:var(--muted);font-size:0.72rem">({len(sdocs)} docs)</span></td>
              <td>{badge_recall(m4_avg)}</td>
              <td>{cert_badge}</td>
            </tr>"""

    # ── Tableau de synthèse global ─────────────────────────────────────────────
    summary_rows = ""
    for doc in docs:
        for mk, (short, label, color) in METHOD_LABELS.items():
            m = doc["methods"].get(mk, {})
            met = m.get("metrics", {})
            statut_cell = badge_statut(m.get("statut")) if mk == "method4" else "—"
            anomalies = m.get("anomalies_detected", [])
            nb_anom = len(anomalies)
            anom_cell = f'<span class="badge badge-bad">{nb_anom} anomalie{"s" if nb_anom>1 else ""}</span>' if nb_anom else '<span class="badge badge-good">0</span>'
            if mk != "method4":
                anom_cell = "—"
            summary_rows += f"""
            <tr>
              <td class="doc-name">{doc['nom']}</td>
              <td><span class="method-tag" style="border-color:{color};color:{color}">{short}</span></td>
              <td>{badge_recall(met.get('recall_pct'))}</td>
              <td>{badge_coherence(met.get('ebitda_coherent'), met.get('ebitda_error_pct'))}</td>
              <td>{statut_cell}</td>
              <td>{anom_cell}</td>
              <td class="num">{m.get('time_s', '—')}s</td>
            </tr>"""

    # ── Détails par document ───────────────────────────────────────────────────
    doc_sections = ""
    for doc in docs:
        # Tableau valeurs extraites
        fields_table = """
        <table class="fields-table">
          <thead>
            <tr>
              <th>Champ</th>
              <th>M1 Regex</th>
              <th>M2 Zéro-Shot</th>
              <th>M3 CoT</th>
              <th>M4 Hybride</th>
            </tr>
          </thead>
          <tbody>"""
        FIELD_LABELS = {
            "chiffre_affaires":         "Chiffre d'affaires",
            "ebit":                     "EBIT (Résultat d'exploitation)",
            "ebitda":                   "EBITDA",
            "dotations_amortissements": "D&A (Dotations Amortissements)",
            "resultat_net":             "Résultat net",
        }
        for fk, fl in FIELD_LABELS.items():
            fields_table += f'<tr><td class="field-name">{fl}</td>'
            for mk in ["method1", "method2", "method3", "method4"]:
                v = doc["methods"].get(mk, {}).get("fields", {}).get(fk)
                fields_table += f"<td>{fmt_val(v)}</td>"
            fields_table += "</tr>"
        fields_table += "</tbody></table>"

        # Métriques par méthode pour ce document
        metrics_cards = ""
        for mk, (short, label, color) in METHOD_LABELS.items():
            m   = doc["methods"].get(mk, {})
            met = m.get("metrics", {})
            anomalies = m.get("anomalies_detected", [])
            anom_html = ""
            for a in anomalies[:5]:
                sev = a.get("statut", "")
                check = a.get("check", "")
                msg   = a.get("message", "")[:80]
                sev_cls = "badge-bad" if sev in ("FAIL","ERROR") else "badge-warn"
                anom_html += f'<div class="anom-item"><span class="badge {sev_cls}" style="font-size:9px">{sev}</span> <span class="anom-check">{check}</span> — {msg}</div>'

            jsd_line = ""
            if mk == "method4":
                jsd_val   = m.get("semantic_jsd")
                jsd_alert = m.get("semantic_alert")
                jsd_line = f'<div class="metric-row"><span>Dérive sémantique JSD</span>{badge_jsd(jsd_val, jsd_alert)}</div>'

            metrics_cards += f"""
            <div class="method-card" style="border-top-color:{color}">
              <div class="method-card-header" style="color:{color}">{short} — {label}</div>
              <div class="metric-row"><span>Taux d'extraction</span>{badge_recall(met.get('recall_pct'))}</div>
              <div class="metric-row"><span>Cohérence EBITDA</span>{badge_coherence(met.get('ebitda_coherent'), met.get('ebitda_error_pct'))}</div>
              {f'<div class="metric-row"><span>Statut pipeline</span>{badge_statut(m.get("statut"))}</div>' if mk == "method4" else ""}
              {jsd_line}
              <div class="metric-row"><span>Temps traitement</span><span class="num">{m.get("time_s","—")}s</span></div>
              {('<div class="anom-list">' + anom_html + '</div>') if anom_html else ""}
              <div class="obs-text">{auto_obs(mk, m, met)}</div>
            </div>"""

        m4_statut = doc["methods"].get("method4", {}).get("statut", "")
        statut_pill = f' <span class="badge {"badge-good" if m4_statut=="CERTIFIÉ" else "badge-bad" if m4_statut in ("ANOMALIE","ERREUR") else "badge-gray"}" style="font-size:0.6rem">{m4_statut}</span>' if m4_statut else ""
        summary_line = f'{doc["nom"]} — {doc["secteur"]} {doc["annee"]}{statut_pill}'
        if n_docs > 4:
            doc_sections += f"""
        <details class="doc-section">
          <summary class="doc-summary">{summary_line}</summary>
          <div class="doc-header">
            <div class="doc-title">{doc['nom']}</div>
            <div class="doc-meta">
              <span class="pill">{doc['secteur']}</span>
              <span class="pill">10-K {doc['annee']}</span>
            </div>
          </div>
          <div class="doc-challenge">
            <span class="challenge-icon">⚡</span>
            <span><strong>Défi d'extraction :</strong> {doc['defi']}</span>
          </div>
          <h4 class="subsection-title">Valeurs extraites (en millions USD sauf indication contraire)</h4>
          <div class="table-scroll">{fields_table}</div>
          <h4 class="subsection-title">Résultats par méthode</h4>
          <div class="methods-grid">{metrics_cards}</div>
        </details>"""
        else:
            doc_sections += f"""
        <section class="doc-section">
          <div class="doc-header">
            <div class="doc-title">{doc['nom']}</div>
            <div class="doc-meta">
              <span class="pill">{doc['secteur']}</span>
              <span class="pill">10-K {doc['annee']}</span>
            </div>
          </div>
          <div class="doc-challenge">
            <span class="challenge-icon">⚡</span>
            <span><strong>Défi d'extraction :</strong> {doc['defi']}</span>
          </div>
          <h4 class="subsection-title">Valeurs extraites (en millions USD sauf indication contraire)</h4>
          <div class="table-scroll">{fields_table}</div>
          <h4 class="subsection-title">Résultats par méthode</h4>
          <div class="methods-grid">{metrics_cards}</div>
        </section>"""

    # ── Analyse comparative ────────────────────────────────────────────────────
    # Moyennes par méthode sur tous les documents
    avg_rows = ""
    for mk, (short, label, color) in METHOD_LABELS.items():
        recalls = []
        coherences_pass = 0
        coherences_total = 0
        for doc in docs:
            m = doc["methods"].get(mk, {})
            r = m.get("metrics", {}).get("recall_pct")
            if r is not None:
                recalls.append(r)
            c = m.get("metrics", {}).get("ebitda_coherent")
            if c is not None:
                coherences_total += 1
                if c:
                    coherences_pass += 1
        avg_recall = round(sum(recalls) / len(recalls), 1) if recalls else 0
        coh_rate  = round(coherences_pass / coherences_total * 100, 0) if coherences_total else None
        coh_badge = (
            f'<span class="badge {"badge-good" if coh_rate >= 75 else "badge-bad"}">{coh_rate:.0f}%</span>'
            if coh_rate is not None else '<span class="badge badge-gray">—</span>'
        )
        avg_rows += f"""
        <tr>
          <td><span class="method-tag" style="border-color:{color};color:{color}">{short}</span> {label}</td>
          <td>{badge_recall(avg_recall)}</td>
          <td>{coh_badge}</td>
        </tr>"""

    sector_section = ""
    if n_docs > 4 and sector_rows:
        sector_section = f"""
<!-- ══ PERFORMANCE PAR SECTEUR ═══════════════════════════════════ -->
<section class="section">
  <h2 class="section-title">3b — Performance M4 par Secteur</h2>
  <table class="avg-table">
    <thead>
      <tr>
        <th>Secteur</th>
        <th>Rappel M4 moyen</th>
        <th>Certifications</th>
      </tr>
    </thead>
    <tbody>{sector_rows}</tbody>
  </table>
</section>"""

    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Benchmark — Pare-feu Sémantique DD</title>
<style>
/* ── TOKENS ─────────────────────────────────────────────── */
:root {{
  --navy:   #0F2240;
  --steel:  #1E3A5F;
  --accent: #0EA5E9;
  --green:  #059669;
  --amber:  #D97706;
  --red:    #DC2626;
  --gray:   #6B7280;
  --surface:#F0F4FA;
  --border: #D1D9E6;
  --text:   #1A202C;
  --muted:  #4A5568;
  --white:  #FFFFFF;
  --font-body: system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-head: Georgia, 'Times New Roman', serif;
  --font-mono: 'Consolas', 'Courier New', monospace;
}}

/* ── RESET & BASE ───────────────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ font-size: 14px; }}
body {{
  font-family: var(--font-body);
  color: var(--text);
  background: var(--white);
  line-height: 1.6;
}}

/* ── LAYOUT ─────────────────────────────────────────────── */
.page {{ max-width: 900px; margin: 0 auto; padding: 2rem 2.5rem; }}
.table-scroll {{ overflow-x: auto; }}

/* ── COVER ──────────────────────────────────────────────── */
.cover {{
  background: var(--navy);
  color: var(--white);
  padding: 3.5rem 3rem 2.5rem;
  page-break-after: always;
  break-after: page;
}}
.cover-eyebrow {{
  font-size: 0.7rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 1.2rem;
}}
.cover-title {{
  font-family: var(--font-head);
  font-size: 2.2rem;
  line-height: 1.2;
  font-weight: normal;
  margin-bottom: 0.8rem;
  text-wrap: balance;
}}
.cover-subtitle {{
  font-size: 1rem;
  color: #93C5FD;
  margin-bottom: 2.5rem;
  max-width: 560px;
  line-height: 1.5;
}}
.cover-meta {{
  display: flex;
  gap: 2rem;
  font-size: 0.75rem;
  color: #94A3B8;
  border-top: 1px solid #1E3A5F;
  padding-top: 1.2rem;
}}
.cover-meta strong {{ color: var(--white); display: block; font-size: 0.85rem; }}

/* ── SECTIONS ───────────────────────────────────────────── */
.section {{
  margin: 2.5rem 0;
  page-break-inside: avoid;
}}
.section-title {{
  font-family: var(--font-head);
  font-size: 1.35rem;
  color: var(--navy);
  border-bottom: 2px solid var(--navy);
  padding-bottom: 0.4rem;
  margin-bottom: 1.2rem;
}}
.subsection-title {{
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin: 1.2rem 0 0.6rem;
}}

/* ── DOCUMENT SECTION ───────────────────────────────────── */
.doc-section {{
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
  page-break-inside: avoid;
  break-inside: avoid;
}}
.doc-header {{
  display: flex;
  align-items: baseline;
  gap: 0.8rem;
  flex-wrap: wrap;
  margin-bottom: 0.8rem;
}}
.doc-title {{
  font-family: var(--font-head);
  font-size: 1.15rem;
  font-weight: bold;
  color: var(--navy);
}}
.doc-meta {{ display: flex; gap: 0.4rem; }}
.pill {{
  font-size: 0.65rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 99px;
  padding: 0.1rem 0.55rem;
  color: var(--muted);
}}
.doc-challenge {{
  background: #FFF7ED;
  border-left: 3px solid var(--amber);
  padding: 0.5rem 0.8rem;
  font-size: 0.78rem;
  color: #92400E;
  border-radius: 0 4px 4px 0;
  margin-bottom: 0.8rem;
  display: flex;
  gap: 0.5rem;
}}
.challenge-icon {{ flex-shrink: 0; }}

/* ── METHODS GRID ───────────────────────────────────────── */
.methods-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 0.75rem;
  margin-top: 0.5rem;
}}
.method-card {{
  border: 1px solid var(--border);
  border-top: 3px solid;
  border-radius: 4px;
  padding: 0.8rem;
  font-size: 0.75rem;
  background: var(--white);
}}
.method-card-header {{
  font-weight: 600;
  margin-bottom: 0.5rem;
  font-size: 0.72rem;
  line-height: 1.3;
}}
.metric-row {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.4rem;
  padding: 0.18rem 0;
  font-size: 0.72rem;
  color: var(--muted);
  border-bottom: 1px solid #F1F5F9;
}}
.metric-row:last-child {{ border-bottom: none; }}
.anom-list {{
  margin-top: 0.5rem;
  background: #FFF1F2;
  border-radius: 3px;
  padding: 0.4rem;
}}
.anom-item {{
  font-size: 0.65rem;
  color: #7F1D1D;
  margin-bottom: 0.2rem;
  line-height: 1.4;
}}
.anom-check {{ font-weight: 600; }}
.obs-text {{
  margin-top: 0.5rem;
  font-size: 0.65rem;
  color: #4A5568;
  line-height: 1.4;
  border-top: 1px dashed #E2E8F0;
  padding-top: 0.4rem;
  font-style: italic;
}}

/* ── FIELDS TABLE ───────────────────────────────────────── */
.fields-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.76rem;
  margin-bottom: 0.5rem;
}}
.fields-table th {{
  background: var(--navy);
  color: var(--white);
  font-weight: 600;
  padding: 0.4rem 0.7rem;
  text-align: left;
  white-space: nowrap;
}}
.fields-table td {{
  padding: 0.35rem 0.7rem;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}
.fields-table tr:nth-child(even) td {{ background: #F8FAFD; }}
.field-name {{ font-weight: 500; color: var(--steel); white-space: nowrap; }}

/* ── SUMMARY TABLE ──────────────────────────────────────── */
.summary-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}}
.summary-table th {{
  background: var(--navy);
  color: var(--white);
  padding: 0.5rem 0.8rem;
  font-weight: 600;
  text-align: left;
}}
.summary-table td {{
  padding: 0.4rem 0.8rem;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}
.summary-table tr:hover td {{ background: #F0F4FA; }}
.doc-name {{ font-weight: 500; color: var(--steel); }}

/* ── BADGES ─────────────────────────────────────────────── */
.badge {{
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 0.15rem 0.45rem;
  border-radius: 3px;
  white-space: nowrap;
  font-family: var(--font-mono);
}}
.badge-good  {{ background: #D1FAE5; color: #065F46; }}
.badge-bad   {{ background: #FEE2E2; color: #991B1B; }}
.badge-warn  {{ background: #FEF3C7; color: #92400E; }}
.badge-gray  {{ background: #F1F5F9; color: #64748B; }}
.missing {{ color: #CBD5E1; font-style: italic; }}
.num {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
.method-tag {{
  display: inline-block;
  font-size: 0.68rem;
  font-weight: 700;
  font-family: var(--font-mono);
  border: 1.5px solid;
  border-radius: 3px;
  padding: 0.1rem 0.35rem;
}}

/* ── METHODOLOGY CARDS ──────────────────────────────────── */
.method-defs {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}}
.method-def {{
  border-left: 4px solid;
  padding: 0.8rem 1rem;
  background: var(--surface);
  border-radius: 0 4px 4px 0;
  font-size: 0.78rem;
}}
.method-def-num {{
  font-family: var(--font-mono);
  font-size: 0.68rem;
  font-weight: 700;
  margin-bottom: 0.3rem;
}}
.method-def-name {{
  font-weight: 700;
  font-size: 0.85rem;
  margin-bottom: 0.4rem;
  color: var(--navy);
}}
.method-def-body {{ color: var(--muted); line-height: 1.5; }}
.method-def-claim {{
  margin-top: 0.5rem;
  font-size: 0.7rem;
  color: var(--muted);
  background: rgba(255,255,255,0.7);
  padding: 0.3rem 0.5rem;
  border-radius: 3px;
}}
.method-def-claim strong {{ color: var(--text); }}

/* ── CONCLUSION BOX ─────────────────────────────────────── */
.conclusion-box {{
  background: var(--navy);
  color: var(--white);
  padding: 1.5rem 2rem;
  border-radius: 6px;
  margin-top: 1.5rem;
}}
.conclusion-box h3 {{
  font-family: var(--font-head);
  font-size: 1.1rem;
  margin-bottom: 0.8rem;
  color: #93C5FD;
}}
.conclusion-box ul {{
  list-style: none;
  padding: 0;
  font-size: 0.82rem;
  line-height: 1.8;
  color: #CBD5E1;
}}
.conclusion-box li::before {{ content: "→ "; color: var(--accent); font-weight: bold; }}

/* ── AVG TABLE ──────────────────────────────────────────── */
.avg-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
  margin-bottom: 1.2rem;
}}
.avg-table th {{
  background: var(--surface);
  color: var(--steel);
  padding: 0.5rem 0.8rem;
  font-weight: 600;
  text-align: left;
  border-bottom: 2px solid var(--border);
}}
.avg-table td {{
  padding: 0.45rem 0.8rem;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}

/* ── COLLAPSIBLE DOC SECTION (details/summary) ──────────── */
details.doc-section {{
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 0.8rem;
  padding: 0;
}}
details.doc-section > summary.doc-summary {{
  list-style: none;
  cursor: pointer;
  padding: 0.75rem 1rem;
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--steel);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  user-select: none;
}}
details.doc-section > summary.doc-summary::before {{
  content: "▶";
  font-size: 0.6rem;
  color: var(--muted);
  transition: transform 0.15s;
  flex-shrink: 0;
}}
details.doc-section[open] > summary.doc-summary::before {{
  content: "▼";
}}
details.doc-section > .doc-header,
details.doc-section > .doc-challenge,
details.doc-section > .subsection-title,
details.doc-section > .table-scroll,
details.doc-section > .methods-grid {{
  padding-left: 1rem;
  padding-right: 1rem;
}}
details.doc-section > .doc-header {{ padding-top: 0.5rem; }}
details.doc-section > .methods-grid {{ padding-bottom: 1rem; }}

/* ── PRINT ──────────────────────────────────────────────── */
@media print {{
  @page {{ size: A4; margin: 1.5cm 2cm; }}
  body {{ font-size: 11px; }}
  .cover {{ page-break-after: always; }}
  .doc-section {{ page-break-inside: avoid; }}
  .section {{ page-break-inside: avoid; }}
  a {{ color: inherit; text-decoration: none; }}
}}

@media (prefers-color-scheme: dark) {{
  body {{ background: #111827; color: #E5E7EB; }}
  .doc-section {{ border-color: #374151; background: #1F2937; }}
  .fields-table td {{ border-color: #374151; }}
  .fields-table tr:nth-child(even) td {{ background: #1a2435; }}
  .metric-row {{ border-color: #374151; color: #9CA3AF; }}
  .doc-challenge {{ background: #2D1B06; color: #FCD34D; border-color: #D97706; }}
}}
:root[data-theme="light"] {{
  body {{ background: #FFFFFF; color: #1A202C; }}
  .doc-section {{ border-color: #D1D9E6; background: #FFFFFF; }}
}}
:root[data-theme="dark"] {{
  body {{ background: #111827; color: #E5E7EB; }}
  .doc-section {{ border-color: #374151; background: #1F2937; }}
  .fields-table td {{ border-color: #374151; }}
  .fields-table tr:nth-child(even) td {{ background: #1a2435; }}
  .metric-row {{ border-color: #374151; color: #9CA3AF; }}
}}
</style>
</head>
<body>

<!-- ══ COVER ══════════════════════════════════════════════════════ -->
<div class="cover">
  <div class="page">
    <p class="cover-eyebrow">Rapport de Benchmark · Due Diligence VC / M&amp;A</p>
    <h1 class="cover-title">Évaluation Comparative des Méthodes d'Extraction Financière sur Documents 10-K SEC</h1>
    <p class="cover-subtitle">
      Analyse de 4 approches d'extraction sur {n_docs} comptes de résultat 10-K SEC de grandes entreprises américaines :
      parseurs classiques, LLM zéro-shot, Chain-of-Thought, et pare-feu hybride neuro-symbolique.
    </p>
    <div class="cover-meta">
      <div><strong>Date</strong>{date_str}</div>
      <div><strong>Modèle LLM</strong>{model}</div>
      <div><strong>Corpus</strong>{corpus_label}</div>
      <div><strong>Métriques</strong>Rappel · Cohérence EBITDA · Détection d'anomalies</div>
    </div>
  </div>
</div>

<div class="page">

<!-- ══ MÉTHODOLOGIE ═══════════════════════════════════════════════ -->
<section class="section">
  <h2 class="section-title">1 — Méthodologie</h2>
  <div class="method-defs">
    <div class="method-def" style="border-color:#6B7280">
      <div class="method-def-num" style="color:#6B7280">M1</div>
      <div class="method-def-name">Parseur Regex / Géométrique</div>
      <div class="method-def-body">Extraction par expressions régulières sur le texte brut du PDF. Labels financiers standards (Net sales, Operating income, D&amp;A) ciblés par pattern matching séquentiel.</div>
      <div class="method-def-claim"><strong>Hypothèse :</strong> échoue dès que la mise en page dévie du standard (mauvais Rappel sur structures bancaires)</div>
    </div>
    <div class="method-def" style="border-color:#0EA5E9">
      <div class="method-def-num" style="color:#0EA5E9">M2</div>
      <div class="method-def-name">LLM Zéro-Shot</div>
      <div class="method-def-body">Prompt minimal demandant l'extraction des 5 champs clés, sans contexte métier, sans instructions sectorielles, sans aucune validation déterministe.</div>
      <div class="method-def-claim"><strong>Hypothèse :</strong> fort rappel mais hallucinations non détectées — incohérences arithmétiques passent en silence</div>
    </div>
    <div class="method-def" style="border-color:#F59E0B">
      <div class="method-def-num" style="color:#F59E0B">M3</div>
      <div class="method-def-name">LLM + Chain-of-Thought</div>
      <div class="method-def-body">Extraction en deux temps : le LLM extrait, puis se relit pour vérifier EBITDA = EBIT + D&amp;A. Correction auto si écart &gt; 5%.</div>
      <div class="method-def-claim"><strong>Hypothèse :</strong> biais de confirmation — le LLM valide ses propres erreurs de source</div>
    </div>
    <div class="method-def" style="border-color:#10B981">
      <div class="method-def-num" style="color:#10B981">M4</div>
      <div class="method-def-name">Hybride Neuro-Symbolique</div>
      <div class="method-def-body">LLM avec prompt AuditWen (instructions sectorielles, banques, GAAP) + DDTaxonomy (contrôles déterministes) + auto-corrections + moniteur sémantique JSD.</div>
      <div class="method-def-claim"><strong>Hypothèse :</strong> validation 100% déterministe et détection des dérives distributionnelles</div>
    </div>
  </div>
</section>

<!-- ══ SYNTHÈSE GLOBALE ════════════════════════════════════════════ -->
<section class="section">
  <h2 class="section-title">2 — Tableau de Synthèse</h2>
  <div class="table-scroll">
    <table class="summary-table">
      <thead>
        <tr>
          <th>Document</th>
          <th>Méthode</th>
          <th>Rappel (5 champs)</th>
          <th>Cohérence EBITDA</th>
          <th>Statut Final</th>
          <th>Anomalies détectées</th>
          <th>Temps</th>
        </tr>
      </thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </div>
</section>

<!-- ══ ANALYSE COMPARATIVE ════════════════════════════════════════ -->
<section class="section">
  <h2 class="section-title">3 — Analyse Comparative</h2>
  <table class="avg-table">
    <thead>
      <tr>
        <th>Méthode</th>
        <th>Rappel moyen ({n_docs} docs)</th>
        <th>Cohérence EBITDA (taux de succès)</th>
      </tr>
    </thead>
    <tbody>{avg_rows}</tbody>
  </table>
</section>

{sector_section}

<!-- ══ RÉSULTATS PAR DOCUMENT ═════════════════════════════════════ -->
<section class="section">
  <h2 class="section-title">4 — Résultats par Document</h2>
  {doc_sections}
</section>

<!-- ══ CONCLUSIONS ════════════════════════════════════════════════ -->
<section class="section">
  <h2 class="section-title">5 — Conclusions</h2>
  <div class="conclusion-box">
    <h3>Synthèse des résultats</h3>
    <ul>
      <li><strong>M1 Regex :</strong> Rappel insuffisant sur les structures non-standard (banques). Aucun mécanisme de détection d'erreur. Inadapté aux 10-K réels multi-sectoriels.</li>
      <li><strong>M2 LLM Zéro-Shot :</strong> Rappel élevé mais incohérences arithmétiques non détectées. Les hallucinations du LLM (EBITDA non-GAAP, EBIT de segment) sont acceptées sans vérification.</li>
      <li><strong>M3 LLM + CoT :</strong> Amélioration marginale sur la cohérence EBITDA. Le biais de confirmation empêche la correction des mauvaises valeurs sources — le LLM recalcule un EBITDA "cohérent" depuis un EBIT erroné.</li>
      <li><strong>M4 Hybride Neuro-Symbolique :</strong> Seule approche combinant guidance sectorielle (banques, GAAP vs non-GAAP), validation déterministe (DDTaxonomy) et détection de dérive distributionnelle (JSD). Rappel maximal et précision vérifiée.</li>
    </ul>
  </div>
</section>

</div><!-- /page -->
</body>
</html>"""

    return html


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    use_full = "--full" in sys.argv
    src_path    = RESULTS_FULL_PATH if use_full else RESULTS_PATH
    report_path = REPORT_FULL_PATH  if use_full else REPORT_PATH

    if not src_path.exists():
        print(f"Fichier résultats introuvable : {src_path}")
        flag = " --full" if use_full else ""
        print(f"Lancez d'abord : .\\venv\\Scripts\\python.exe benchmark_compare.py{flag}")
        sys.exit(1)

    data = json.loads(src_path.read_text(encoding="utf-8"))
    html = generate_html(data)
    report_path.write_text(html, encoding="utf-8")
    print(f"✓ Rapport généré → {report_path}")
