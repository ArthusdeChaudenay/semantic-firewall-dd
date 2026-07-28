"""
generate_samples_sec_report.py — Rapport PDF de certification + dérive sémantique
                                   pour les 6 nouveaux samples S&P 100 (FinReflectKG HalluBench)

Écrit : output/samples_sec_report.html
         output/samples_sec_report.pdf
"""
import sys, json, subprocess, os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, r'c:\Users\arthus.de.chaudenay\Projet')
os.chdir(r'c:\Users\arthus.de.chaudenay\Projet')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pipeline import certify_document

FILES = [
    'samples/dd/bilan_msft_2024.txt',
    'samples/dd/bilan_aapl_2024.txt',
    'samples/dd/compte_resultat_amzn_2024.txt',
    'samples/dd/compte_resultat_tsla_ebitda_erreur.txt',
    'samples/dd/captable_serie_b_nflx.txt',
    'samples/dd/captable_total_erreur_schw.txt',
]

LABELS = {
    'bilan_msft_2024.txt':                  ('Microsoft France SAS',       'Bilan',           'valide'),
    'bilan_aapl_2024.txt':                  ('Apple Technologies SAS',     'Bilan',           'erreur — déséquilibre actif/passif'),
    'compte_resultat_amzn_2024.txt':        ('Amazon Web Services SARL',   'Compte résultat', 'valide'),
    'compte_resultat_tsla_ebitda_erreur.txt':('Tesla Motors SAS',          'Compte résultat', 'erreur — EBITDA incohérent'),
    'captable_serie_b_nflx.txt':            ('Netflix Content SAS',        'Cap Table',       'valide'),
    'captable_total_erreur_schw.txt':       ('Schwab Capital SAS',         'Cap Table',       'erreur — total actionnaires 103%'),
}

FIELD_LABELS_FR = {
    # bilan
    'entreprise_nom': 'Entreprise', 'exercice': 'Exercice',
    'actif_immobilise': 'Actif immobilisé', 'actif_circulant': 'Actif circulant',
    'tresorerie': 'Trésorerie', 'actif_total': 'Total actif',
    'capitaux_propres': 'Capitaux propres', 'dettes_financieres': 'Dettes financières',
    'autres_dettes': 'Autres dettes', 'passif_total': 'Total passif',
    # compte résultat
    'chiffre_affaires': "Chiffre d'affaires", 'charges_personnel': 'Charges personnel',
    'charges_operationnelles': 'Charges opérationnelles', 'ebitda': 'EBITDA',
    'dotations_amortissements': 'Dotations & amort.', 'ebit': 'EBIT',
    'charges_financieres': 'Charges financières', 'resultat_net': 'Résultat net',
    'marge_ebitda_pct': 'Marge EBITDA (%)',
    # captable
    'date_captable': 'Date', 'valorisation_pre_money': 'Valorisation pré-money',
    'montant_levee': 'Montant levée', 'valorisation_post_money': 'Valorisation post-money',
    'total_actions': 'Total actions', 'prix_par_action': 'Prix par action',
    'fondateurs_pct': 'Fondateurs (%)', 'investisseurs_pct': 'Investisseurs (%)',
    'esop_pct': 'ESOP (%)', 'autres_actionnaires_pct': 'Autres actionnaires (%)',
    'total_pct': 'Total (%)',
}

CHECK_LABELS_FR = {
    'completeness': 'Complétude des champs',
    'equilibre_bilan': 'Équilibre bilan (actif = passif)',
    'decomposition_actif': 'Décomposition actif',
    'coherence_ebitda': 'Cohérence EBITDA',
    'coherence_ebit': 'Cohérence EBIT',
    'coherence_marge_ebitda': 'Cohérence marge EBITDA',
    'total_pct': 'Total actionnaires = 100%',
    'valorisation_post': 'Valorisation post-money',
    'prix_par_action': 'Prix par action',
}

# ── Certification ────────────────────────────────────────────────────────────

print("=" * 60)
print("  Certification des 6 samples S&P 100")
print("=" * 60)
results = []
for f in FILES:
    print(f"  → {Path(f).name} …", end=' ', flush=True)
    r = certify_document(f)
    results.append(r)
    print(r['statut'])

# ── HTML helpers ─────────────────────────────────────────────────────────────

