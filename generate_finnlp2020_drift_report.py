"""
generate_finnlp2020_drift_report.py
Rapport PDF de detection de derive semantique (JSD) base sur le cadre theorique
de Montariol et al. (FinNLP 2020) — "Variations in Word Usage for the Financial Domain".

Dimensions testees :
  - Synchronique : variation cross-secteurs (Tech SaaS, Bale III, IFRS, Immobilier)
  - Diachronique : variation temporelle (COVID 2020)
  - Controle     : vocabulaire DD standard (baseline)

Ecrit : output/finnlp2020_drift_report.html
        output/finnlp2020_drift_report.pdf
"""
import sys, os, subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, r'c:\Users\arthus.de.chaudenay\Projet')
os.chdir(r'c:\Users\arthus.de.chaudenay\Projet')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from semantic_monitor import SemanticMonitor
from detect_doc_type import detect_doc_type

m = SemanticMonitor().fit('samples/dd', 'samples')

# (chemin, entreprise, dimension, description_drift, alert_attendue)
SAMPLES = [
    (
        'samples/finnlp2020/compte_resultat_synchronique_tech_saas.txt',
        'CloudScale SAS',
        'Synchronique — Secteur Tech SaaS',
        'ARR, MRR, NRR, churn rate, CAC, LTV, burn rate, runway : vocabulaire EN SaaS absent du corpus FR',
        True,
    ),
    (
        'samples/finnlp2020/bilan_synchronique_bale3_regulatoire.txt',
        'Credit Regional SAS',
        'Synchronique — Reglementaire Bale III',
        'CET1, RWA, LCR, NSFR, TLTRO, MREL, stress test : terminologie prudentielle post-2008',
        True,
    ),
    (
        'samples/finnlp2020/bilan_synchronique_ifrs_groupe.txt',
        'Michelin Technologies SE',
        'Synchronique — Normes IFRS',
        'Goodwill, OCI, ECL, FVOCI, IFRS 16, IAS 19, impairment : terminologie IFRS internationale',
        True,
    ),
    (
        'samples/finnlp2020/compte_resultat_synchronique_immobilier.txt',
        'Fonciere Paris Est SAS',
        'Synchronique — Secteur Immobilier',
        'Revenus locatifs, taux d\'occupation, WALB, rendement foncier, vacance : vocabulaire immobilier',
        True,
    ),
    (
        'samples/finnlp2020/bilan_diachronique_covid2020.txt',
        'Restauration Paris SAS',
        'Diachronique — Ere COVID 2020',
        'PGE, moratoire, activite partielle, resilience, click & collect : neologismes COVID',
        True,
    ),
    (
        'samples/finnlp2020/compte_resultat_controle_standard.txt',
        'Services Pro SAS',
        'Controle — Vocabulaire DD standard',
        'CA, charges personnel, EBITDA, EBIT, resultat net : vocabulaire identique au corpus de reference',
        False,
    ),
]

# ── Analyse ──────────────────────────────────────────────────────────────────

print("Analyse des samples FinNLP 2020...")
rows = []
for path, company, dimension, desc, expected_alert in SAMPLES:
    p    = Path(path)
    text = p.read_text(encoding='utf-8', errors='replace')
    doc_type = detect_doc_type(str(p), text)
    result   = m.analyze(text, doc_type)
    ok = (result['jsd_alert'] == expected_alert)
    rows.append({
        'path': str(p),
        'fname': p.name,
        'text_preview': '\n'.join(text.splitlines()[:30]),
        'company': company,
        'dimension': dimension,
        'desc': desc,
        'expected_alert': expected_alert,
        'result_ok': ok,
        'doc_type': doc_type,
        **result,
    })
    status = 'ALERTE' if result['jsd_alert'] else 'nominal'
    ok_str = 'OK' if ok else 'INATTENDU'
    print(f"  [{ok_str}] {p.name:52s} JSD={result['jsd_score']:.4f}  {status}")

# ── Helpers HTML ─────────────────────────────────────────────────────────────

def esc(s):
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def dim_badge(dimension):
    if 'Synchronique' in dimension:
        cls, abbr = 'dim-sync', 'SYNC'
    elif 'Diachronique' in dimension:
        cls, abbr = 'dim-dia', 'DIAC'
    else:
        cls, abbr = 'dim-ctrl', 'CTRL'
    return f'<span class="dim-badge {cls}">{abbr}</span>'

