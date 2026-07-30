"""
dd_generate_report.py — Dashboard HTML + PDF dédié aux dossiers de Due Diligence.

Vue consolidée par type de document :
  BILAN           → barres empilées Actif / Passif, indicateur d'équilibre
  P&L             → cascade CA → EBITDA → EBIT → Résultat Net
  CAP TABLE       → donut CSS ownership + valorisation pre/post
  ANOMALIES       → liste complète avec niveau de criticité
  ANALYSE ML      → JSD + centroïdes TF-IDF

Lit  : output/dossiers/<uuid>.json  (produit par pipeline.certify_dossier)
Écrit: output/dossiers/<uuid>.html
       output/dossiers/<uuid>.pdf   (via Edge headless)

Lance avec :
    .\\venv\\Scripts\\python.exe dd_generate_report.py output/dossiers/<uuid>.json
    .\\venv\\Scripts\\python.exe dd_generate_report.py   # rapport le plus récent
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from semantic_firewall.config import JSD_ALERT_THRESHOLD

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# UTILITAIRES
# ──────────────────────────────────────────────────────────────────────────────

def _safe_str(v, default: str = "") -> str:
    return str(v) if v is not None else default


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def _safe_section(fn, *args) -> str:
    try:
        return fn(*args)
    except Exception as exc:
        return (
            f'<div style="background:#1e293b;border-left:3px solid #f43f5e;'
            f'border-radius:6px;padding:12px 16px;margin:12px 0;font-size:.82rem;color:#f87171">'
            f'Erreur de rendu — {exc}</div>'
        )


def _badge(statut: str) -> str:
    statut = _safe_str(statut, "—")
    COLORS = {
        "CERTIFIÉ":  "#10b981", "PASS": "#10b981",
        "VIGILANCE": "#8b5cf6",
        "ANOMALIE":  "#f59e0b", "FAIL": "#f43f5e",
        "ERREUR":    "#ef4444", "ERROR": "#ef4444",
    }
    bg = COLORS.get(statut, "#64748b")
    return (
        f'<span style="background:{bg};color:#fff;padding:3px 10px;'
        f'border-radius:5px;font-size:.72rem;font-weight:700;letter-spacing:.04em">'
        f'{statut}</span>'
    )


def _score_color(v) -> str:
    f = _safe_float(v, default=-1.0)
    if f < 0:   return "#94a3b8"
    if f >= 90: return "#10b981"
    if f >= 70: return "#f59e0b"
    return "#f43f5e"


def _fmt_eur(v, default: str = "—") -> str:
    try:
        f = _safe_float(v, default=None)
        if f is None or (f == 0.0 and v is None):
            return default
        if abs(f) >= 1_000_000:
            return f"{f / 1_000_000:.2f}M €"
        if abs(f) >= 1_000:
            return f"{f / 1_000:.0f}K €"
        return f"{f:,.0f} €"
    except Exception:
        return default


def _fmt_pct(v, default: str = "—") -> str:
    try:
        f = _safe_float(v, default=None)
        return f"{f:.1f}%" if f is not None else default
    except Exception:
        return default


def _pct_of(num, denom, default: float = 0.0) -> float:
    """Retourne num/denom*100, borné [0, 100]."""
    d = _safe_float(denom)
    if d == 0:
        return default
    return max(0.0, min(100.0, _safe_float(num) / d * 100))


# ──────────────────────────────────────────────────────────────────────────────
# CSS — screen + print
# ──────────────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }
.container { max-width: 980px; margin: 0 auto; padding: 40px 24px; }

/* HEADERS */
h1 { font-size: 1.7rem; font-weight: 700; color: #f1f5f9; margin-bottom: 4px; }
h2 { font-size: .85rem; font-weight: 600; color: #94a3b8; margin: 36px 0 14px;
     text-transform: uppercase; letter-spacing: .1em; }
.section-title {
  display: flex; align-items: center; gap: 10px;
  font-size: .8rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: .1em; color: #94a3b8; margin: 36px 0 14px;
}
.section-title::before {
  content: ''; display: block; width: 4px; height: 18px;
  border-radius: 2px; flex-shrink: 0;
}
.section-bilan::before   { background: #3b82f6; }
.section-pl::before      { background: #10b981; }
.section-captable::before{ background: #8b5cf6; }
.section-anomalies::before { background: #f43f5e; }
.section-semantic::before  { background: #6366f1; }
.section-controls::before  { background: #64748b; }

.subtitle { font-size: .85rem; color: #64748b; margin-bottom: 24px; }

/* VERDICT BANNER */
.verdict-banner {
  border-radius: 10px; padding: 14px 20px; margin-bottom: 28px;
  display: flex; align-items: center; gap: 14px; font-size: .9rem;
}
.verdict-ok    { background: #052e16; border: 1px solid #10b981; color: #34d399; }
.verdict-warn  { background: #422006; border: 1px solid #f59e0b; color: #fbbf24; }
.verdict-error { background: #1c0a0a; border: 1px solid #ef4444; color: #f87171; }
.verdict-icon  { font-size: 1.5rem; }

/* KPI GRID */
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 28px; }
.kpi { background: #1e293b; border-radius: 10px; padding: 16px 14px; }
.kpi .label { font-size: .68rem; color: #64748b; text-transform: uppercase;
              letter-spacing: .06em; margin-bottom: 6px; }
.kpi .value { font-size: 1.6rem; font-weight: 700; }

/* DD CARDS */
.dd-card {
  background: #1e293b; border-radius: 10px; padding: 18px 20px;
  margin: 10px 0; break-inside: avoid;
}
.dd-card.has-anomaly   { border-left: 3px solid #f59e0b; }
.dd-card.has-error     { border-left: 3px solid #ef4444; }
.dd-card.has-vigilance { border-left: 3px solid #8b5cf6; }
.card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px; flex-wrap: wrap; gap: 8px;
}
.card-doc-name { font-weight: 600; color: #f1f5f9; font-size: .9rem; }
.card-meta     { font-size: .78rem; color: #64748b; }
.card-score    { font-size: .85rem; font-weight: 700; }

/* BILAN STACKED BARS */
.bilan-grid { display: grid; grid-template-columns: 1fr 60px 1fr; gap: 12px;
              align-items: center; }
.bar-wrap   { display: flex; flex-direction: column; gap: 6px; }
.bar-label  { font-size: .72rem; color: #94a3b8; margin-bottom: 2px; }
.bar-total  { font-size: .9rem; font-weight: 700; color: #f1f5f9; }
.bar-stack  { display: flex; height: 20px; border-radius: 4px; overflow: hidden;
              background: #0f172a; }
.seg-immo   { background: #1d4ed8; }
.seg-circ   { background: #3b82f6; }
.seg-treso  { background: #93c5fd; }
.seg-cp     { background: #059669; }
.seg-df     { background: #d97706; }
.seg-ad     { background: #f87171; }
.bar-legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.leg-item   { display: flex; align-items: center; gap: 4px;
              font-size: .68rem; color: #94a3b8; }
.leg-dot    { width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.eq-center  { text-align: center; font-size: .75rem; font-weight: 700; }
.eq-ok      { color: #10b981; }
.eq-ko      { color: #f43f5e; }

/* P&L CASCADE */
.pl-table  { width: 100%; font-size: .82rem; }
.pl-row    { display: flex; align-items: center; gap: 8px;
             padding: 4px 0; border-bottom: 1px solid #0f172a; }
.pl-row:last-child { border-bottom: none; }
.pl-lbl    { width: 220px; flex-shrink: 0; color: #94a3b8; }
.pl-lbl.highlight { color: #f1f5f9; font-weight: 600; }
.pl-bar-bg { flex: 1; height: 12px; background: #0f172a; border-radius: 3px;
             overflow: hidden; }
.pl-bar    { height: 100%; border-radius: 3px; }
.pl-val    { width: 90px; text-align: right; color: #e2e8f0; flex-shrink: 0; }
.pl-pct    { width: 54px; text-align: right; color: #64748b; font-size: .75rem;
             flex-shrink: 0; }
.pl-pct.anomaly { color: #f59e0b; font-weight: 700; }
.pl-divider { border-top: 1px solid #334155; margin: 4px 0; }

/* CAPTABLE DONUT */
.captable-grid { display: grid; grid-template-columns: 120px 1fr 1fr; gap: 20px;
                 align-items: center; }
.donut-wrap { position: relative; width: 108px; height: 108px; flex-shrink: 0; }
.donut      { width: 108px; height: 108px; border-radius: 50%; }
.donut-hole { position: absolute; inset: 28%; border-radius: 50%;
              background: #0f172a; display: flex; align-items: center;
              justify-content: center; font-size: .68rem; color: #64748b;
              font-weight: 700; }
.cap-legend { display: flex; flex-direction: column; gap: 7px; }
.cap-leg-row{ display: flex; align-items: center; gap: 8px; font-size: .8rem; }
.cap-leg-dot{ width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.cap-leg-lbl{ color: #94a3b8; flex: 1; }
.cap-leg-val{ font-weight: 700; color: #f1f5f9; }
.val-metrics{ display: flex; flex-direction: column; gap: 8px; }
.val-row    { display: flex; justify-content: space-between; font-size: .82rem;
              padding: 6px 10px; background: #0f172a; border-radius: 6px; }
.val-row .lbl { color: #64748b; }
.val-row .amt { font-weight: 700; color: #f1f5f9; }
.val-post .amt{ color: #8b5cf6; }

/* ANOMALY BOX */
.anomaly-box { background: #1e293b; border-left: 3px solid #f43f5e; border-radius: 6px;
               padding: 11px 14px; margin: 5px 0; font-size: .82rem; }
.anomaly-box.warn { border-left-color: #f59e0b; }
.anomaly-box .check { font-weight: 600; color: #f87171; margin-bottom: 2px; }
.anomaly-box.warn .check { color: #fbbf24; }
.anomaly-box .msg   { color: #94a3b8; }

/* CONTROLS TABLE */
table { width: 100%; border-collapse: collapse; background: #1e293b;
        border-radius: 10px; overflow: hidden; font-size: .82rem; }
th { background: #0f172a; color: #64748b; text-align: left; padding: 9px 14px;
     font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; }
td { padding: 10px 14px; border-top: 1px solid #0f172a; vertical-align: top;
     color: #e2e8f0; }
tr:hover td { background: #263344; }

/* SEMANTIC */
.jsd-bar { height: 12px; border-radius: 3px; }

.footer { text-align: center; color: #334155; font-size: .72rem;
          margin-top: 48px; padding-top: 20px; border-top: 1px solid #1e293b; }

/* ── PRINT ─────────────────────────────────────────────────── */
@media print {
  body              { background: #fff !important; color: #1e293b !important; }
  h1                { color: #0f172a !important; }
  h2, .section-title{ color: #475569 !important; }
  .subtitle         { color: #64748b !important; }
  .verdict-banner   { background: #f8fafc !important; border-color: #cbd5e1 !important;
                      color: #1e293b !important; }
  .kpi              { background: #f8fafc !important; border: 1px solid #e2e8f0 !important; }
  .kpi .label       { color: #64748b !important; }
  .kpi .value       { color: #0f172a !important; }
  .dd-card          { background: #f8fafc !important; border: 1px solid #e2e8f0 !important; }
  .bar-stack, .pl-bar-bg { background: #e2e8f0 !important; }
  .donut-hole       { background: #f8fafc !important; }
  .anomaly-box      { background: #fff5f5 !important; }
  .anomaly-box .check { color: #dc2626 !important; }
  .anomaly-box .msg   { color: #475569 !important; }
  .val-row          { background: #f1f5f9 !important; }
  .val-row .lbl     { color: #475569 !important; }
  .val-row .amt     { color: #0f172a !important; }
  table             { background: #fff !important; border: 1px solid #e2e8f0 !important; }
  th                { background: #f1f5f9 !important; color: #475569 !important; }
  td                { border-top-color: #e2e8f0 !important; color: #1e293b !important; }
  tr:hover td       { background: transparent !important; }
  .footer           { color: #94a3b8 !important; border-top-color: #e2e8f0 !important; }
  .no-print         { display: none !important; }
  .card-meta        { color: #475569 !important; }
  .pl-lbl           { color: #475569 !important; }
  .pl-lbl.highlight { color: #0f172a !important; }
  .pl-pct           { color: #475569 !important; }
  .bar-label        { color: #475569 !important; }
  .bar-total        { color: #0f172a !important; }
  .leg-item         { color: #475569 !important; }
  .cap-leg-lbl      { color: #475569 !important; }
  .cap-leg-val      { color: #0f172a !important; }
}
"""


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS DE RENDU
# ──────────────────────────────────────────────────────────────────────────────