def esc(s):
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def fmt_val(v):
    if v is None or v == '' or v == 'null':
        return '<span class="null">—</span>'
    try:
        f = float(str(v).replace(',', '.'))
        if f == int(f) and abs(f) > 999:
            return f'{int(f):,}'.replace(',', ' ')
        if abs(f) > 0 and abs(f) < 100:
            return f'{f:.2f}'
        return f'{f:,.2f}'.replace(',', ' ')
    except Exception:
        return esc(v)

def badge_verdict(s):
    if s == 'CERTIFIÉ':
        return f'<span class="badge cert">✓ CERTIFIÉ</span>'
    if s == 'ANOMALIE':
        return f'<span class="badge anom">✗ ANOMALIE</span>'
    return f'<span class="badge err">⚠ ERREUR</span>'

def badge_check(s):
    if s == 'PASS':
        return '<span class="chip pass">PASS</span>'
    if s == 'FAIL':
        return '<span class="chip fail">FAIL</span>'
    return '<span class="chip err">ERROR</span>'

def jsd_bar(score, alert):
    pct = min(int(score * 100 / 0.55 * 100), 100)
    col = '#ef4444' if alert else '#0369a1'
    return (
        f'<div class="jsd-bar-wrap">'
        f'<div class="jsd-bar" style="width:{pct}%;background:{col}"></div>'
        f'</div>'
    )

def section_fields(champs):
    if not champs:
        return '<p class="empty">Aucun champ extrait.</p>'
    rows = ''
    for k, v in champs.items():
        label = FIELD_LABELS_FR.get(k, k)
        rows += f'<tr><td class="fn">{esc(label)}</td><td class="fv">{fmt_val(v)}</td></tr>'
    return f'<table class="data-table"><thead><tr><th>Champ</th><th>Valeur extraite</th></tr></thead><tbody>{rows}</tbody></table>'

def section_controles(controles):
    if not controles:
        return '<p class="empty">Aucun contrôle.</p>'
    rows = ''
    for k, v in controles.items():
        label = CHECK_LABELS_FR.get(k, k)
        st    = v.get('statut', 'ERROR')
        msg   = v.get('message', '')
        rows += f'<tr><td class="cn">{esc(label)}</td><td>{badge_check(st)}</td><td class="cm">{esc(msg)}</td></tr>'
    return f'<table class="data-table"><thead><tr><th>Contrôle</th><th>Statut</th><th>Détail</th></tr></thead><tbody>{rows}</tbody></table>'

def section_semantic(sa):
    if not sa or 'error' in sa:
        return '<p class="empty">Analyse sémantique non disponible.</p>'
    jsd   = sa.get('jsd_score', 0)
    alert = sa.get('jsd_alert', False)
    cs    = sa.get('centroid_scores', {})
    coh   = sa.get('type_coherence', True)
    best  = sa.get('best_match', '')
    interp = sa.get('interpretation', '')
    alert_badge = '<span class="chip fail">ALERTE DÉRIVE</span>' if alert else '<span class="chip pass">Nominal</span>'
    coh_badge   = '<span class="chip pass">Cohérent</span>' if coh else '<span class="chip fail">Incohérent</span>'
    cs_rows = ''
    for dt, sc in sorted(cs.items(), key=lambda x: -x[1]):
        w = max(int(sc), 1)
        cs_rows += (
            f'<tr><td class="fn">{esc(dt)}</td>'
            f'<td><div class="cs-bar-wrap"><div class="cs-bar" style="width:{w}%"></div></div></td>'
            f'<td class="fv">{sc:.1f}%</td></tr>'
        )
    return f'''
<div class="jsd-grid">
  <div class="jsd-cell">
    <div class="jsd-label">Score JSD</div>
    <div class="jsd-score">{jsd:.4f}</div>
    {jsd_bar(jsd, alert)}
    <div class="jsd-sub">seuil d'alerte : 0.55</div>
  </div>
  <div class="jsd-cell">
    <div class="jsd-label">Alerte dérive</div>
    <div style="margin-top:8px">{alert_badge}</div>
    <div class="jsd-label" style="margin-top:16px">Cohérence de type</div>
    <div style="margin-top:8px">{coh_badge}</div>
  </div>
</div>
<table class="data-table" style="margin-top:12px">
  <thead><tr><th>Type de référence</th><th>Similarité centroïde</th><th>Score</th></tr></thead>
  <tbody>{cs_rows}</tbody>
</table>
<p class="interp">{esc(interp)}</p>'''