def verdict_badge(alert, ok):
    v_cls = 'anom' if alert else 'cert'
    v_txt = 'DERIVE DETECTEE' if alert else 'Nominal'
    p_cls = 'ok-mark' if ok else 'ko-mark'
    p_txt = 'Attendu' if ok else 'Inattendu'
    return f'<span class="badge {v_cls}">{v_txt}</span>&nbsp;<span class="badge-ok {p_cls}">{p_txt}</span>'

def jsd_bar(jsd, alert):
    pct  = round(jsd * 100, 1)
    fcls = 'jf-alert' if alert else 'jf-ok'
    return f'''<div class="jsd-scale-wrap">
      <div class="jsd-scale">
        <div class="jsd-fill {fcls}" style="width:{pct}%"></div>
        <div class="jsd-thr"></div>
      </div>
      <div class="jsd-axis">
        <span>0</span>
        <span class="thr-lbl">0.55</span>
        <span>1.0</span>
      </div>
    </div>'''

def cs_rows(cs):
    out = ''
    for dt, sc in sorted(cs.items(), key=lambda x: -x[1]):
        w = max(round(sc), 1)
        out += f'''<tr>
          <td class="cs-type">{esc(dt)}</td>
          <td><div class="cs-wrap"><div class="cs-bar" style="width:{w}%"></div></div></td>
          <td class="cs-val">{sc:.1f}%</td>
        </tr>'''
    return out

def proof_a(text_preview):
    return f'<pre class="doc-pre">{esc(text_preview)}</pre>'

def proof_b(row):
    jsd   = row['jsd_score']
    alert = row['jsd_alert']
    al_c  = 'chip fail' if alert else 'chip pass'
    al_t  = 'ALERTE DERIVE (JSD > 0.55)' if alert else 'Nominal (JSD <= 0.55)'
    co_c  = 'chip pass' if row['type_coherence'] else 'chip fail'
    co_t  = f'Type coherent : {row["best_match"]}' if row['type_coherence'] else f'Incoherent — plus proche : {row["best_match"]}'
    col   = '#dc2626' if alert else '#059669'
    return f'''<div class="jsd-grid">
  <div class="jsd-left">
    <div class="jsd-num" style="color:{col}">{jsd:.4f}</div>
    <div class="jsd-lbl">Score JSD</div>
    {jsd_bar(jsd, alert)}
    <div style="margin-top:10px; display:flex; flex-direction:column; gap:5px;">
      <span class="{al_c}">{al_t}</span>
      <span class="{co_c}">{co_t} ({row["best_match_score"]:.1f}%)</span>
    </div>
  </div>
  <div class="jsd-right">
    <div class="detail-lbl">Interpretation</div>
    <p class="interp">{esc(row.get("interpretation",""))}</p>
    <div class="detail-lbl" style="margin-top:12px">Parametres</div>
    <table class="det-table">
      <tr><td class="dk">Type detecte (filename)</td><td class="dv">{esc(row["doc_type"])}</td></tr>
      <tr><td class="dk">Seuil d'alerte</td><td class="dv">0.55 (55 %)</td></tr>
      <tr><td class="dk">Dimension (FinNLP 2020)</td>
          <td class="dv">{esc(row["dimension"])}</td></tr>
      <tr><td class="dk">Derive attendue</td>
          <td class="dv">{"Oui" if row["expected_alert"] else "Non"}</td></tr>
    </table>
    <div class="detail-lbl" style="margin-top:12px">Vocabulaire responsable de la derive</div>
    <p class="vocab-desc">{esc(row["desc"])}</p>
  </div>
</div>'''

def proof_c(row):
    return f'''<table class="cs-table">
      <thead><tr>
        <th>Type de reference</th>
        <th>Similarite cosinus (centroide TF-IDF)</th>
        <th>Score</th>
      </tr></thead>
      <tbody>{cs_rows(row["centroid_scores"])}</tbody>
    </table>
    <p class="interp" style="margin-top:8px">
      Seuil attendu pour un document FR nominal : similarite &gt; 60 % sur le type declare.
      Un score &lt; 40 % indique un eloignement du vocabulaire de reference.
    </p>'''