def _certs_by_type(rapport: dict) -> dict:
    """Regroupe les certifications par doc_type."""
    out: dict = {}
    for cert in (rapport.get("certifications") or []):
        if not isinstance(cert, dict):
            continue
        t = cert.get("doc_type") or "inconnu"
        out.setdefault(t, []).append(cert)
    return out


def _card_class(cert: dict) -> str:
    s = cert.get("statut", "")
    if s == "ERREUR":    return "dd-card has-error"
    if s == "ANOMALIE":  return "dd-card has-anomaly"
    if s == "VIGILANCE": return "dd-card has-vigilance"
    return "dd-card"


def _card_header(cert: dict) -> str:
    doc   = _safe_str(cert.get("document"), "—")
    ent   = cert.get("champs_extraits", {}).get("entreprise_nom") or ""
    ex    = cert.get("champs_extraits", {}).get("exercice") or ""
    meta  = " — ".join(x for x in [ent, ex] if x)
    score = cert.get("score_confiance")
    score_disp  = f"{_safe_float(score):.0f}%" if score is not None else "—"
    score_color = _score_color(score)
    return f"""
    <div class="card-header">
      <div>
        <div class="card-doc-name">{doc}</div>
        <div class="card-meta">{meta}</div>
      </div>
      <div style="display:flex;align-items:center;gap:12px">
        {_badge(cert.get("statut","—"))}
        <span class="card-score" style="color:{score_color}">{score_disp}</span>
      </div>
    </div>"""


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : EN-TÊTE
# ──────────────────────────────────────────────────────────────────────────────