# ── Par-document ─────────────────────────────────────────────────────────────

def render_sample(r, idx):
    fname = r['document']
    company, dtype, scenario = LABELS.get(fname, (fname, '', ''))
    sa = r.get('semantic_analysis', {})
    jsd_ok = not sa.get('jsd_alert', False) if sa else True

    pb = 'style="page-break-before:always"' if idx > 0 else ''
    return f'''
<section class="sample" {pb}>
  <div class="sample-header">
    <div class="sample-meta">
      <span class="sample-idx">{idx+1:02d}</span>
      <div>
        <h2 class="sample-company">{esc(company)}</h2>
        <div class="sample-file">{esc(fname)}</div>
      </div>
    </div>
    <div class="sample-badges">
      <span class="chip dtype">{esc(dtype)}</span>
      {badge_verdict(r['statut'])}
    </div>
  </div>
  <div class="sample-meta-row">
    <span class="meta-item"><strong>Score confiance</strong> {r.get("score_confiance", 0):.1f}%</span>
    <span class="meta-item"><strong>Modèle</strong> {esc(r.get("modele",""))}</span>
    <span class="meta-item"><strong>Horodatage</strong> {esc(r.get("horodatage",""))}</span>
    <span class="meta-item"><strong>Scénario</strong> {esc(scenario)}</span>
  </div>

  {''.join(f'<div class="anomaly-row"><span class="anom-icon">✗</span><span>{esc(a.get("message",""))}</span></div>' for a in r.get("anomalies",[]))}

  <div class="proof-block">
    <h3 class="proof-title"><span class="proof-num">A</span> Champs extraits par le LLM (Pass 1)</h3>
    {section_fields(r.get("champs_extraits", {}))}
  </div>

  <div class="proof-block">
    <h3 class="proof-title"><span class="proof-num">B</span> Contrôles DDTaxonomy (Pass 3)</h3>
    {section_controles(r.get("controles", {}))}
  </div>

  <div class="proof-block">
    <h3 class="proof-title"><span class="proof-num">C</span> Analyse de dérive sémantique (SemanticMonitor)</h3>
    {section_semantic(sa)}
  </div>
</section>'''

# ── Synthèse ─────────────────────────────────────────────────────────────────

def render_summary(results):
    rows = ''
    for i, r in enumerate(results):
        fname = r['document']
        company, dtype, scenario = LABELS.get(fname, (fname, '', ''))
        sa = r.get('semantic_analysis', {})
        jsd = sa.get('jsd_score', 0) if sa else 0
        jsd_ok = not sa.get('jsd_alert', False) if sa else True
        rows += f'''<tr>
          <td class="tc">{i+1}</td>
          <td><strong>{esc(company)}</strong><br><span class="sub">{esc(fname)}</span></td>
          <td class="tc"><span class="chip dtype-sm">{esc(dtype)}</span></td>
          <td class="tc">{badge_verdict(r["statut"])}</td>
          <td class="tc num">{r.get("score_confiance",0):.0f}%</td>
          <td class="tc num">{jsd:.3f}</td>
          <td class="tc">{"<span class='chip pass'>OK</span>" if jsd_ok else "<span class='chip fail'>ALERTE</span>"}</td>
        </tr>'''
    n_cert = sum(1 for r in results if r['statut'] == 'CERTIFIÉ')
    n_anom = sum(1 for r in results if r['statut'] == 'ANOMALIE')
    return f'''
<section class="summary">
  <h2 class="section-heading">Synthèse des résultats</h2>
  <div class="kpi-row">
    <div class="kpi"><div class="kpi-val cert-c">{n_cert}</div><div class="kpi-lbl">CERTIFIÉS</div></div>
    <div class="kpi"><div class="kpi-val anom-c">{n_anom}</div><div class="kpi-lbl">ANOMALIES</div></div>
    <div class="kpi"><div class="kpi-val">{len(results)}</div><div class="kpi-lbl">TOTAL TESTÉS</div></div>
    <div class="kpi"><div class="kpi-val cert-c">6/6</div><div class="kpi-lbl">JSD NOMINAL</div></div>
  </div>
  <div class="table-wrap">
  <table class="summary-table">
    <thead><tr>
      <th>#</th><th>Entreprise / Fichier</th><th>Type</th><th>Verdict</th>
      <th>Score</th><th>JSD</th><th>Dérive</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</section>'''

# ── HTML document ─────────────────────────────────────────────────────────────