def render_doc(row, idx):
    pb = 'style="page-break-before:always"' if idx > 0 else ''
    return f'''
<section class="sample" {pb}>
  <div class="sample-header">
    <div class="sample-left">
      <span class="sidx">{idx+1:02d}</span>
      <div>
        <h2 class="scompany">{esc(row["company"])}</h2>
        <div class="sfname">{esc(row["fname"])}</div>
      </div>
    </div>
    <div class="sample-right">
      {dim_badge(row["dimension"])}
      <span class="chip dtype">{esc(row["doc_type"])}</span>
      {verdict_badge(row["jsd_alert"], row["result_ok"])}
    </div>
  </div>

  <div class="meta-row">
    <span class="mi"><strong>JSD</strong> {row["jsd_score"]:.4f} ({round(row["jsd_score"]*100,1)} %)</span>
    <span class="mi"><strong>Dimension</strong> {esc(row["dimension"])}</span>
    <span class="mi"><strong>Conforme</strong> {"Oui" if row["result_ok"] else "Non"}</span>
  </div>

  <div class="dim-explain">
    <strong>Hypothese FinNLP 2020 :</strong> {esc(row["desc"])}
  </div>

  <div class="proof-block">
    <h3 class="ptitle"><span class="pnum">A</span>Extrait du document soumis (30 premieres lignes)</h3>
    {proof_a(row["text_preview"])}
  </div>
  <div class="proof-block">
    <h3 class="ptitle"><span class="pnum">B</span>Analyse de derive JSD — SemanticMonitor</h3>
    {proof_b(row)}
  </div>
  <div class="proof-block">
    <h3 class="ptitle"><span class="pnum">C</span>Profil de similarite aux centroïdes TF-IDF</h3>
    {proof_c(row)}
  </div>
</section>'''

# ── Synthese ─────────────────────────────────────────────────────────────────