def _header(rapport: dict) -> str:
    ent     = rapport.get("entreprise_nom") or "Dossier sans nom"
    ts      = _safe_str(rapport.get("horodatage", ""))[:10]
    modele  = _safe_str(rapport.get("modele", ""))
    did     = _safe_str(rapport.get("dossier_id", ""))
    did_s   = (did[:8] + "…") if len(did) >= 8 else did or "—"
    verdict = _safe_str(rapport.get("verdict", ""))
    nb_ano  = int(rapport.get("nb_anomalies", 0) or 0)
    nb_err  = int(rapport.get("nb_erreurs", 0) or 0)

    if verdict == "CERTIFIÉ":
        cls, icon, msg = "verdict-ok", "✓", "Dossier certifié conforme — aucune anomalie détectée"
    elif verdict == "VIGILANCE":
        cls, icon, msg = "verdict-warn", "⚠", "Dérive sémantique détectée — documents certifiés, vigilance recommandée"
    elif nb_err > 0:
        cls, icon, msg = "verdict-error", "✗", f"{nb_err} erreur(s) d'analyse — documents non reconnus ou LLM indisponible"
    else:
        cls, icon, msg = "verdict-warn", "⚠", f"{nb_ano} anomalie(s) détectée(s) — vérification requise"

    return f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
      <div>
        <h1>Rapport de Due Diligence</h1>
        <p class="subtitle">{ent} &nbsp;·&nbsp; Audit du {ts} &nbsp;·&nbsp; {modele} &nbsp;·&nbsp; ID {did_s}</p>
      </div>
    </div>
    <div class="verdict-banner {cls}">
      <span class="verdict-icon">{icon}</span>
      <span>{msg}</span>
    </div>"""


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : KPIs
# ──────────────────────────────────────────────────────────────────────────────

def _kpis(rapport: dict) -> str:
    n    = int(rapport.get("nb_documents", 0) or 0)
    ok   = int(rapport.get("nb_certifies", 0) or 0)
    vig  = int(rapport.get("nb_vigilance", 0) or 0)
    ano  = int(rapport.get("nb_anomalies", 0) or 0)
    err  = int(rapport.get("nb_erreurs", 0) or 0)

    certs_valid = [c for c in (rapport.get("certifications") or [])
                   if isinstance(c, dict) and c.get("statut") not in ("ERREUR", None)]
    if certs_valid:
        s_avg = sum(_safe_float(c.get("score_confiance")) for c in certs_valid) / len(certs_valid)
        s_str = f"{s_avg:.0f}%"
        s_col = _score_color(s_avg)
    else:
        s_str, s_col = "N/A", "#94a3b8"

    by_type = _certs_by_type(rapport)
    nb_bilan = len(by_type.get("bilan", []))
    nb_pl    = len(by_type.get("compte_resultat", []))
    nb_cap   = len(by_type.get("captable", []))
    type_info = f"{nb_bilan} bilan · {nb_pl} P&amp;L · {nb_cap} captable"

    def kpi(label, value, color="#f1f5f9", sub=""):
        sub_html = f'<div style="font-size:.65rem;color:#475569;margin-top:3px">{sub}</div>' if sub else ""
        return (f'<div class="kpi"><div class="label">{label}</div>'
                f'<div class="value" style="color:{color}">{value}</div>{sub_html}</div>')

    return f"""<h2>Synthèse</h2>
    <div class="kpi-grid">
      {kpi("Documents", n, sub=type_info)}
      {kpi("Certifiés", ok, "#10b981")}
      {kpi("Vigilance", vig, "#8b5cf6" if vig else "#64748b")}
      {kpi("Anomalies", ano, "#f59e0b" if ano else "#10b981")}
      {kpi("Erreurs LLM", err, "#ef4444" if err else "#64748b")}
      {kpi("Score moyen", s_str, s_col)}
    </div>"""


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : BILAN
# ──────────────────────────────────────────────────────────────────────────────

def _bilan_section(rapport: dict) -> str:
    bilans = _certs_by_type(rapport).get("bilan", [])
    if not bilans:
        return ""

    cards = ""
    for cert in bilans:
        ch = cert.get("champs_extraits") or {}
        at = _safe_float(ch.get("actif_total"))
        pt = _safe_float(ch.get("passif_total"))
        ref = max(at, pt, 1.0)

        # ACTIF segments (% of ref)
        immo = _safe_float(ch.get("actif_immobilise"))
        circ = _safe_float(ch.get("actif_circulant"))
        tres = _safe_float(ch.get("tresorerie"))
        immo_pct = immo / ref * 100
        circ_pct = circ / ref * 100
        tres_pct = tres / ref * 100

        # PASSIF segments
        cp   = _safe_float(ch.get("capitaux_propres"))
        df   = _safe_float(ch.get("dettes_financieres"))
        ad   = _safe_float(ch.get("autres_dettes"))
        cp_pct = cp / ref * 100
        df_pct = df / ref * 100
        ad_pct = ad / ref * 100

        # Passif bar width = pt/ref*100
        passif_width = min(100, pt / ref * 100) if ref > 0 else 0

        actif_bar = (
            f'<div class="bar-stack" style="width:{min(100,at/ref*100):.1f}%">'
            f'<div class="seg-immo" style="width:{immo_pct/(at/ref) if at else 0:.1f}%"></div>'
            f'<div class="seg-circ" style="width:{circ_pct/(at/ref) if at else 0:.1f}%"></div>'
            f'<div class="seg-treso" style="width:{tres_pct/(at/ref) if at else 0:.1f}%"></div>'
            f'</div>' if at > 0 else
            '<div style="font-size:.72rem;color:#64748b">—</div>'
        )

        passif_bar = (
            f'<div class="bar-stack" style="width:{passif_width:.1f}%">'
            f'<div class="seg-cp" style="width:{cp_pct/(pt/ref) if pt else 0:.1f}%"></div>'
            f'<div class="seg-df" style="width:{df_pct/(pt/ref) if pt else 0:.1f}%"></div>'
            f'<div class="seg-ad" style="width:{ad_pct/(pt/ref) if pt else 0:.1f}%"></div>'
            f'</div>' if pt > 0 else
            '<div style="font-size:.72rem;color:#64748b">—</div>'
        )

        if at and pt:
            ecart = abs(at - pt)
            if ecart < 1:
                eq_html = '<div class="eq-center eq-ok">✓ Équilibré</div>'
            else:
                eq_html = f'<div class="eq-center eq-ko">✗ Écart<br>{_fmt_eur(ecart)}</div>'
        else:
            eq_html = '<div class="eq-center" style="color:#64748b">N/A</div>'

        cards += f"""
        <div class="{_card_class(cert)}">
          {_card_header(cert)}
          <div class="bilan-grid">
            <div class="bar-wrap">
              <div class="bar-label">ACTIF TOTAL</div>
              <div class="bar-total">{_fmt_eur(at)}</div>
              <div style="margin:8px 0;background:#0f172a;border-radius:4px;padding:6px">
                {actif_bar}
              </div>
              <div class="bar-legend">
                <div class="leg-item"><div class="leg-dot" style="background:#1d4ed8"></div>Immob. {_fmt_eur(immo)}</div>
                <div class="leg-item"><div class="leg-dot" style="background:#3b82f6"></div>Circulant {_fmt_eur(circ)}</div>
                <div class="leg-item"><div class="leg-dot" style="background:#93c5fd"></div>Tréso {_fmt_eur(tres)}</div>
              </div>
            </div>
            {eq_html}
            <div class="bar-wrap">
              <div class="bar-label">PASSIF TOTAL</div>
              <div class="bar-total">{_fmt_eur(pt)}</div>
              <div style="margin:8px 0;background:#0f172a;border-radius:4px;padding:6px">
                {passif_bar}
              </div>
              <div class="bar-legend">
                <div class="leg-item"><div class="leg-dot" style="background:#059669"></div>Cap. propres {_fmt_eur(cp)}</div>
                <div class="leg-item"><div class="leg-dot" style="background:#d97706"></div>Dettes fin. {_fmt_eur(df)}</div>
                <div class="leg-item"><div class="leg-dot" style="background:#f87171"></div>Autres dettes {_fmt_eur(ad)}</div>
              </div>
            </div>
          </div>
        </div>"""

    return f'<div class="section-title section-bilan">Bilan ({len(bilans)} document(s))</div>{cards}'


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : COMPTE DE RÉSULTAT (P&L)
# ──────────────────────────────────────────────────────────────────────────────

def _pl_section(rapport: dict) -> str:
    pls = _certs_by_type(rapport).get("compte_resultat", [])
    if not pls:
        return ""

    cards = ""
    for cert in pls:
        ch   = cert.get("champs_extraits") or {}
        ca   = _safe_float(ch.get("chiffre_affaires"))
        pers = _safe_float(ch.get("charges_personnel"))
        opex = _safe_float(ch.get("charges_operationnelles"))
        ebitda = _safe_float(ch.get("ebitda"))
        da     = _safe_float(ch.get("dotations_amortissements"))
        ebit   = _safe_float(ch.get("ebit"))
        chfin  = _safe_float(ch.get("charges_financieres"))
        rn     = _safe_float(ch.get("resultat_net"))
        marge_decl = ch.get("marge_ebitda_pct")
        marge_calc = (ebitda / ca * 100) if ca > 0 else 0.0

        # Anomaly lookup
        ano_checks = {a.get("check") for a in (cert.get("anomalies") or [])}

        def bar_row(label, value, bar_color, pct_of_ca, is_key=False, check_name=""):
            pct   = max(0.0, min(100.0, pct_of_ca))
            cls   = "pl-lbl highlight" if is_key else "pl-lbl"
            warn  = " anomaly" if check_name and check_name in ano_checks else ""
            pct_s = f"{pct:.1f}%{' ⚠' if warn else ''}"
            return f"""
            <div class="pl-row">
              <div class="{cls}">{label}</div>
              <div class="pl-bar-bg">
                <div class="pl-bar" style="width:{pct:.1f}%;background:{bar_color}"></div>
              </div>
              <div class="pl-val">{_fmt_eur(value)}</div>
              <div class="pl-pct{warn}">{pct_s}</div>
            </div>"""

        cascade = ""
        if ca > 0:
            cascade += bar_row("Chiffre d'affaires", ca, "#3b82f6", 100, is_key=True)
            if pers:
                cascade += bar_row("− Charges personnel", pers, "#ef4444", pers/ca*100)
            if opex:
                cascade += bar_row("− Charges opex", opex, "#f87171", opex/ca*100)
            cascade += '<div class="pl-divider"></div>'
            marge_warn = "coherence_marge_ebitda" in ano_checks or "coherence_ebitda" in ano_checks
            cascade += bar_row("EBITDA", ebitda, "#10b981", ebitda/ca*100, is_key=True,
                                check_name="coherence_ebitda")
            if da:
                cascade += bar_row("− D&A", da, "#94a3b8", da/ca*100)
            cascade += '<div class="pl-divider"></div>'
            cascade += bar_row("EBIT", ebit, "#6366f1", ebit/ca*100, is_key=True,
                                check_name="coherence_ebit")
            if chfin:
                cascade += bar_row("− Charges financières", chfin, "#f87171", chfin/ca*100)
            cascade += '<div class="pl-divider"></div>'
            cascade += bar_row("Résultat net", rn, "#8b5cf6", rn/ca*100 if rn else 0, is_key=True)

            # Marge EBITDA row
            marge_col = "#f59e0b" if marge_warn else "#64748b"
            marge_disp = (_fmt_pct(marge_decl) + " déclaré / " + f"{marge_calc:.1f}% calculé"
                          if marge_decl else f"{marge_calc:.1f}% calculé")
            cascade += f"""
            <div class="pl-row" style="margin-top:6px">
              <div class="pl-lbl" style="color:{marge_col}">Marge EBITDA</div>
              <div class="pl-bar-bg"></div>
              <div class="pl-val" style="color:{marge_col}"></div>
              <div class="pl-pct" style="color:{marge_col};width:160px">{marge_disp}</div>
            </div>"""
        else:
            cascade = '<p style="font-size:.8rem;color:#64748b">CA non extrait.</p>'

        cards += f"""
        <div class="{_card_class(cert)}">
          {_card_header(cert)}
          <div class="pl-table">{cascade}</div>
        </div>"""

    return f'<div class="section-title section-pl">Compte de résultat ({len(pls)} document(s))</div>{cards}'


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : CAP TABLE
# ──────────────────────────────────────────────────────────────────────────────

def _captable_section(rapport: dict) -> str:
    captables = _certs_by_type(rapport).get("captable", [])
    if not captables:
        return ""

    CAP_COLORS = {
        "fondateurs":    "#6366f1",
        "investisseurs": "#10b981",
        "esop":          "#f59e0b",
        "autres":        "#94a3b8",
    }

    cards = ""
    for cert in captables:
        ch = cert.get("champs_extraits") or {}

        fond = _safe_float(ch.get("fondateurs_pct"))
        inv  = _safe_float(ch.get("investisseurs_pct"))
        esop = _safe_float(ch.get("esop_pct"))
        autr = _safe_float(ch.get("autres_actionnaires_pct"))
        total_pct = _safe_float(ch.get("total_pct"))

        pre    = ch.get("valorisation_pre_money")
        levee  = ch.get("montant_levee")
        post   = ch.get("valorisation_post_money")
        prix   = ch.get("prix_par_action")
        actions= ch.get("total_actions")
        date_ct= ch.get("date_captable") or "—"

        # Donut via conic-gradient
        slices = [
            ("Fondateurs",    fond, "#6366f1"),
            ("Investisseurs", inv,  "#10b981"),
            ("ESOP",          esop, "#f59e0b"),
            ("Autres",        autr, "#94a3b8"),
        ]
        total_shown = sum(v for _, v, _ in slices)
        stops, cum = [], 0.0
        for _, val, col in slices:
            if val > 0:
                end = cum + (val / total_shown * 100 if total_shown > 0 else 0)
                stops.append(f"{col} {cum:.1f}% {end:.1f}%")
                cum = end
        conic = f"conic-gradient({', '.join(stops)})" if stops else "#334155"

        total_col = "#10b981" if abs(total_pct - 100) < 0.1 else "#f43f5e"

        legend_rows = "".join(
            f'<div class="cap-leg-row"><div class="cap-leg-dot" style="background:{col}"></div>'
            f'<div class="cap-leg-lbl">{lbl}</div>'
            f'<div class="cap-leg-val">{_fmt_pct(val)}</div></div>'
            for lbl, val, col in slices if val > 0
        )

        ano_checks = {a.get("check") for a in (cert.get("anomalies") or [])}
        vpost_warn = "valorisation_post" in ano_checks
        prix_warn  = "prix_par_action"   in ano_checks
        total_warn = "total_pct"         in ano_checks

        def val_row(lbl, amt, warn=False, extra_cls=""):
            col = "#f59e0b" if warn else ""
            col_style = f'style="color:{col}"' if col else ""
            return (f'<div class="val-row {extra_cls}">'
                    f'<span class="lbl">{lbl}</span>'
                    f'<span class="amt" {col_style}>{amt}{"  ⚠" if warn else ""}</span></div>')

        cards += f"""
        <div class="{_card_class(cert)}">
          {_card_header(cert)}
          <div style="font-size:.72rem;color:#64748b;margin-bottom:12px">Date : {date_ct}</div>
          <div class="captable-grid">
            <div class="donut-wrap">
              <div class="donut" style="background:{conic}"></div>
              <div class="donut-hole"
                   style="color:{'#f43f5e' if total_warn else '#64748b'}">
                {_fmt_pct(total_pct)}
              </div>
            </div>
            <div class="cap-legend">{legend_rows}</div>
            <div class="val-metrics">
              {val_row("Pré-money",   _fmt_eur(pre))}
              {val_row("+ Levée",     _fmt_eur(levee))}
              {val_row("Post-money",  _fmt_eur(post), warn=vpost_warn, extra_cls="val-post")}
              {val_row("Prix / action", _fmt_eur(prix), warn=prix_warn)}
              {val_row("Total actions", f"{int(_safe_float(actions)):,}".replace(",", " ") if actions else "—")}
            </div>
          </div>
        </div>"""

    return f'<div class="section-title section-captable">Cap Table ({len(captables)} document(s))</div>{cards}'


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : ANOMALIES
# ──────────────────────────────────────────────────────────────────────────────

def _anomalies_section(rapport: dict) -> str:
    all_ano = rapport.get("anomalies") or []
    if not all_ano:
        return (
            '<div class="section-title section-anomalies">Anomalies</div>'
            '<p style="color:#10b981;font-size:.85rem;padding:4px 0">'
            '✓ Aucune anomalie détectée — dossier certifié conforme.</p>'
        )

    CRITICITE = {
        "equilibre_bilan":       ("Critique",  "warn"),
        "decomposition_actif":   ("Haute",     "warn"),
        "coherence_ebitda":      ("Critique",  ""),
        "coherence_ebit":        ("Haute",     ""),
        "coherence_marge_ebitda":("Moyenne",   "warn"),
        "total_pct":             ("Critique",  ""),
        "valorisation_post":     ("Critique",  ""),
        "prix_par_action":       ("Haute",     ""),
        "completeness":              ("Moyenne",          "warn"),
        "transcription_divergence":  ("Critique — LLM",  ""),
        "extraction":                ("Erreur LLM",       ""),
    }

    boxes = ""
    for a in all_ano:
        if not isinstance(a, dict):
            continue
        check = _safe_str(a.get("check"), "—")
        msg   = _safe_str(a.get("message"), "")
        doc   = _safe_str(a.get("document"), "")
        stat  = _safe_str(a.get("statut"), "—")
        crit, cls_extra = CRITICITE.get(check, ("—", "warn"))
        boxes += f"""<div class="anomaly-box {cls_extra}">
          <div class="check">[{crit}] {check} — {doc}</div>
          <div class="msg">{msg}</div>
        </div>"""

    return f'<div class="section-title section-anomalies">Anomalies détectées ({len(all_ano)})</div>{boxes}'


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : ANALYSE SÉMANTIQUE ML
# ──────────────────────────────────────────────────────────────────────────────

def _semantic_section(rapport: dict) -> str:
    certs = [c for c in (rapport.get("certifications") or [])
             if isinstance(c, dict) and c.get("semantic_analysis")]
    if not certs:
        return ""

    blocks = ""
    for cert in certs:
        sa = cert.get("semantic_analysis") or {}
        if not isinstance(sa, dict) or "error" in sa:
            continue

        doc   = _safe_str(cert.get("document"), "—")
        jsd       = _safe_float(sa.get("jsd_score"), 0.0)
        # D7: fall back to the single source of truth, never a retyped literal.
        jsd_thr   = _safe_float(sa.get("jsd_threshold"), JSD_ALERT_THRESHOLD)
        jsd_pct   = round(jsd * 100, 1)
        jsd_thr_pct = round(jsd_thr * 100, 1)
        jsd_alert = bool(sa.get("jsd_alert", False))
        jsd_color = "#f43f5e" if jsd_alert else "#10b981"
        jsd_w     = min(100, jsd_pct * 2)

        coh_ok    = bool(sa.get("type_coherence", True))
        coh_color = "#10b981" if coh_ok else "#f59e0b"
        coh_lbl   = "Cohérent" if coh_ok else "Incohérence de type"
        interp    = _safe_str(sa.get("interpretation"), "")

        scores = {k: _safe_float(v) for k, v in (sa.get("centroid_scores") or {}).items()}
        best   = _safe_str(sa.get("best_match"), "")
        bars   = ""
        for dt, score in sorted(scores.items(), key=lambda x: -x[1]):
            bw = min(100, score)
            is_best   = (dt == best)
            bar_col   = "#6366f1" if is_best else "#334155"
            lbl_col   = "#e2e8f0" if is_best else "#64748b"
            lbl_w     = "700" if is_best else "400"
            bars += f"""
            <div style="margin:4px 0;display:flex;align-items:center;gap:8px">
              <span style="width:110px;font-size:.72rem;color:{lbl_col};text-align:right">{dt}</span>
              <div style="flex:1;background:#0f172a;border-radius:3px;height:9px;overflow:hidden">
                <div style="width:{bw}%;height:100%;background:{bar_col};border-radius:3px"></div>
              </div>
              <span style="width:40px;font-size:.72rem;color:{lbl_col};font-weight:{lbl_w}">{score:.0f}%</span>
            </div>"""

        blocks += f"""
        <div class="dd-card" style="break-inside:avoid">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <span style="font-weight:600;color:#f1f5f9;font-size:.85rem">{doc}</span>
            <span style="font-size:.72rem;color:{coh_color}">{coh_lbl}</span>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
            <div>
              <div style="font-size:.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">JSD — Dérive sémantique</div>
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <div style="flex:1;background:#0f172a;border-radius:3px;height:12px;overflow:hidden">
                  <div class="jsd-bar" style="width:{jsd_w}%;background:{jsd_color}"></div>
                </div>
                <span style="color:{jsd_color};font-weight:700;font-size:.82rem">{jsd_pct}%</span>
              </div>
              <div style="font-size:.68rem;color:#64748b">Seuil {jsd_thr_pct}% — {'⚠ DÉRIVE DÉTECTÉE' if jsd_alert else '✓ Dans les normes'}</div>
            </div>
            <div>
              <div style="font-size:.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Centroïdes TF-IDF</div>
              {bars or '<span style="font-size:.72rem;color:#64748b">Aucun profil</span>'}
            </div>
          </div>
          <div style="margin-top:10px;font-size:.75rem;color:#475569;font-style:italic;
                      border-top:1px solid #334155;padding-top:8px">{interp}</div>
        </div>"""

    if not blocks:
        return ""

    return (
        f'<div class="section-title section-semantic">Analyse sémantique ML</div>'
        f'<p style="font-size:.78rem;color:#475569;margin-bottom:12px">'
        f'JSD = divergence Jensen-Shannon vs corpus de référence. '
        f'Centroïdes TF-IDF = similarité cosinus par type.</p>'
        f'{blocks}'
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION : DÉTAIL DES CONTRÔLES
# ──────────────────────────────────────────────────────────────────────────────

def _controls_detail(rapport: dict) -> str:
    rows = ""
    for cert in (rapport.get("certifications") or []):
        if not isinstance(cert, dict):
            continue
        controles = cert.get("controles") or {}
        if not controles:
            continue
        doc = _safe_str(cert.get("document"), "—")
        for check, res in controles.items():
            if not isinstance(res, dict):
                continue
            stat = _safe_str(res.get("statut"), "—")
            msg  = _safe_str(res.get("message"), "")
            rows += f"""<tr>
              <td style="color:#94a3b8;font-size:.78rem;max-width:180px;word-break:break-word">{doc}</td>
              <td style="font-size:.8rem">{check}</td>
              <td>{_badge(stat)}</td>
              <td style="color:#64748b;font-size:.78rem">{msg}</td>
            </tr>"""

    if not rows:
        return ""

    return f"""<div class="section-title section-controls">Détail des contrôles</div>
    <table>
      <thead><tr>
        <th>Document</th><th>Contrôle</th><th>Résultat</th><th>Message</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