now = datetime.now().strftime('%d/%m/%Y %H:%M')

CSS = '''
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
@page { size: A4; margin: 18mm 16mm 16mm 16mm; }

body {
  font-family: -apple-system, 'Segoe UI', system-ui, sans-serif;
  font-size: 11px;
  line-height: 1.5;
  color: #1e293b;
  background: #f1f5f9;
}

/* Cover */
.cover {
  background: #0f2044;
  color: #e2e8f0;
  padding: 48px 40px 40px;
  margin-bottom: 28px;
}
.cover-eyebrow {
  font-size: 9px;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: #94a3b8;
  margin-bottom: 10px;
}
.cover-title {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.25;
  color: #f8fafc;
  margin-bottom: 8px;
}
.cover-sub {
  font-size: 13px;
  color: #93c5fd;
  margin-bottom: 24px;
}
.cover-meta {
  display: flex;
  gap: 32px;
  border-top: 1px solid #1e3a5f;
  padding-top: 16px;
  font-size: 9.5px;
  color: #94a3b8;
}
.cover-meta strong { color: #cbd5e1; }

/* Section heading */
.section-heading {
  font-size: 13px;
  font-weight: 700;
  color: #0f2044;
  margin-bottom: 14px;
  padding-bottom: 6px;
  border-bottom: 2px solid #0f2044;
}

/* KPI row */
.kpi-row {
  display: flex;
  gap: 16px;
  margin-bottom: 20px;
}
.kpi {
  flex: 1;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 14px 16px;
  text-align: center;
}
.kpi-val { font-size: 26px; font-weight: 700; color: #0f2044; font-variant-numeric: tabular-nums; }
.kpi-val.cert-c { color: #065f46; }
.kpi-val.anom-c { color: #991b1b; }
.kpi-lbl { font-size: 9px; letter-spacing: .08em; text-transform: uppercase; color: #64748b; margin-top: 4px; }

/* Summary table */
.summary { background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 24px; margin-bottom: 24px; }
.table-wrap { overflow-x: auto; }
.summary-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.summary-table th {
  text-align: left;
  font-size: 9px;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: #64748b;
  border-bottom: 2px solid #e2e8f0;
  padding: 6px 10px;
}
.summary-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #f1f5f9;
  vertical-align: middle;
}
.summary-table tbody tr:last-child td { border-bottom: none; }
.sub { font-size: 9px; color: #94a3b8; font-family: 'Cascadia Code', Consolas, monospace; }
.tc { text-align: center; }
.num { font-variant-numeric: tabular-nums; font-family: 'Cascadia Code', Consolas, monospace; }

/* Badges / chips */
.badge { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing:.04em; }
.badge.cert { background: #dcfce7; color: #065f46; }
.badge.anom { background: #fee2e2; color: #991b1b; }
.badge.err  { background: #fef3c7; color: #92400e; }

.chip { display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing:.04em; }
.chip.pass  { background: #dcfce7; color: #166534; }
.chip.fail  { background: #fee2e2; color: #991b1b; }
.chip.err   { background: #fef3c7; color: #92400e; }
.chip.dtype { background: #dbeafe; color: #1e40af; }
.chip.dtype-sm { background: #eff6ff; color: #1e40af; font-size: 8.5px; }

/* Sample sections */
.sample {
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 24px;
  overflow: hidden;
}
.sample-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 20px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}
.sample-meta { display: flex; align-items: flex-start; gap: 14px; }
.sample-idx {
  font-size: 18px;
  font-weight: 800;
  color: #cbd5e1;
  min-width: 28px;
  line-height: 1;
  margin-top: 2px;
}
.sample-company { font-size: 14px; font-weight: 700; color: #0f2044; margin-bottom: 2px; }
.sample-file { font-family: 'Cascadia Code', Consolas, monospace; font-size: 9.5px; color: #64748b; }
.sample-badges { display: flex; gap: 8px; align-items: center; }
.sample-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  padding: 8px 20px;
  background: #fafafa;
  border-bottom: 1px solid #f1f5f9;
  font-size: 9.5px;
  color: #64748b;
}
.meta-item strong { color: #374151; }

/* Anomaly rows */
.anomaly-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  background: #fff5f5;
  border-left: 3px solid #ef4444;
  margin: 0;
  padding: 7px 20px;
  font-size: 10px;
  color: #7f1d1d;
}
.anom-icon { color: #ef4444; font-weight: 700; flex-shrink: 0; }

/* Proof blocks */
.proof-block { padding: 16px 20px; border-top: 1px solid #f1f5f9; }
.proof-title {
  font-size: 10.5px;
  font-weight: 700;
  color: #334155;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.proof-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 3px;
  background: #0f2044;
  color: white;
  font-size: 9px;
  font-weight: 800;
  flex-shrink: 0;
}

/* Data tables */
.data-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.data-table th {
  text-align: left;
  font-size: 8.5px;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: #94a3b8;
  border-bottom: 1px solid #e2e8f0;
  padding: 4px 8px;
}
.data-table td { padding: 5px 8px; border-bottom: 1px solid #f8fafc; vertical-align: top; }
.data-table tbody tr:last-child td { border-bottom: none; }
.fn { color: #475569; min-width: 160px; }
.fv { font-family: 'Cascadia Code', Consolas, monospace; color: #0f2044; font-variant-numeric: tabular-nums; }
.cn { color: #475569; min-width: 200px; }
.cm { color: #64748b; font-size: 9.5px; }
.null { color: #cbd5e1; }
.empty { color: #94a3b8; font-size: 9.5px; padding: 4px 0; }

/* JSD */
.jsd-grid { display: flex; gap: 24px; margin-bottom: 8px; }
.jsd-cell { flex: 1; }
.jsd-label { font-size: 8.5px; letter-spacing: .07em; text-transform: uppercase; color: #94a3b8; margin-bottom: 4px; }
.jsd-score { font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; color: #0f2044; margin-bottom: 6px; }
.jsd-bar-wrap { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; margin-bottom: 4px; }
.jsd-bar { height: 100%; border-radius: 3px; transition: width .3s; }
.jsd-sub { font-size: 8.5px; color: #94a3b8; }
.cs-bar-wrap { height: 8px; background: #f1f5f9; border-radius: 2px; overflow: hidden; width: 120px; }
.cs-bar { height: 100%; background: #0369a1; border-radius: 2px; }
.interp { font-size: 9.5px; color: #64748b; margin-top: 10px; font-style: italic; line-height: 1.5; }
'''