def render_summary():
    n_alert = sum(1 for r in rows if r['jsd_alert'])
    n_ok    = sum(1 for r in rows if not r['jsd_alert'])
    n_conf  = sum(1 for r in rows if r['result_ok'])

    trs = ''
    for r in rows:
        jsd   = r['jsd_score']
        alert = r['jsd_alert']
        w     = round(jsd * 100)
        bc    = 'sb-alert' if alert else 'sb-ok'
        al_c  = 'chip fail' if alert else 'chip pass'
        al_t  = 'DERIVE' if alert else 'OK'
        pr_c  = 'chip pass' if r['result_ok'] else 'chip fail'
        pr_t  = 'Conforme' if r['result_ok'] else 'Inattendu'
        trs += f'''<tr>
          <td>{dim_badge(r["dimension"])}</td>
          <td><strong>{esc(r["company"])}</strong><br>
              <span class="sub">{esc(r["fname"])}</span></td>
          <td class="tc"><span class="chip dtype-sm">{esc(r["doc_type"])}</span></td>
          <td class="tc num">
            {jsd:.4f}
            <div class="sbar-wrap">
              <div class="sbar {bc}" style="width:{w}%"></div>
              <div class="sbar-thr"></div>
            </div>
          </td>
          <td class="tc"><span class="{al_c}">{al_t}</span></td>
          <td class="tc"><span class="{pr_c}">{pr_t}</span></td>
        </tr>'''

    return f'''
<section class="summary">
  <h2 class="sec-h">Synthese — Cadre FinNLP 2020 (Montariol et al.)</h2>
  <div class="kpi-row">
    <div class="kpi"><div class="kv anom-c">{n_alert}</div><div class="kl">Derives detectees</div></div>
    <div class="kpi"><div class="kv cert-c">{n_ok}</div><div class="kl">Documents nominaux</div></div>
    <div class="kpi"><div class="kv">{len(rows)}</div><div class="kl">Total analyses</div></div>
    <div class="kpi"><div class="kv cert-c">{n_conf}/{len(rows)}</div><div class="kl">Predictions conformes</div></div>
  </div>

  <div class="theory-box">
    <strong>Cadre theorique FinNLP 2020 :</strong>
    Montariol et al. definissent deux axes de derive semantique :
    <em>diachronique</em> (variation temporelle — ex. "crisis" pre/post 2008)
    et <em>synchronique</em> (variation cross-secteurs — ex. "client" en immobilier vs tech vs finance).
    Ils mesurent cette derive par JSD sur distributions d'embeddings BERT.
    Notre implementation utilise le meme JSD sur distributions TF-IDF (approche sans LLM),
    et retrouve les memes patterns de separation sectorielle et temporelle.
  </div>

  <div class="legend-row">
    <span class="li"><span class="sbar-lg sb-alert"></span>JSD &gt; 0.55 — derive detectee</span>
    <span class="li"><span class="sbar-lg sb-ok"></span>JSD &lt;= 0.55 — nominal</span>
    <span class="li"><span class="sbar-thr-lg"></span>Seuil 0.55</span>
  </div>

  <div class="tw"><table class="stab">
    <thead><tr>
      <th>Dimension</th><th>Entreprise / Fichier</th><th>Type</th>
      <th>Score JSD (0-1)</th><th>Derive</th><th>Prediction</th>
    </tr></thead>
    <tbody>{trs}</tbody>
  </table></div>
</section>'''

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = '''
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
@page{size:A4;margin:18mm 16mm 16mm 16mm}
body{font-family:-apple-system,'Segoe UI',system-ui,sans-serif;font-size:11px;
  line-height:1.5;color:#1e293b;background:#f1f5f9}

.cover{background:#0d1b36;color:#e2e8f0;padding:48px 40px 36px;margin-bottom:22px}
.c-eye{font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:#64748b;margin-bottom:10px}
.c-title{font-size:21px;font-weight:700;line-height:1.25;color:#f8fafc;margin-bottom:6px}
.c-sub{font-size:12px;color:#7dd3fc;margin-bottom:22px}
.c-meta{display:flex;flex-wrap:wrap;gap:18px 28px;border-top:1px solid #1e3a5f;
  padding-top:14px;font-size:9.5px;color:#94a3b8}
.c-meta strong{color:#cbd5e1}
.c-ref{margin-top:14px;padding:10px 14px;background:rgba(59,130,246,.1);
  border-left:3px solid #3b82f6;border-radius:0 4px 4px 0;font-size:9px;color:#93c5fd;line-height:1.6}

.sec-h{font-size:13px;font-weight:700;color:#0f2044;margin-bottom:14px;
  padding-bottom:6px;border-bottom:2px solid #0f2044}

.kpi-row{display:flex;gap:14px;margin-bottom:16px}
.kpi{flex:1;background:white;border:1px solid #e2e8f0;border-radius:6px;padding:14px;text-align:center}
.kv{font-size:26px;font-weight:700;color:#0f2044;font-variant-numeric:tabular-nums}
.kv.cert-c{color:#065f46}.kv.anom-c{color:#991b1b}
.kl{font-size:8.5px;letter-spacing:.07em;text-transform:uppercase;color:#64748b;margin-top:4px}

.theory-box{background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;
  padding:12px 14px;font-size:9.5px;color:#1e3a5f;line-height:1.7;margin-bottom:14px}

.dim-badge{display:inline-block;padding:2px 7px;border-radius:3px;
  font-size:8.5px;font-weight:800;letter-spacing:.07em}
.dim-sync{background:#fef9c3;color:#854d0e}
.dim-dia{background:#fce7f3;color:#9d174d}
.dim-ctrl{background:#f1f5f9;color:#475569}

.badge{display:inline-block;padding:3px 9px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.03em}
.badge.cert{background:#dcfce7;color:#065f46}
.badge.anom{background:#fee2e2;color:#991b1b}
.badge-ok{display:inline-block;padding:2px 6px;border-radius:3px;font-size:8.5px;font-weight:600}
.badge-ok.ok-mark{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
.badge-ok.ko-mark{background:#fff7ed;color:#9a3412;border:1px solid #fed7aa}
.chip{display:inline-block;padding:2px 7px;border-radius:3px;font-size:9px;font-weight:700;letter-spacing:.04em}
.chip.pass{background:#dcfce7;color:#166534}
.chip.fail{background:#fee2e2;color:#991b1b}
.chip.dtype{background:#dbeafe;color:#1e40af}
.chip.dtype-sm{background:#eff6ff;color:#1e40af;font-size:8.5px}

.summary{background:white;border:1px solid #e2e8f0;border-radius:8px;padding:22px;margin-bottom:22px}
.tw{overflow-x:auto}
.stab{width:100%;border-collapse:collapse;font-size:10px}
.stab th{text-align:left;font-size:8.5px;letter-spacing:.06em;text-transform:uppercase;
  color:#64748b;border-bottom:2px solid #e2e8f0;padding:6px 10px}
.stab td{padding:7px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
.sub{font-size:8.5px;color:#94a3b8;font-family:'Cascadia Code',Consolas,monospace}
.tc{text-align:center}.num{font-variant-numeric:tabular-nums;font-family:'Cascadia Code',Consolas,monospace}

.sbar-wrap{position:relative;height:5px;background:#f1f5f9;border-radius:2px;overflow:hidden;
  margin-top:4px;width:80px}
.sbar{height:100%;border-radius:2px}
.sb-alert{background:#ef4444}.sb-ok{background:#22c55e}
.sbar-thr{position:absolute;left:55%;top:0;width:2px;height:100%;background:#f59e0b}
.legend-row{display:flex;gap:20px;align-items:center;font-size:9px;color:#64748b;margin-bottom:12px}
.li{display:flex;align-items:center;gap:5px}
.sbar-lg{display:inline-block;width:18px;height:6px;border-radius:2px}
.sbar-thr-lg{display:inline-block;width:3px;height:12px;background:#f59e0b;border-radius:1px}

.sample{background:white;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:20px;overflow:hidden}
.sample-header{display:flex;justify-content:space-between;align-items:flex-start;
  padding:16px 20px 12px;background:#f8fafc;border-bottom:1px solid #e2e8f0}
.sample-left{display:flex;align-items:flex-start;gap:12px}
.sidx{font-size:18px;font-weight:800;color:#cbd5e1;min-width:26px;line-height:1;margin-top:2px}
.scompany{font-size:13px;font-weight:700;color:#0f2044;margin-bottom:3px}
.sfname{font-family:'Cascadia Code',Consolas,monospace;font-size:9px;color:#64748b}
.sample-right{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.meta-row{display:flex;flex-wrap:wrap;gap:16px;padding:7px 20px;background:#fafafa;
  border-bottom:1px solid #f1f5f9;font-size:9.5px;color:#64748b}
.mi strong{color:#374151}

.dim-explain{padding:8px 20px;background:#fffbeb;border-bottom:1px solid #fef3c7;
  font-size:9px;color:#78350f;line-height:1.6}

.proof-block{padding:14px 20px;border-top:1px solid #f1f5f9}
.ptitle{font-size:10.5px;font-weight:700;color:#334155;margin-bottom:10px;
  display:flex;align-items:center;gap:8px}
.pnum{display:inline-flex;align-items:center;justify-content:center;
  width:18px;height:18px;border-radius:3px;background:#0f2044;color:white;
  font-size:9px;font-weight:800;flex-shrink:0}

.doc-pre{font-family:'Cascadia Code',Consolas,'Courier New',monospace;font-size:8.5px;
  line-height:1.5;color:#334155;background:#f8fafc;border:1px solid #e2e8f0;
  border-radius:4px;padding:12px 14px;overflow-x:auto;white-space:pre;
  max-height:220px;overflow-y:auto}

.jsd-grid{display:flex;gap:24px;align-items:flex-start}
.jsd-left{flex:0 0 220px}
.jsd-right{flex:1}
.jsd-num{font-size:36px;font-weight:800;font-variant-numeric:tabular-nums;
  line-height:1;margin-bottom:2px}
.jsd-lbl{font-size:8.5px;letter-spacing:.1em;text-transform:uppercase;
  color:#94a3b8;margin-bottom:10px}
.jsd-scale-wrap{margin-bottom:4px}
.jsd-scale{position:relative;height:14px;background:#e2e8f0;border-radius:4px;overflow:hidden}
.jsd-fill{height:100%;border-radius:4px}
.jf-alert{background:linear-gradient(90deg,#fca5a5,#ef4444)}
.jf-ok{background:linear-gradient(90deg,#6ee7b7,#059669)}
.jsd-thr{position:absolute;left:55%;top:0;width:2px;height:100%;background:#f59e0b}
.jsd-axis{display:flex;justify-content:space-between;font-size:8px;
  color:#94a3b8;margin-top:3px;font-variant-numeric:tabular-nums}
.thr-lbl{position:relative;left:-8px;color:#d97706;font-weight:600}
.detail-lbl{font-size:8.5px;letter-spacing:.08em;text-transform:uppercase;
  color:#94a3b8;margin-bottom:6px}
.det-table{border-collapse:collapse;width:100%}
.det-table td{padding:3px 6px;font-size:9.5px}
.dk{color:#64748b;min-width:160px}.dv{color:#0f2044;font-weight:600}
.vocab-desc{font-size:9px;color:#64748b;font-style:italic;line-height:1.6;
  background:#f8fafc;border-left:2px solid #e2e8f0;padding:6px 10px;border-radius:0 4px 4px 0}
.interp{font-size:9px;color:#64748b;font-style:italic;line-height:1.5}

.cs-table{border-collapse:collapse;width:100%;font-size:10px}
.cs-table th{text-align:left;font-size:8.5px;letter-spacing:.06em;text-transform:uppercase;
  color:#94a3b8;border-bottom:1px solid #e2e8f0;padding:4px 8px}
.cs-table td{padding:4px 8px;border-bottom:1px solid #f8fafc;vertical-align:middle}
.cs-type{color:#475569;min-width:130px}
.cs-wrap{height:10px;background:#f1f5f9;border-radius:3px;overflow:hidden;max-width:180px}
.cs-bar{height:100%;background:#0369a1;border-radius:3px}
.cs-val{color:#0f2044;font-family:'Cascadia Code',Consolas,monospace;
  font-variant-numeric:tabular-nums;min-width:50px;text-align:right}
'''