# ──────────────────────────────────────────────────────────────────────────────
# GÉNÉRATION HTML + PDF
# ──────────────────────────────────────────────────────────────────────────────

def build_html(rapport: dict) -> str:
    """Construit le HTML du rapport DD à partir d'un dict (sans I/O disque)."""
    date_gen = datetime.now().strftime("%d/%m/%Y %H:%M")
    ent = rapport.get("entreprise_nom") or "Dossier"

    sections = "\n".join([
        _safe_section(_header,            rapport),
        _safe_section(_kpis,              rapport),
        _safe_section(_bilan_section,     rapport),
        _safe_section(_pl_section,        rapport),
        _safe_section(_captable_section,  rapport),
        _safe_section(_anomalies_section, rapport),
        _safe_section(_semantic_section,  rapport),
        _safe_section(_controls_detail,   rapport),
    ])

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Rapport DD — {ent}</title>
  <style>{CSS}</style>
</head>
<body>
<div class="container">
  {sections}
  <div class="footer">
    Pare-feu Sémantique DD &nbsp;·&nbsp; {_safe_str(rapport.get("modele"))}
    &nbsp;·&nbsp; Généré le {date_gen}
  </div>
</div>
</body>
</html>"""


def generate_dd_report(rapport_path: str) -> tuple:
    path = Path(rapport_path)
    try:
        rapport = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Erreur : fichier introuvable — {rapport_path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Erreur : JSON invalide dans {rapport_path} — {exc}")
        sys.exit(1)

    if not isinstance(rapport, dict):
        print(f"Erreur : contenu JSON invalide dans {rapport_path}.")
        sys.exit(1)

    html = build_html(rapport)

    html_path = path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    print(f"HTML généré : {html_path}")

    pdf_path = path.with_suffix(".pdf")
    _export_pdf(html_path, pdf_path)
    return html_path, pdf_path


def _export_pdf(html_path: Path, pdf_path: Path) -> None:
    import subprocess
    edge_candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge = next((p for p in edge_candidates if Path(p).exists()), None)
    if not edge:
        print("PDF non généré : Edge introuvable. Ouvre le HTML dans un navigateur (Ctrl+P).")
        return
    try:
        subprocess.run(
            [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--print-to-pdf={pdf_path.resolve()}",
             "--print-to-pdf-no-header",
             f"file:///{html_path.resolve()}"],
            check=True, capture_output=True, timeout=30,
        )
        print(f"PDF généré  : {pdf_path}")
    except subprocess.CalledProcessError as exc:
        print(f"PDF non généré : Edge exit {exc.returncode}.")
    except subprocess.TimeoutExpired:
        print("PDF non généré : Edge n'a pas répondu dans les 30 s.")
    except Exception as exc:
        print(f"PDF non généré : {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _latest_dossier() -> str | None:
    d = Path("output/dossiers")
    if not d.exists():
        return None
    jsons = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(jsons[0]) if jsons else None


if __name__ == "__main__":
    rapport_path = None

    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        if candidate.exists():
            rapport_path = str(candidate)
        else:
            print(f"Fichier introuvable : {sys.argv[1]}")
            print("Utilisation du rapport le plus récent...")

    if rapport_path is None:
        rapport_path = _latest_dossier()
        if rapport_path is None:
            print("Aucun rapport dans output/dossiers/. Lancez d'abord pipeline.py.")
            sys.exit(1)
        print(f"Rapport : {rapport_path}")

    generate_dd_report(rapport_path)