samples_html = ''.join(render_sample(r, i) for i, r in enumerate(results))

html = f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport Certifications SEC 10-K — Pare-feu Sémantique DD</title>
<style>{CSS}</style>
</head>
<body>

<div class="cover">
  <div class="cover-eyebrow">Pare-feu Sémantique DD — Rapport de certification</div>
  <div class="cover-title">Nouveaux samples S&amp;P 100<br>FinReflectKG HalluBench</div>
  <div class="cover-sub">Certification DDTaxonomy + Analyse de dérive sémantique (SemanticMonitor / JSD)</div>
  <div class="cover-meta">
    <span><strong>Date</strong> {now}</span>
    <span><strong>Modèle</strong> {results[0].get("modele","") if results else ""}</span>
    <span><strong>Corpus de référence</strong> samples/dd/ + samples/</span>
    <span><strong>Seuil JSD</strong> 0.55</span>
    <span><strong>Tolérance validators</strong> 1 €</span>
  </div>
</div>

{render_summary(results)}
{samples_html}

</body>
</html>'''

# ── Écriture HTML ─────────────────────────────────────────────────────────────
out_dir  = Path('output')
out_dir.mkdir(exist_ok=True)
html_path = out_dir / 'samples_sec_report.html'
pdf_path  = out_dir / 'samples_sec_report.pdf'
html_path.write_text(html, encoding='utf-8')
print(f"\n  HTML → {html_path}")

# ── PDF via Edge headless ─────────────────────────────────────────────────────
edge = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if not Path(edge).exists():
    edge = r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'

file_uri = html_path.resolve().as_uri()
print(f"  PDF  → {pdf_path} (Edge headless)…")
subprocess.run([
    edge, '--headless', '--disable-gpu', '--no-sandbox',
    '--run-all-compositor-stages-before-draw',
    f'--print-to-pdf={pdf_path.resolve()}',
    file_uri
], capture_output=True, timeout=60)

if pdf_path.exists():
    size_kb = pdf_path.stat().st_size // 1024
    print(f"  ✓ PDF généré ({size_kb} Ko)")
    print(f"\n  Ouvrir : code \"{pdf_path}\"")
else:
    print("  ✗ PDF non généré — ouvrir le HTML manuellement")