# ── Assemblage HTML ───────────────────────────────────────────────────────────

now  = datetime.now().strftime('%d/%m/%Y %H:%M')
sections = '\n'.join(render_doc(r, i) for i, r in enumerate(rows))

html = f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport Derive Semantique FinNLP 2020 — Montariol et al.</title>
<style>{CSS}</style>
</head>
<body>
<div class="cover">
  <div class="c-eye">Pare-feu Semantique DD — Test de derive semantique JSD</div>
  <div class="c-title">Detection de derive semantique<br>Cadre FinNLP 2020 — Montariol, Allauzen, Kitamoto</div>
  <div class="c-sub">Dimensions synchronique (cross-secteurs) et diachronique (temporelle) | TF-IDF JSD</div>
  <div class="c-meta">
    <span><strong>Date</strong> {now}</span>
    <span><strong>Reference</strong> FinNLP 2020 — "Variations in Word Usage for the Financial Domain"</span>
    <span><strong>Corpus ref.</strong> samples/dd/ + samples/ (documents DD francais)</span>
    <span><strong>Seuil JSD</strong> 0.55</span>
    <span><strong>Methode</strong> TF-IDF + Jensen-Shannon Divergence (vs BERT dans le papier)</span>
  </div>
  <div class="c-ref">
    Montariol et al. (2020) definissent la derive semantique sur deux axes :
    <strong>synchronique</strong> (variation cross-secteurs, ex. "client" = 4 sens differents selon SIC code)
    et <strong>diachronique</strong> (evolution temporelle, ex. "crisis" pre/post 2008).
    Mesure par JSD sur distributions d'embeddings BERT (corpus SEC-Edgar 8 676 docs + BCE/Fed 411 docs).
    Notre approche : meme JSD sur distributions TF-IDF — plus simple, applicable sans GPU.
  </div>
</div>

{render_summary()}
{sections}

</body></html>'''

# ── Ecriture + PDF ────────────────────────────────────────────────────────────

out_dir   = Path('output')
out_dir.mkdir(exist_ok=True)
html_path = out_dir / 'finnlp2020_drift_report.html'
pdf_path  = out_dir / 'finnlp2020_drift_report.pdf'

html_path.write_text(html, encoding='utf-8')
print(f"\n  HTML -> {html_path}")

edge = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if not Path(edge).exists():
    edge = r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'

file_uri = html_path.resolve().as_uri()
print(f"  PDF  -> {pdf_path} ...")
subprocess.run([
    edge, '--headless', '--disable-gpu', '--no-sandbox',
    '--run-all-compositor-stages-before-draw',
    f'--print-to-pdf={pdf_path.resolve()}',
    file_uri,
], capture_output=True, timeout=60)

if pdf_path.exists():
    kb = pdf_path.stat().st_size // 1024
    print(f"  OK PDF genere ({kb} Ko)")
    print(f"\n  Ouvrir : start \"\" \"{pdf_path.resolve()}\"")
else:
    print(f"  PDF non genere — HTML disponible : {html_path}")
