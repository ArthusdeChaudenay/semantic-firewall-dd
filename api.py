"""
api.py — Service REST FastAPI pour le pare-feu sémantique DD.

Endpoints :
  POST /certifier/document            Certifie un seul fichier uploadé
  POST /certifier/dossier             Certifie un dossier (N fichiers) + push connecteurs
  GET  /dossiers/{dossier_id}         Récupère un rapport persisté
  GET  /dossiers                      Liste tous les dossiers certifiés
  GET  /health                        Statut du service

  POST /webhook/ingest                Ingestion depuis une data room (URL de fichier)
  POST /webhook/ingest-file           Ingestion directe (upload multipart) + push alertes
  GET  /connectors/status             État de tous les connecteurs DD

  POST /rag/index                     Indexe une data room (gate JSD)
  GET  /rag/stats                     Statistiques du corpus RAG
  POST /rag/query                     Recherche de documents similaires
  DELETE /rag/reset                   Réinitialise le corpus RAG

Lancer :
    .\\venv\\Scripts\\uvicorn.exe api:app --reload --port 8000
"""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from fastapi           import FastAPI, File, Form, UploadFile, HTTPException, Header
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from pydantic          import BaseModel

from pipeline    import certify_document_from_bytes, certify_dossier
from rag_corpus  import RagCorpus, CORPUS_PATH
from connectors  import get_manager
from dd_workflow        import classify_alerts, WORKFLOW_SCHEMA
from dd_generate_report import build_html, generate_dd_report

DOSSIERS_DIR = Path("output/dossiers")
DOSSIERS_DIR.mkdir(parents=True, exist_ok=True)

# ── Constantes de sécurité ────────────────────────────────────────────────────
MAX_UPLOAD_BYTES   = 20 * 1024 * 1024          # 20 Mo par fichier
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".csv", ".xlsx", ".xls", ".docx"}
_UNSAFE_FILENAME   = re.compile(r'[^\w\s.\-]', re.UNICODE)  # caractères dangereux


# ── SSRF — plages IP internes interdites ──────────────────────────────────────
import ipaddress as _ipaddress
_PRIVATE_PREFIXES = [
    _ipaddress.ip_network("10.0.0.0/8"),
    _ipaddress.ip_network("172.16.0.0/12"),
    _ipaddress.ip_network("192.168.0.0/16"),
    _ipaddress.ip_network("127.0.0.0/8"),
    _ipaddress.ip_network("169.254.0.0/16"),
    _ipaddress.ip_network("::1/128"),
]


def _safe_filename(raw: str) -> str:
    """Extrait uniquement le nom de fichier (sans répertoire) et valide l'extension."""
    name = Path(raw).name          # supprime tout préfixe de répertoire
    name = _UNSAFE_FILENAME.sub("_", name)
    if not name:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Extension '{ext}' non autorisée. Acceptées : {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return name


def _check_file_size(content: bytes) -> None:
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux ({len(content)//1024//1024} Mo). Maximum : 20 Mo.",
        )


def _check_webhook_url(url: str) -> None:
    """Bloque les URLs pointant vers des ressources réseau internes (SSRF)."""
    import socket, urllib.parse
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Schéma d'URL non autorisé (http/https uniquement).")
    host = parsed.hostname or ""
    try:
        ip = _ipaddress.ip_address(socket.gethostbyname(host))
        for net in _PRIVATE_PREFIXES:
            if ip in net:
                raise HTTPException(status_code=400, detail="URL vers une adresse réseau privée interdite.")
    except HTTPException:
        raise
    except Exception:
        pass  # Si la résolution DNS échoue on laisse l'appel downstream échouer naturellement

app = FastAPI(
    title="Pare-feu Sémantique DD",
    description="Certification automatique de documents de due diligence (bilan, P&L, cap table, facture)",
    version="1.0.0",
)


# ==========================================
# PAGE D'UPLOAD
# ==========================================

@app.get("/", tags=["Monitoring"], include_in_schema=False)
def upload_page():
    """Page d'upload directe — deux zones : certification DD + vérification dérive sémantique."""
    html = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>Pare-feu Sémantique DD</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif;
           min-height: 100vh; padding: 32px 24px; }
    h1 { font-size: 1.1rem; font-weight: 700; margin-bottom: 4px; color: #f8fafc; }
    .sub { font-size: .78rem; color: #64748b; margin-bottom: 22px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; max-width: 1040px; margin: 0 auto; }
    @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 28px 32px; }
    .card-title { font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
                  color: #64748b; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
    .pill { font-size: .65rem; padding: 2px 8px; border-radius: 99px; font-weight: 600; }
    .pill-blue  { background: #1e3a5f; color: #60a5fa; }
    .pill-amber { background: #3b2a0e; color: #fbbf24; }
    label { display: block; font-size: .76rem; color: #94a3b8; margin-bottom: 5px; }
    input[type=text] { width: 100%; background: #0f172a; border: 1px solid #334155;
                       border-radius: 6px; padding: 8px 11px; color: #f1f5f9;
                       font-size: .88rem; margin-bottom: 16px; }
    input[type=text]:focus { outline: none; border-color: #6366f1; }
    .drop { border: 2px dashed #334155; border-radius: 8px; padding: 24px 16px;
            text-align: center; cursor: pointer; transition: border-color .15s, background .15s;
            margin-bottom: 16px; }
    .drop:hover, .drop.over { border-color: #6366f1; background: #161f35; }
    .drop-icon { font-size: 1.6rem; margin-bottom: 6px; }
    .drop-text { font-size: .82rem; color: #94a3b8; }
    .drop-text strong { color: #a5b4fc; }
    .file-list { margin-top: 8px; font-size: .74rem; color: #6ee7b7; text-align: left; line-height: 1.7; }
    .btn { width: 100%; border: none; border-radius: 8px; padding: 11px;
           font-size: .92rem; font-weight: 600; cursor: pointer; transition: background .15s; }
    .btn-indigo { background: #6366f1; color: white; }
    .btn-indigo:hover { background: #4f46e5; }
    .btn-amber  { background: #d97706; color: white; }
    .btn-amber:hover  { background: #b45309; }
    .btn:disabled { background: #1e293b; color: #475569; border: 1px solid #334155; cursor: not-allowed; }
    .hint { font-size: .7rem; color: #475569; text-align: center; margin-top: 10px; }
    .hint a { color: #6366f1; }
    /* Stats corpus */
    .stats-bar { background: #0f172a; border: 1px solid #334155; border-radius: 8px;
                 padding: 10px 14px; margin-bottom: 16px; font-size: .76rem; color: #94a3b8;
                 display: flex; align-items: center; justify-content: space-between; }
    .stats-num { font-weight: 700; color: #f1f5f9; font-size: .9rem; }
    .stats-refresh { background: none; border: none; color: #6366f1; cursor: pointer;
                     font-size: .72rem; padding: 2px 6px; border-radius: 4px; }
    .stats-refresh:hover { background: #1e293b; }
    /* Résultats dérive */
    #drift-result { margin-top: 16px; display: none; }
    .jsd-wrap { margin: 12px 0; }
    .jsd-label { display: flex; justify-content: space-between; font-size: .74rem; color: #94a3b8; margin-bottom: 5px; }
    .jsd-track { background: #0f172a; border-radius: 99px; height: 10px; position: relative; overflow: visible; }
    .jsd-bar   { height: 10px; border-radius: 99px; transition: width .4s; }
    .jsd-needle { position: absolute; top: -4px; width: 2px; height: 18px;
                  background: #f59e0b; border-radius: 1px; transform: translateX(-50%); }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 6px;
             font-size: .74rem; font-weight: 700; margin-bottom: 10px; }
    .badge-red    { background: #450a0a; color: #f87171; border: 1px solid #991b1b; }
    .badge-green  { background: #052e16; color: #4ade80; border: 1px solid #166534; }
    .badge-amber  { background: #3b2a0e; color: #fbbf24; border: 1px solid #92400e; }
    .reason-txt { font-size: .74rem; color: #64748b; margin-bottom: 12px; line-height: 1.5; }
    .similar-title { font-size: .72rem; font-weight: 700; color: #64748b; text-transform: uppercase;
                     letter-spacing: .06em; margin-bottom: 8px; }
    .sim-row { display: flex; justify-content: space-between; align-items: center;
               padding: 6px 10px; border-radius: 6px; background: #0f172a;
               margin-bottom: 4px; font-size: .76rem; }
    .sim-name { color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                max-width: 70%; }
    .sim-pct  { font-weight: 700; color: #818cf8; flex-shrink: 0; }
    .no-corpus { font-size: .78rem; color: #64748b; text-align: center; padding: 16px 0; }
    .spinner-sm { display: none; text-align: center; font-size: .8rem; color: #94a3b8; margin-top: 8px; }
    /* Dossiers */
    .dossiers-panel { max-width: 1040px; margin: 24px auto 0; }
    .dossiers-panel .card-title { margin-bottom: 12px; }
    .dossier-table { width: 100%; border-collapse: collapse; font-size: .78rem; }
    .dossier-table th { text-align: left; color: #475569; font-weight: 600;
                        padding: 6px 10px; border-bottom: 1px solid #1e3a5f; font-size: .7rem;
                        text-transform: uppercase; letter-spacing: .05em; }
    .dossier-table td { padding: 8px 10px; border-bottom: 1px solid #0f172a; vertical-align: middle; }
    .dossier-table tr:last-child td { border-bottom: none; }
    .dossier-table tr:hover td { background: #162032; }
    .d-nom  { color: #e2e8f0; font-weight: 500; }
    .d-id   { color: #334155; font-size: .68rem; font-family: monospace; }
    .d-date { color: #64748b; }
    .d-docs { color: #94a3b8; }
    .d-verdict { display: inline-block; padding: 2px 8px; border-radius: 4px;
                 font-size: .7rem; font-weight: 700; }
    .v-certifie  { background:#052e16; color:#4ade80; }
    .v-vigilance { background:#2e1065; color:#c4b5fd; }
    .v-anomalie  { background:#3b2a0e; color:#fbbf24; }
    .v-erreur    { background:#450a0a; color:#f87171; }
    .btn-del { background: none; border: 1px solid #334155; border-radius: 5px;
               color: #64748b; cursor: pointer; padding: 3px 8px; font-size: .72rem;
               transition: all .15s; }
    .btn-del:hover { border-color: #ef4444; color: #f87171; background: #1a0a0a; }
    .btn-dl  { background: none; border: 1px solid #1e3a5f; border-radius: 5px;
               color: #60a5fa; cursor: pointer; padding: 3px 8px; font-size: .72rem;
               text-decoration: none; display: inline-block; transition: all .15s; }
    .btn-dl:hover { border-color: #60a5fa; background: #0c1f35; }
    .btn-dl-pdf { border-color: #312e6e; color: #a5b4fc; }
    .btn-dl-pdf:hover { border-color: #a5b4fc; background: #151535; }
    .actions-cell { display: flex; gap: 5px; align-items: center; flex-wrap: wrap; }
    .no-dossiers { text-align: center; color: #334155; padding: 20px; font-size: .8rem; }
    /* Mots dérivants */
    .drift-words-wrap { margin-top: 14px; border-top: 1px solid #1e3a5f; padding-top: 10px; }
    .drift-words-title { font-size:.68rem; color:#94a3b8; font-weight:700; text-transform:uppercase;
                         letter-spacing:.06em; margin-bottom:7px; }
    .drift-words-table { width:100%; border-collapse:collapse; font-size:.73rem; }
    .drift-words-table th { color:#475569; font-weight:600; padding:4px 8px;
                            border-bottom:1px solid #1e3a5f; font-size:.65rem; text-transform:uppercase; }
    .drift-words-table td { padding:5px 8px; border-bottom:1px solid #0a1929; vertical-align:top; }
    .drift-words-table tr:last-child td { border-bottom:none; }
    .drift-words-table tr:hover td { background:#0d1e30; }
    .mot-absent { color:#f87171; font-weight:600; }
    .mot-surep  { color:#fbbf24; font-weight:600; }
    .mot-normal { color:#e2e8f0; font-weight:600; }
    .mot-expl   { color:#64748b; font-size:.68rem; }
    .header-bar { max-width: 1040px; margin: 0 auto 24px; display: flex; align-items: baseline; gap: 12px; }
    .header-bar h2 { font-size: 1rem; font-weight: 700; color: #f8fafc; }
    .header-bar .tagline { font-size: .78rem; color: #475569; }
  </style>
</head>
<body>

<div class="header-bar">
  <h2>Pare-feu Sémantique DD</h2>
  <span class="tagline">certification · dérive sémantique · due diligence</span>
  <span style="margin-left:auto;font-size:.72rem;color:#334155">
    <a href="/docs" style="color:#6366f1">API Swagger</a>
  </span>
</div>

<div class="grid">

  <!-- ── ZONE 1 : Certification DD ─────────────────────────────────────── -->
  <div class="card">
    <div class="card-title">
      Certification DD
      <span class="pill pill-blue">bilan · P&L · cap table</span>
    </div>

    <div id="form-cert">

      <label for="ent">Nom de l'entreprise</label>
      <input type="text" id="ent" placeholder="Ex : TechVenture SAS">

      <label>Documents du dossier</label>
      <input type="file" id="files-cert" multiple
             accept=".txt,.pdf,.csv,.xlsx,.xls,.docx" style="display:none">
      <div class="drop" id="drop-cert"
           onclick="document.getElementById('files-cert').click()">
        <div class="drop-icon">📂</div>
        <div class="drop-text">
          <strong>Cliquer</strong> ou glisser-déposer<br>
          <span style="font-size:.7rem">.txt · .pdf · .csv · .xlsx</span>
        </div>
        <div class="file-list" id="list-cert"></div>
      </div>

      <button class="btn btn-indigo" id="btn-cert" onclick="lancerCertification()" disabled>
        Certifier et générer le rapport
      </button>
      <div id="cert-progress" style="display:none;margin-top:12px">
        <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px 16px">
          <div style="font-size:.8rem;color:#94a3b8;margin-bottom:8px">
            ⏳ <span id="cert-status">Analyse en cours…</span>
          </div>
          <div style="background:#1e293b;border-radius:4px;height:6px;overflow:hidden">
            <div id="cert-bar" style="height:6px;background:#6366f1;width:0%;transition:width .4s;border-radius:4px"></div>
          </div>
          <div id="cert-files-status" style="margin-top:8px;font-size:.72rem;color:#475569;line-height:1.7"></div>
        </div>
      </div>
      <div id="cert-error" style="display:none;margin-top:10px;font-size:.78rem;color:#f87171;
           background:#1a0a0a;border:1px solid #7f1d1d;border-radius:6px;padding:10px 14px"></div>
      <p class="hint" id="cert-hint">Résultat dans cette page — clic sur le rapport pour ouvrir</p>
    </div>
  </div>

  <!-- ── ZONE 2 : Dérive sémantique ───────────────────────────────────── -->
  <div class="card">
    <div class="card-title">
      Dérive sémantique
      <span class="pill pill-amber">JSD · TF-IDF</span>
    </div>

    <!-- Stats corpus -->
    <div class="stats-bar">
      <span>Corpus RAG : <span class="stats-num" id="corpus-count">—</span> docs indexés</span>
      <span id="corpus-types" style="font-size:.68rem;color:#475569;margin-left:8px"></span>
      <span style="margin-left:auto;display:flex;gap:4px">
        <button class="stats-refresh" onclick="loadStats()">↻</button>
        <button class="stats-refresh" id="btn-reset-corpus" onclick="resetCorpus()"
                title="Vider le corpus RAG" style="color:#64748b">🗑</button>
      </span>
    </div>

    <!-- Indexation corpus (admin) -->
    <div style="margin-bottom:16px">
      <label>Ajouter au corpus de référence</label>
      <div style="display:flex;gap:8px;align-items:center">
        <input type="file" id="files-index" multiple accept=".txt,.pdf,.csv,.xlsx" style="display:none">
        <div class="drop" id="drop-index" style="flex:1;padding:12px 10px;margin-bottom:0"
             onclick="document.getElementById('files-index').click()">
          <div class="drop-text" style="font-size:.76rem">
            <strong id="index-label">Sélectionner des docs de référence</strong>
          </div>
        </div>
        <button class="btn btn-indigo" id="btn-index" onclick="indexFiles()"
                style="width:auto;padding:10px 16px;flex-shrink:0" disabled>
          Indexer
        </button>
      </div>
      <div class="spinner-sm" id="spin-index"></div>
      <div id="index-result" style="font-size:.74rem;margin-top:6px"></div>
    </div>

    <hr style="border:none;border-top:1px solid #1e3a5f;margin-bottom:16px">

    <!-- Zone upload dérive -->
    <label>Document à vérifier</label>
    <input type="file" id="file-drift" accept=".txt,.pdf,.csv,.xlsx" style="display:none">
    <div class="drop" id="drop-drift"
         onclick="document.getElementById('file-drift').click()">
      <div class="drop-icon">🔍</div>
      <div class="drop-text">
        <strong>Cliquer</strong> ou glisser-déposer<br>
        <span style="font-size:.7rem">compare par rapport au corpus indexé</span>
      </div>
      <div class="file-list" id="list-drift"></div>
    </div>

    <button class="btn btn-amber" id="btn-drift" onclick="checkDrift()" disabled>
      Vérifier la dérive sémantique
    </button>
    <div class="spinner-sm" id="spin-drift">⏳ Analyse en cours…</div>

    <!-- Résultats -->
    <div id="drift-result"></div>
  </div>

</div>

<div class="dossiers-panel">
  <div class="card">
    <div class="card-title" style="justify-content:space-between">
      <span>Dossiers certifiés <span id="d-count" style="color:#334155;font-weight:400"></span></span>
      <button class="stats-refresh" onclick="loadDossiers()">↻ Rafraîchir</button>
    </div>
    <div id="dossiers-wrap">
      <table class="dossier-table">
        <thead><tr>
          <th>Entreprise</th><th>Verdict</th><th>Date</th><th>Docs</th><th>Anomalies</th><th>Actions</th>
        </tr></thead>
        <tbody id="dossiers-tbody">
          <tr><td colspan="6" class="no-dossiers">Chargement…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
// ── Dossiers ─────────────────────────────────────────────────────────────────
async function loadDossiers() {
  const tbody = document.getElementById('dossiers-tbody');
  const count = document.getElementById('d-count');
  try {
    const r = await fetch('/dossiers');
    const d = await r.json();
    count.textContent = '(' + d.total + ')';
    if (!d.dossiers.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="no-dossiers">Aucun dossier certifié</td></tr>';
      return;
    }
    tbody.innerHTML = d.dossiers.map(dos => {
      const vc = dos.verdict === 'CERTIFIÉ'  ? 'v-certifie'
               : dos.verdict === 'VIGILANCE' ? 'v-vigilance'
               : dos.verdict === 'ANOMALIE'  ? 'v-anomalie' : 'v-erreur';
      return `<tr>
        <td><div class="d-nom">${dos.entreprise_nom}</div>
            <div class="d-id">${dos.dossier_id.substring(0,8)}…</div></td>
        <td><span class="d-verdict ${vc}">${dos.verdict}</span></td>
        <td class="d-date">${dos.horodatage || '—'}</td>
        <td class="d-docs">${dos.nb_documents}</td>
        <td class="d-docs">${dos.nb_anomalies || 0}</td>
        <td><div class="actions-cell">
          <a class="btn-dl" href="/dossiers/${dos.dossier_id}/rapport" target="_blank" title="Ouvrir le rapport HTML">HTML</a>
          <a class="btn-dl btn-dl-pdf" href="/dossiers/${dos.dossier_id}/pdf" download title="Télécharger le PDF">PDF</a>
          <button class="btn-del" onclick="deleteDossier('${dos.dossier_id}', this)" title="Supprimer ce dossier">✕</button>
        </div></td>
      </tr>`;
    }).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="6" class="no-dossiers" style="color:#f87171">Erreur de chargement</td></tr>';
  }
}
loadDossiers();

async function deleteDossier(id, btn) {
  if (!confirm('Supprimer ce dossier ?')) return;
  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch('/dossiers/' + id, { method: 'DELETE' });
    if (r.ok) { await loadDossiers(); }
    else { btn.textContent = 'Erreur'; btn.style.color = '#f87171'; }
  } catch(e) { btn.textContent = 'Erreur'; }
}

// ── Certification ──────────────────────────────────────────────────────────

function setupDrop(dropId, inputId, listId, btnId) {
  const drop  = document.getElementById(dropId);
  const input = document.getElementById(inputId);
  const list  = document.getElementById(listId);
  const btn   = document.getElementById(btnId);

  input.addEventListener('change', () => updateList(input, list, btn));

  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => {
    e.preventDefault(); drop.classList.remove('over');
    const dt = new DataTransfer();
    [...(input.files || []), ...e.dataTransfer.files].forEach(f => dt.items.add(f));
    input.files = dt.files; updateList(input, list, btn);
  });
}

function updateList(input, list, btn) {
  const files = [...input.files];
  list.innerHTML = files.map(f => '✓ ' + f.name).join('<br>');
  btn.disabled = files.length === 0;
}

setupDrop('drop-cert',  'files-cert', 'list-cert',  'btn-cert');
setupDrop('drop-drift', 'file-drift', 'list-drift', 'btn-drift');

// Indexation : fichiers multiples, pas de btn auto via setupDrop
const idxInput = document.getElementById('files-index');
const idxDrop  = document.getElementById('drop-index');
const idxBtn   = document.getElementById('btn-index');

idxInput.addEventListener('change', updateIndexLabel);
idxDrop.addEventListener('dragover', e => { e.preventDefault(); idxDrop.classList.add('over'); });
idxDrop.addEventListener('dragleave', () => idxDrop.classList.remove('over'));
idxDrop.addEventListener('drop', e => {
  e.preventDefault(); idxDrop.classList.remove('over');
  const dt = new DataTransfer();
  [...(idxInput.files || []), ...e.dataTransfer.files].forEach(f => dt.items.add(f));
  idxInput.files = dt.files; updateIndexLabel();
});

function updateIndexLabel() {
  const n = idxInput.files.length;
  document.getElementById('index-label').textContent =
    n ? n + ' fichier' + (n > 1 ? 's' : '') + ' sélectionné' + (n > 1 ? 's' : '') : 'Sélectionner des docs de référence';
  idxBtn.disabled = n === 0;
}

async function lancerCertification() {
  const input    = document.getElementById('files-cert');
  const btn      = document.getElementById('btn-cert');
  const progress = document.getElementById('cert-progress');
  const barEl    = document.getElementById('cert-bar');
  const statusEl = document.getElementById('cert-status');
  const filesEl  = document.getElementById('cert-files-status');
  const errEl    = document.getElementById('cert-error');
  const hintEl   = document.getElementById('cert-hint');

  if (!input.files.length) return;

  btn.disabled = true;
  progress.style.display = 'block';
  errEl.style.display    = 'none';
  hintEl.style.display   = 'none';
  statusEl.textContent   = 'Envoi des fichiers…';
  barEl.style.width      = '5%';

  const nbFiles = input.files.length;
  filesEl.innerHTML = [...input.files].map(f => '&#x23F3; ' + f.name).join('<br>');

  // Animation de progression (indicateur visuel, pas temps réel)
  let pct = 5;
  const ticker = setInterval(() => {
    if (pct < 85) { pct += (pct < 30 ? 3 : pct < 60 ? 1.5 : 0.5); barEl.style.width = pct + '%'; }
  }, 1500);

  try {
    statusEl.textContent = 'Extraction LLM en cours (' + nbFiles + ' doc' + (nbFiles > 1 ? 's' : '') + ')…';
    const fd = new FormData();
    fd.append('entreprise', document.getElementById('ent').value);
    [...input.files].forEach(f => fd.append('files', f));

    const r = await fetch('/certifier/dossier', { method: 'POST', body: fd });
    const data = await r.json();

    clearInterval(ticker);
    barEl.style.width = '100%';

    if (!r.ok) {
      errEl.textContent   = 'Erreur ' + r.status + ' : ' + (data.detail || JSON.stringify(data));
      errEl.style.display = 'block';
      statusEl.textContent = 'Echec de la certification';
    } else {
      statusEl.textContent = 'Certification terminee — ouverture du rapport…';
      filesEl.innerHTML    = '';
      await loadDossiers();
      // Ouvre le rapport dans un nouvel onglet
      if (data.dossier_id) {
        const rapportUrl = '/dossiers/' + data.dossier_id + '/rapport';
        const win = window.open(rapportUrl, '_blank');
        if (!win) {
          // Popup bloqué — lien de secours dans la zone de progression
          statusEl.innerHTML = 'Certification terminee — '
            + '<a href="' + rapportUrl + '" target="_blank" style="color:#a5b4fc">ouvrir le rapport</a>';
        }
      }
      // Remet l'UI en etat initial apres 5s
      setTimeout(() => {
        progress.style.display = 'none';
        hintEl.style.display   = 'block';
        barEl.style.width      = '0%';
        btn.disabled           = false;
        document.getElementById('list-cert').innerHTML = '';
      }, 5000);
      return;
    }
  } catch(e) {
    clearInterval(ticker);
    errEl.textContent   = 'Erreur reseau : ' + e.message + ' — verifiez que le serveur tourne et que le modele LLM repond.';
    errEl.style.display = 'block';
    statusEl.textContent = 'Echec';
  }

  progress.style.display = 'none';
  hintEl.style.display   = 'block';
  barEl.style.width      = '0%';
  btn.disabled           = false;
}

// ── Stats corpus ───────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const r = await fetch('/rag/stats');
    const d = await r.json();
    const el    = document.getElementById('corpus-count');
    const types = document.getElementById('corpus-types');
    if (d.total_docs === 0) {
      el.textContent    = '0';
      el.style.color    = '#64748b';
      types.textContent = '';
    } else {
      el.textContent    = d.total_docs;
      el.style.color    = '#f1f5f9';
      types.textContent = Object.entries(d.clusters || {})
        .map(([k, v]) => k + ' ' + v.count).join(' · ');
    }
  } catch(e) { document.getElementById('corpus-count').textContent = '—'; }
}
loadStats();

async function loadMonitorStats() {
  try {
    const r = await fetch('/monitor/stats');
    const d = await r.json();
    const el    = document.getElementById('monitor-count');
    const types = document.getElementById('monitor-types');
    el.textContent    = d.total;
    el.style.color    = d.total > 0 ? '#f1f5f9' : '#64748b';
    types.textContent = Object.entries(d.par_type || {})
      .map(([k, v]) => k + ' ' + v).join(' · ');
  } catch(e) { document.getElementById('monitor-count').textContent = '—'; }
}
loadMonitorStats();

async function rebuildMonitor() {
  const btn = document.getElementById('btn-rebuild-monitor');
  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch('/monitor/rebuild', { method: 'POST' });
    const d = await r.json();
    if (r.ok) {
      btn.textContent = '✓';
      document.getElementById('monitor-count').textContent = d.total;
      document.getElementById('monitor-count').style.color = '#4ade80';
      document.getElementById('monitor-types').textContent =
        Object.entries(d.par_type || {}).map(([k, v]) => k + ' ' + v).join(' · ');
      setTimeout(() => {
        btn.textContent = '↺';
        btn.disabled    = false;
        document.getElementById('monitor-count').style.color = '#f1f5f9';
      }, 2000);
    } else {
      alert((d.detail || d.message) || 'Erreur lors de la reconstruction.');
      btn.disabled = false; btn.textContent = '↺';
    }
  } catch(e) {
    alert('Erreur réseau : ' + e.message);
    btn.disabled = false; btn.textContent = '↺';
  }
}

async function resetCorpus() {
  if (!confirm('Vider entièrement le corpus de référence ?')) return;
  const btn = document.getElementById('btn-reset-corpus');
  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch('/rag/reset', { method: 'DELETE' });
    if (r.ok) { await loadStats(); }
    else {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || 'Erreur lors de la réinitialisation.');
    }
  } catch(e) { alert('Erreur réseau : ' + e.message); }
  finally { btn.disabled = false; btn.textContent = '🗑'; }
}

// ── Indexation ──────────────────────────────────────────────────
async function indexFiles() {
  const input  = document.getElementById('files-index');
  if (!input.files.length) return;

  const btn    = document.getElementById('btn-index');
  const spin   = document.getElementById('spin-index');
  const result = document.getElementById('index-result');

  btn.disabled = true; btn.textContent = 'Indexation…';
  spin.style.display = 'block'; spin.textContent = '⏳ Indexation en cours…';
  result.textContent = '';

  try {
    const fd = new FormData();
    [...input.files].forEach(f => fd.append('files', f));
    const r = await fetch('/rag/index', { method: 'POST', body: fd });
    const d = await r.json();

    if (!r.ok) {
      result.innerHTML = '<span style="color:#f87171">' + (d.detail || 'Erreur') + '</span>';
    } else {
      const docs    = d.details || d.documents || [];
      const indexed = d.indexed ?? docs.filter(x => x.indexed).length;
      const skipped = d.skipped ?? (docs.length - indexed);
      const col     = indexed > 0 ? '#4ade80' : '#fbbf24';
      result.innerHTML =
        '<span style="color:' + col + '">' +
        indexed + ' indexé' + (indexed > 1 ? 's' : '') +
        (skipped ? ', ' + skipped + ' ignoré' + (skipped > 1 ? 's' : '') + ' (doublons)' : '') +
        '</span>';
      input.value = '';
      document.getElementById('index-label').textContent = 'Sélectionner des docs de référence';
      idxBtn.disabled = true;
      await loadStats();
    }
  } catch(e) {
    result.innerHTML = '<span style="color:#f87171">Erreur réseau : ' + e.message + '</span>';
  } finally {
    spin.style.display = 'none';
    btn.disabled = false; btn.textContent = 'Indexer';
  }
}

// ── Vérification dérive ────────────────────────────────────────────────────
async function checkDrift() {
  const input = document.getElementById('file-drift');
  if (!input.files.length) return;

  const btn  = document.getElementById('btn-drift');
  const spin = document.getElementById('spin-drift');
  const res  = document.getElementById('drift-result');

  btn.disabled = true; btn.textContent = 'Analyse…';
  spin.style.display = 'block';
  res.style.display  = 'none';

  try {
    const fd = new FormData();
    fd.append('file', input.files[0]);
    const r = await fetch('/rag/check-file', { method: 'POST', body: fd });
    const d = await r.json();

    if (!r.ok) {
      res.innerHTML     = '<p style="color:#f87171;font-size:.8rem">' + (d.detail || 'Erreur serveur') + '</p>';
      res.style.display = 'block';
    } else {
      // Affiche résumé inline
      window._lastDriftData = d;
      res.innerHTML     = renderDrift(d);
      res.style.display = 'block';
      res.insertAdjacentHTML('afterbegin',
        '<div style="display:flex;gap:8px;margin-bottom:12px">'
        + '<button class="btn-dl" onclick="openDriftReport(window._lastDriftData)">Ouvrir le rapport</button>'
        + '<button class="btn-dl btn-dl-pdf" onclick="downloadDriftReport(window._lastDriftData)">Télécharger HTML</button>'
        + '</div>');
      await loadStats();
    }
  } catch(e) {
    res.innerHTML     = '<p style="color:#f87171;font-size:.8rem">Erreur réseau : ' + e.message + '</p>';
    res.style.display = 'block';
  } finally {
    spin.style.display = 'none';
    btn.disabled = false; btn.textContent = 'Vérifier la dérive sémantique';
  }
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function buildDriftReportPage(d) {
  const derive   = d.derive_detectee;
  const noCorpus = d.nb_corpus === 0;
  const jsd      = d.jsd || 0;
  const thr      = d.threshold || 0;
  const verdict  = noCorpus ? 'Premier document' : derive ? 'Dérive détectée' : 'Contenu conforme';
  const verdCol  = noCorpus ? '#fbbf24' : derive ? '#f87171' : '#4ade80';
  const now      = new Date().toLocaleString('fr-FR');

  // Jauge JSD
  const jsdPct = Math.round(Math.min(jsd, 1) * 100);
  const thrPct = Math.round(Math.min(thr, 1) * 100);
  const barCol = derive ? '#ef4444' : '#22c55e';

  // Mots dérivants
  let motsTbody = '';
  if (derive && (d.mots_derives || []).length) {
    d.mots_derives.forEach(function(w) {
      var absent = w.freq_corpus < 0.001;
      var surep  = !absent && w.freq_doc > w.freq_corpus * 1.5;
      var col    = absent ? '#f87171' : surep ? '#fbbf24' : '#e2e8f0';
      motsTbody += '<tr>'
        + '<td style="font-weight:700;color:' + col + '">' + escHtml(w.mot) + '</td>'
        + '<td style="text-align:right;color:#a5b4fc">' + w.contribution_jsd.toFixed(2) + '%</td>'
        + '<td style="text-align:right">' + w.freq_doc.toFixed(2) + '%</td>'
        + '<td style="text-align:right">' + w.freq_corpus.toFixed(2) + '%</td>'
        + '<td style="color:#94a3b8;font-size:.85em">' + escHtml(w.explication) + '</td>'
        + '</tr>';
    });
  }

  // Docs similaires
  let simRows = '';
  (d.documents_similaires || []).forEach(function(s) {
    simRows += '<tr>'
      + '<td>' + escHtml(s.source || s.doc_id) + '</td>'
      + '<td style="text-align:right;font-weight:700;color:#818cf8">' + escHtml(s.similarity) + '%</td>'
      + '</tr>';
  });

  return '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
    + '<title>Rapport dérive sémantique — ' + escHtml(d.fichier || '') + '</title>'
    + '<style>'
    + '* { box-sizing:border-box; margin:0; padding:0; }'
    + 'body { background:#0f172a; color:#e2e8f0; font-family:system-ui,sans-serif; padding:40px 32px; max-width:860px; margin:0 auto; }'
    + 'h1 { font-size:1.2rem; font-weight:700; color:#f8fafc; margin-bottom:4px; }'
    + '.meta { font-size:.75rem; color:#475569; margin-bottom:28px; }'
    + '.section { background:#1e293b; border:1px solid #334155; border-radius:10px; padding:22px 24px; margin-bottom:18px; }'
    + '.section-title { font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#64748b; margin-bottom:14px; }'
    + '.verdict { font-size:1.4rem; font-weight:800; margin-bottom:6px; }'
    + '.jsd-track { background:#0f172a; border-radius:99px; height:12px; position:relative; overflow:visible; margin:10px 0 4px; }'
    + '.jsd-bar   { height:12px; border-radius:99px; }'
    + '.jsd-needle { position:absolute; top:-5px; width:2px; height:22px; background:#f59e0b; border-radius:1px; transform:translateX(-50%); }'
    + 'table { width:100%; border-collapse:collapse; font-size:.82rem; }'
    + 'th { text-align:left; color:#475569; font-weight:600; padding:6px 10px; border-bottom:1px solid #1e3a5f; font-size:.72rem; text-transform:uppercase; }'
    + 'td { padding:7px 10px; border-bottom:1px solid #0a1929; vertical-align:top; }'
    + 'tr:last-child td { border-bottom:none; }'
    + 'tr:hover td { background:#162032; }'
    + '@media print { body { background:#fff; color:#000; } .section { border:1px solid #ccc; } }'
    + '</style></head><body>'
    + '<h1>Rapport — Dérive sémantique</h1>'
    + '<div class="meta">Fichier : <strong>' + escHtml(d.fichier || '—') + '</strong>'
    + ' &nbsp;·&nbsp; Type : ' + escHtml(d.doc_type || '—')
    + ' &nbsp;·&nbsp; Corpus : ' + d.nb_corpus + ' doc(s)'
    + ' &nbsp;·&nbsp; ' + now + '</div>'

    + '<div class="section">'
    + '<div class="section-title">Verdict</div>'
    + '<div class="verdict" style="color:' + verdCol + '">' + verdict + '</div>'
    + (d.reason ? '<p style="font-size:.84rem;color:#94a3b8;margin-top:8px">' + escHtml(d.reason) + '</p>' : '')
    + '</div>'

    + (noCorpus ? '' :
      '<div class="section">'
      + '<div class="section-title">Score JSD</div>'
      + '<div style="display:flex;justify-content:space-between;font-size:.8rem;color:#94a3b8;margin-bottom:6px">'
      + '<span>JSD = <strong style="color:#f1f5f9">' + jsd.toFixed(4) + '</strong></span>'
      + '<span>Seuil adaptatif = ' + thr.toFixed(4) + '</span></div>'
      + '<div class="jsd-track">'
      + '<div class="jsd-bar" style="width:' + jsdPct + '%;background:' + barCol + '"></div>'
      + '<div class="jsd-needle" style="left:' + thrPct + '%"></div>'
      + '</div>'
      + '<div style="font-size:.68rem;color:#475569;text-align:right;margin-top:2px">▲ seuil adaptatif</div>'
      + '</div>')

    + (motsTbody ?
      '<div class="section">'
      + '<div class="section-title">Mots &#224; l&#39;origine de la d&#233;rive (top 10)</div>'
      + '<table><thead><tr><th>Mot</th><th style="text-align:right">Contribution</th>'
      + '<th style="text-align:right">Fréq. doc</th><th style="text-align:right">Fréq. corpus</th><th>Explication</th></tr></thead>'
      + '<tbody>' + motsTbody + '</tbody></table>'
      + '</div>' : '')

    + (simRows ?
      '<div class="section">'
      + '<div class="section-title">Documents similaires dans le corpus</div>'
      + '<table><thead><tr><th>Document</th><th style="text-align:right">Similarité</th></tr></thead>'
      + '<tbody>' + simRows + '</tbody></table>'
      + '</div>' : '')

    + '<div style="font-size:.68rem;color:#334155;text-align:center;margin-top:24px">'
    + 'Pare-feu Sémantique DD — généré le ' + now
    + '</div>'
    + '</body></html>';
}

function renderDrift(d) {
  const jsd       = d.jsd || 0;
  const threshold = d.threshold || 0;
  const derive    = d.derive_detectee;
  const noCorpus  = d.nb_corpus === 0;

  // badge
  let badge;
  if (noCorpus) {
    badge = '<span class="badge badge-amber">Corpus vide — premier document</span>';
  } else if (derive) {
    badge = '<span class="badge badge-red">⚠ Dérive sémantique détectée</span>';
  } else {
    badge = '<span class="badge badge-green">✓ Contenu similaire au corpus</span>';
  }

  // jauge JSD
  const jsdPct  = Math.round(Math.min(jsd, 1) * 100);
  const thrPct  = Math.round(Math.min(threshold, 1) * 100);
  const barCol  = derive ? '#ef4444' : '#22c55e';
  const jaugeHtml = noCorpus ? '' : `
    <div class="jsd-wrap">
      <div class="jsd-label">
        <span>JSD = <strong>${jsd.toFixed(3)}</strong></span>
        <span>seuil = ${threshold.toFixed(3)}</span>
      </div>
      <div class="jsd-track">
        <div class="jsd-bar" style="width:${jsdPct}%;background:${barCol}"></div>
        <div class="jsd-needle" style="left:${thrPct}%"></div>
      </div>
      <div style="font-size:.67rem;color:#475569;margin-top:3px;text-align:right">
        ▲ seuil adaptatif
      </div>
    </div>`;

  // type détecté
  const typeHtml = `<div style="font-size:.74rem;color:#64748b;margin-bottom:8px">
    Type détecté : <strong style="color:#94a3b8">${d.doc_type || '—'}</strong>
    &nbsp;·&nbsp; Corpus : ${d.nb_corpus} doc(s)
  </div>`;

  // reason
  const reasonHtml = d.reason
    ? `<p class="reason-txt">${d.reason}</p>` : '';

  // docs similaires
  let simHtml = '';
  if ((d.documents_similaires || []).length) {
    simHtml = '<div class="similar-title">Documents similaires</div>';
    d.documents_similaires.forEach(s => {
      simHtml += `<div class="sim-row">
        <span class="sim-name" title="${s.source}">${s.source || s.doc_id}</span>
        <span class="sim-pct">${s.similarity}%</span>
      </div>`;
    });
  } else if (!noCorpus) {
    simHtml = '<p class="no-corpus">Aucun document similaire dans le corpus</p>';
  }

  // mots derivants
  let motsDeriveHtml = '';
  if (derive && (d.mots_derives || []).length) {
    motsDeriveHtml += '<div class="drift-words-wrap">';
    motsDeriveHtml += '<div class="drift-words-title">Mots a l&#39;origine de la derive</div>';
    motsDeriveHtml += '<table class="drift-words-table"><thead><tr>'
      + '<th>Mot</th><th>Contribution JSD</th><th>Freq. doc</th><th>Freq. corpus</th><th>Pourquoi</th>'
      + '</tr></thead><tbody>';
    d.mots_derives.forEach(function(w) {
      var absent = w.freq_corpus < 0.001;
      var surep  = !absent && w.freq_doc > w.freq_corpus * 1.5;
      var cls    = absent ? 'mot-absent' : surep ? 'mot-surep' : 'mot-normal';
      motsDeriveHtml += '<tr>'
        + '<td><span class="' + cls + '">' + w.mot + '</span></td>'
        + '<td style="text-align:right">' + w.contribution_jsd.toFixed(2) + '%</td>'
        + '<td style="text-align:right">' + w.freq_doc.toFixed(2) + '%</td>'
        + '<td style="text-align:right">' + w.freq_corpus.toFixed(2) + '%</td>'
        + '<td class="mot-expl">' + w.explication + '</td>'
        + '</tr>';
    });
    motsDeriveHtml += '</tbody></table></div>';
  }

  return badge + typeHtml + jaugeHtml + reasonHtml + motsDeriveHtml + simHtml;
}

function openDriftReport(d) {
  const win = window.open('', '_blank');
  if (!win) { alert('Popup bloqué — autorisez les popups pour ce site.'); return; }
  win.document.open();
  win.document.write(buildDriftReportPage(d));
  win.document.close();
}

function downloadDriftReport(d) {
  const html = buildDriftReportPage(d);
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'rapport_derive_' + (d.fichier || 'document').replace(/[^a-z0-9_.\-]/gi, '_') + '.html';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ==========================================
# HEALTH
# ==========================================

@app.get("/health", tags=["Monitoring"])
def health():
    return {"statut": "ok", "service": "Pare-feu Sémantique DD v1.0"}


@app.get("/files/{filename}", tags=["Monitoring"])
def serve_sample(filename: str):
    """
    Sert un fichier depuis samples/dd/ — utile pour tester /webhook/ingest
    sans serveur externe.
    Exemple : GET /files/bilan_exemple.txt
    """
    # Sécurité : extrait uniquement le nom de fichier, refuse les traversées de répertoire
    safe = Path(filename).name
    if not safe or safe != filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    for base in (Path("samples/dd"), Path("samples")):
        p = base / safe
        # Double vérification : le chemin résolu doit rester sous samples/
        try:
            p.resolve().relative_to(base.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Accès refusé.")
        if p.exists() and p.is_file():
            return FileResponse(str(p))
    raise HTTPException(status_code=404, detail=f"Fichier '{safe}' introuvable.")


# ==========================================
# CERTIFICATION — UN DOCUMENT
# ==========================================

@app.post("/certifier/document", tags=["Certification"])
async def certifier_document(file: UploadFile = File(...)):
    """
    Certifie un seul document uploadé.

    Retourne le résultat de certification JSON avec :
    - statut : CERTIFIÉ | ANOMALIE | ERREUR
    - anomalies : liste des contrôles en échec
    - champs_extraits : valeurs extraites par le LLM
    - score_confiance : % de contrôles passés
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    _check_file_size(content)
    safe_name = _safe_filename(file.filename or "document")

    result = certify_document_from_bytes(safe_name, content)
    status_code = 200 if result["statut"] in ("CERTIFIÉ", "VIGILANCE", "ANOMALIE") else 422
    return JSONResponse(content=result, status_code=status_code)


# ==========================================
# CERTIFICATION — DOSSIER COMPLET
# ==========================================

@app.post("/certifier/dossier", tags=["Certification"])
async def certifier_dossier(
    entreprise: str = Form(default=""),
    files: List[UploadFile] = File(...),
):
    """
    Certifie un dossier DD complet (bilan + P&L + captable ou factures).

    Persiste le rapport dans output/dossiers/{dossier_id}.json.
    Retourne le rapport consolidé avec verdict global et liste des anomalies.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier fourni.")

    tmp_dir = Path("output/tmp")
    tmp_dir.mkdir(exist_ok=True)
    tmp_paths = []

    try:
        for upload in files:
            content = await upload.read()
            if not content:
                continue
            _check_file_size(content)
            safe_name = _safe_filename(upload.filename or "document")
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(content)
            tmp_paths.append(str(tmp_path))

        if not tmp_paths:
            raise HTTPException(status_code=400, detail="Tous les fichiers sont vides.")

        rapport = certify_dossier(tmp_paths, entreprise)
    finally:
        for p in tmp_paths:
            try:
                Path(p).unlink()
            except Exception:
                pass

    status_code = 200 if rapport["verdict"] in ("CERTIFIÉ", "VIGILANCE") else 207

    # Push vers tous les connecteurs DD configurés (non bloquant)
    connector_results = get_manager().push_dossier(rapport)
    rapport["connectors"] = connector_results

    return JSONResponse(content=rapport, status_code=status_code)


# ==========================================
# CONNECTEURS — STATUT
# ==========================================

@app.get("/connectors/status", tags=["Connecteurs"])
def connectors_status():
    """
    Retourne l'état de configuration de chaque connecteur DD.

    Un connecteur est « configuré » si toutes ses variables d'environnement
    sont présentes (token, IDs, URL webhook…).
    """
    return JSONResponse(content=get_manager().status())


# ==========================================
# WEBHOOK INGESTION — DATA ROOM
# ==========================================

class IngestRequest(BaseModel):
    file_url:       str
    filename:       str
    entreprise_nom: str  = ""
    source:         str  = "data_room"


@app.post("/webhook/ingest", tags=["Connecteurs"])
async def webhook_ingest(req: IngestRequest):
    """
    Point d'entrée pour les webhooks de data room (Datasite, Intralinks, DocSend…).

    La data room envoie un JSON :
      {
        "file_url":       "https://datasite.com/files/bilan.pdf",
        "filename":       "bilan_techventure_2025.pdf",
        "entreprise_nom": "TechVenture SAS",
        "source":         "datasite"
      }

    Le pare-feu :
      1. Télécharge le fichier
      2. Le certifie (pipeline complet LLM + DDTaxonomy + JSD)
      3. Pousse le résultat vers Notion, Airtable, Teams, SharePoint
    """
    import httpx

    # Sécurité SSRF : bloque les URLs vers des ressources réseau internes
    _check_webhook_url(req.file_url)
    safe_name = _safe_filename(req.filename or "document")

    # 1. Téléchargement
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            resp = await client.get(req.file_url)
            resp.raise_for_status()
            content = resp.content
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Téléchargement échoué : {exc}")

    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide après téléchargement.")
    _check_file_size(content)

    # 2. Certification
    cert = certify_document_from_bytes(safe_name, content)

    # 3. Push connecteurs (si dossier = 1 document, on crée un rapport minimal)
    rapport_minimal = {
        "dossier_id":     cert.get("horodatage", "").replace(":", "").replace("-", ""),
        "entreprise_nom": req.entreprise_nom,
        "horodatage":     cert.get("horodatage", ""),
        "modele":         cert.get("modele", ""),
        "verdict":        cert.get("statut", ""),
        "nb_documents":   1,
        "nb_certifies":   1 if cert.get("statut") == "CERTIFIÉ" else 0,
        "nb_vigilance":   1 if cert.get("statut") == "VIGILANCE" else 0,
        "nb_anomalies":   1 if cert.get("statut") == "ANOMALIE" else 0,
        "nb_erreurs":     1 if cert.get("statut") == "ERREUR" else 0,
        "anomalies":      [{"document": req.filename, **a} for a in cert.get("anomalies", [])],
        "certifications": [cert],
    }
    connector_results = get_manager().push_dossier(rapport_minimal)

    return JSONResponse(content={
        "source":        req.source,
        "fichier":       req.filename,
        "statut_parefeu": cert.get("statut", "ERREUR"),
        "certification": cert,
        "connectors":    connector_results,
    })


# ==========================================
# WEBHOOK INGESTION — UPLOAD DIRECT (multipart)
# ==========================================

@app.post("/webhook/ingest-file", tags=["Connecteurs"])
async def webhook_ingest_file(
    file:           UploadFile = File(...),
    entreprise_nom: str        = Form(default=""),
):
    """
    Ingestion directe par upload multipart — pour les scripts d'audit en lot.

    L'analyste (ou un script automatisé) envoie le fichier physique :
      POST /webhook/ingest-file
        file=<fichier>
        entreprise_nom=<nom optionnel>

    Le pare-feu :
      1. Certifie le document (pipeline LLM + DDTaxonomy + JSD)
      2. Pousse les alertes vers Notion, Airtable, Teams, SharePoint
      3. Retourne statut_parefeu + détail complet

    Utilisé par test_batch_api.py pour l'audit automatique de masse.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    _check_file_size(content)
    safe_name = _safe_filename(file.filename or "document")

    cert = certify_document_from_bytes(safe_name, content)

    rapport_minimal = {
        "dossier_id":     (cert.get("horodatage") or "").replace(":", "").replace("-", ""),
        "entreprise_nom": entreprise_nom,
        "horodatage":     cert.get("horodatage", ""),
        "modele":         cert.get("modele", ""),
        "verdict":        cert.get("statut", ""),
        "nb_documents":   1,
        "nb_certifies":   1 if cert.get("statut") == "CERTIFIÉ" else 0,
        "nb_vigilance":   1 if cert.get("statut") == "VIGILANCE" else 0,
        "nb_anomalies":   1 if cert.get("statut") == "ANOMALIE" else 0,
        "nb_erreurs":     1 if cert.get("statut") == "ERREUR" else 0,
        "anomalies":      [{"document": safe_name, **a} for a in cert.get("anomalies", [])],
        "certifications": [cert],
    }

    # Push alertes uniquement si anomalie ou erreur
    connector_results = {}
    if cert.get("statut") in ("ANOMALIE", "ERREUR"):
        connector_results = get_manager().push_dossier(rapport_minimal)

    statut = cert.get("statut", "ERREUR")
    anomalies = cert.get("anomalies", [])
    nb_anomalies = len(anomalies)

    if statut == "CERTIFIÉ":
        message = "Document conforme — aucune anomalie détectée"
    elif statut == "VIGILANCE":
        message = "Document certifié avec dérive sémantique — vigilance recommandée"
    elif statut == "ANOMALIE":
        message = f"{nb_anomalies} anomalie(s) détectée(s) — alertes envoyées"
    else:
        message = "Erreur d'analyse — document non reconnu ou extraction échouée"

    status_code = 200 if statut in ("CERTIFIÉ", "VIGILANCE", "ANOMALIE") else 422
    return JSONResponse(
        content={
            "fichier":        safe_name,
            "statut_parefeu": statut,
            "message":        message,
            "anomalies":      anomalies,
            "doc_type":       cert.get("doc_type", "inconnu"),
            "score_confiance": cert.get("score_confiance"),
            "connectors":     connector_results,
        },
        status_code=status_code,
    )


# ==========================================
# WORKFLOW — FORMAT D'ÉCHANGE & ALERTES
# ==========================================

@app.get("/workflow/schema", tags=["Workflow"])
def workflow_schema():
    """
    Retourne le schéma canonique du format d'échange entre le pare-feu et
    les outils de workflow (Teams, Notion, Airtable, SharePoint).

    Contient :
    - La définition des niveaux d'alerte (BLOQUANT → INFO)
    - Le catalogue de contrôles avec action requise par niveau
    - Les règles de routing par outil
    - Un exemple de payload complet
    """
    return JSONResponse(content=WORKFLOW_SCHEMA)


@app.post("/workflow/certifier", tags=["Workflow"])
async def workflow_certifier(
    entreprise: str = Form(default=""),
    files: List[UploadFile] = File(...),
):
    """
    Certifie un dossier DD et retourne directement le WorkflowEvent classifié.

    Identique à POST /certifier/dossier mais la réponse est le format d'échange
    canonique (event_type, alert_level, alertes classées, action_globale…)
    prêt à être consommé par les outils de workflow des analystes.

    Pousse également vers tous les connecteurs configurés.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier fourni.")

    tmp_dir = Path("output/tmp")
    tmp_dir.mkdir(exist_ok=True)
    tmp_paths = []

    try:
        for upload in files:
            content = await upload.read()
            if not content:
                continue
            _check_file_size(content)
            safe_name = _safe_filename(upload.filename or "document")
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(content)
            tmp_paths.append(str(tmp_path))

        if not tmp_paths:
            raise HTTPException(status_code=400, detail="Tous les fichiers sont vides.")

        rapport = certify_dossier(tmp_paths, entreprise)
    finally:
        for p in tmp_paths:
            try:
                Path(p).unlink()
            except Exception:
                pass

    # Classifie les alertes + enrichit le rapport
    event            = classify_alerts(rapport)
    event_dict       = event.to_dict()
    rapport_enriched = {**rapport, "workflow_event": event_dict}

    # Push connecteurs
    connector_results = get_manager().push_dossier(rapport_enriched)

    status_code = 200 if rapport["verdict"] in ("CERTIFIÉ", "VIGILANCE") else 207
    return JSONResponse(
        content={**event_dict, "connectors": connector_results},
        status_code=status_code,
    )


@app.get("/workflow/alerts/{dossier_id}", tags=["Workflow"])
def workflow_alerts(dossier_id: str):
    """
    Extrait et classe les alertes d'un dossier déjà certifié (persisté sur disque).

    Retourne le WorkflowEvent complet avec :
    - alert_level : niveau maximal détecté
    - alertes     : liste triée BLOQUANT → INFO avec action_requise par alerte
    - resume      : compteurs par niveau + action_globale
    - documents   : résumé par document

    Utile pour consulter les alertes d'un dossier sans recertifier.
    """
    path = DOSSIERS_DIR / f"{dossier_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dossier '{dossier_id}' introuvable.")

    rapport = json.loads(path.read_text(encoding="utf-8"))
    event   = classify_alerts(rapport)
    return JSONResponse(content=event.to_dict())


# ==========================================
# LECTURE D'UN DOSSIER PERSISTÉ
# ==========================================

@app.get("/dossiers/{dossier_id}", tags=["Dossiers"])
def get_dossier(dossier_id: str):
    """
    Récupère un rapport de dossier précédemment certifié.
    """
    path = DOSSIERS_DIR / f"{dossier_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dossier '{dossier_id}' introuvable.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


@app.get("/dossiers/{dossier_id}/rapport", tags=["Dossiers"])
def get_dossier_rapport(dossier_id: str):
    """
    Retourne le rapport HTML du dossier — visualisable directement dans le navigateur.
    Bilan (barres empilées), P&L (cascade), Cap Table (donut), anomalies classées.
    Utilisez Ctrl+P dans le navigateur pour exporter en PDF.
    """
    path = DOSSIERS_DIR / f"{dossier_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dossier '{dossier_id}' introuvable.")
    rapport = json.loads(path.read_text(encoding="utf-8"))
    html = build_html(rapport)
    return HTMLResponse(content=html)


@app.get("/dossiers/{dossier_id}/pdf", tags=["Dossiers"])
def get_dossier_pdf(dossier_id: str):
    """
    Génère et télécharge le rapport PDF du dossier (via Edge headless).
    Retourne 503 si Edge n'est pas installé sur le serveur.
    """
    path = DOSSIERS_DIR / f"{dossier_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dossier '{dossier_id}' introuvable.")
    _, pdf_path = generate_dd_report(str(path))
    if not pdf_path.exists():
        raise HTTPException(
            status_code=503,
            detail="PDF non disponible : Edge introuvable sur ce serveur. Utilisez /rapport + Ctrl+P.",
        )
    ent = json.loads(path.read_text(encoding="utf-8")).get("entreprise_nom") or dossier_id[:8]
    filename = f"rapport_dd_{ent.replace(' ', '_')[:30]}.pdf"
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=filename)


@app.post("/certifier/dossier/rapport", tags=["Certification"])
async def certifier_dossier_rapport(
    entreprise: str = Form(default=""),
    files: List[UploadFile] = File(...),
):
    """
    Certifie un dossier DD et retourne directement le rapport HTML.
    Même traitement que POST /certifier/dossier mais la réponse est
    visualisable dans le navigateur (Ctrl+P pour PDF).
    """
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier fourni.")

    tmp_dir = Path("output/tmp")
    tmp_dir.mkdir(exist_ok=True)
    tmp_paths = []

    try:
        for upload in files:
            content = await upload.read()
            if not content:
                continue
            _check_file_size(content)
            safe_name = _safe_filename(upload.filename or "document")
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(content)
            tmp_paths.append(str(tmp_path))

        if not tmp_paths:
            raise HTTPException(status_code=400, detail="Tous les fichiers sont vides.")

        rapport = certify_dossier(tmp_paths, entreprise)
    finally:
        for p in tmp_paths:
            try:
                Path(p).unlink()
            except Exception:
                pass

    html = build_html(rapport)
    return HTMLResponse(content=html)


@app.get("/dossiers", tags=["Dossiers"])
def list_dossiers():
    """Liste tous les dossiers certifiés avec résumé (entreprise, verdict, date, nb docs)."""
    results = []
    for p in sorted(DOSSIERS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            results.append({
                "dossier_id":    p.stem,
                "entreprise_nom": data.get("entreprise_nom") or "Sans nom",
                "verdict":       data.get("verdict") or "—",
                "horodatage":    (data.get("horodatage") or "")[:10],
                "nb_documents":  int(data.get("nb_documents") or 0),
                "nb_anomalies":  int(data.get("nb_anomalies") or 0),
            })
        except Exception:
            results.append({"dossier_id": p.stem, "entreprise_nom": "—", "verdict": "ERREUR",
                            "horodatage": "", "nb_documents": 0, "nb_anomalies": 0})
    return {"dossiers": results, "total": len(results)}


@app.delete("/dossiers/{dossier_id}", tags=["Dossiers"])
def delete_dossier(dossier_id: str):
    """Supprime un dossier certifié (JSON + HTML + PDF associés)."""
    path = DOSSIERS_DIR / f"{dossier_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dossier '{dossier_id}' introuvable.")
    path.unlink()
    for ext in [".html", ".pdf"]:
        p = path.with_suffix(ext)
        if p.exists():
            p.unlink()
    return {"statut": "ok", "message": f"Dossier '{dossier_id[:8]}…' supprimé."}


# ==========================================
# RAG — MODÈLES
# ==========================================

class QueryRequest(BaseModel):
    text: str
    doc_type: Optional[str] = None
    top_k: int = 3


# ==========================================
# RAG — INDEXATION (data room)
# ==========================================

@app.post("/rag/index", tags=["RAG"])
async def rag_index(
    deal: str = Form(default=""),
    files: List[UploadFile] = File(...),
):
    """
    Indexe une data room complète dans le corpus RAG.

    Chaque fichier passe par le gate JSD statique (seuil = 0.55) :
    - JSD ≤ 0.55 → conforme au corpus de référence → indexé
    - JSD > 0.55 → dérive de distribution lexicale détectée → exclu

    Retourne le rapport d'indexation avec le détail par fichier.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier fourni.")

    tmp_dir = Path("output/tmp")
    tmp_dir.mkdir(exist_ok=True)
    tmp_paths = []

    try:
        for upload in files:
            content = await upload.read()
            if not content:
                continue
            _check_file_size(content)
            safe_name = _safe_filename(upload.filename or "document")
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(content)
            tmp_paths.append(str(tmp_path))

        if not tmp_paths:
            raise HTTPException(status_code=400, detail="Tous les fichiers sont vides.")

        corpus = RagCorpus.load()
        rapport = corpus.index_batch(tmp_paths, deal=deal)
    finally:
        for p in tmp_paths:
            try:
                Path(p).unlink()
            except Exception:
                pass

    return JSONResponse(content=rapport)


# ==========================================
# RAG — STATISTIQUES
# ==========================================

@app.get("/rag/stats", tags=["RAG"])
def rag_stats():
    """
    Retourne les statistiques du corpus RAG :
    - Nombre total de documents indexés
    - Par cluster : count, deals, seuil adaptatif en vigueur
    """
    corpus = RagCorpus.load()
    return JSONResponse(content=corpus.stats())


# ==========================================
# RAG — REQUÊTE
# ==========================================

@app.post("/rag/query", tags=["RAG"])
def rag_query(req: QueryRequest):
    """
    Recherche les documents les plus similaires dans le corpus RAG.

    Body JSON :
      {
        "text":     "bilan actif immobilisé résultat net",
        "doc_type": "bilan",     (optionnel — filtre par type)
        "top_k":    3            (optionnel — nombre de résultats)
      }

    Retourne une liste triée par similarité cosinus décroissante.
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Le champ 'text' est vide.")

    corpus = RagCorpus.load()
    hits   = corpus.query(req.text, doc_type=req.doc_type, top_k=req.top_k)

    return JSONResponse(content={"resultats": hits, "total": len(hits)})


# ==========================================
# RAG — RESET
# ==========================================

@app.post("/rag/check-file", tags=["RAG"])
async def rag_check_file(file: UploadFile = File(...)):
    """
    Vérifie la dérive sémantique d'un fichier par rapport au corpus de référence.
    Utilise SemanticMonitor (min-JSD par document, seuil fixe 0.55) pour le score JSD.
    Le corpus RAG n'est utilisé que pour les documents similaires.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    _check_file_size(content)
    safe_name = _safe_filename(file.filename or "document")

    from detect_doc_type import detect_doc_type
    from semantic_monitor import get_monitor, _jsd_contributors, _tokenize, _word_freq

    # Extraction texte : PDF/binaires via llm_extractor, texte brut sinon
    suffix = Path(safe_name).suffix.lower()
    if suffix in (".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"):
        tmp_dir = Path("output") / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        tmp_path = tmp_dir / safe_name
        tmp_path.write_bytes(content)
        try:
            from llm_extractor import extract_text_from_file
            text = extract_text_from_file(str(tmp_path))
        except Exception as e:
            try:
                tmp_path.unlink()
            except Exception:
                pass
            raise HTTPException(status_code=422, detail=f"Extraction texte impossible : {e}")
        else:
            try:
                tmp_path.unlink()
            except Exception:
                pass
    else:
        text = content.decode("utf-8", errors="replace")

    doc_type = detect_doc_type(safe_name, text[:500])
    monitor  = get_monitor()

    if doc_type == "inconnu":
        jsd_val   = 1.0
        threshold = monitor.JSD_ALERT_THRESHOLD
        derive    = True
        reason    = "Type de document non reconnu."
        nb_corpus = 0
    else:
        analysis  = monitor.analyze(text, doc_type)
        jsd_val   = analysis["jsd_score"]
        threshold = monitor.JSD_ALERT_THRESHOLD
        derive    = analysis["jsd_alert"]
        nb_corpus = sum(len(v) for v in monitor._jsd_corpus.values())
        reason = (
            f"JSD={jsd_val:.3f} > seuil={threshold:.3f} → dérive de distribution lexicale détectée."
            if derive else
            f"JSD={jsd_val:.3f} ≤ seuil={threshold:.3f} → distribution lexicale conforme aux références."
        )

    corpus  = RagCorpus.load()
    similar = corpus.query(text, doc_type=doc_type if doc_type != "inconnu" else None, top_k=5)

    mots_derives: list = []
    if derive and doc_type != "inconnu":
        ref_freq = monitor._ref_freq.get(doc_type, {})
        if ref_freq:
            freq         = _word_freq(_tokenize(text))
            mots_derives = _jsd_contributors(freq, ref_freq, top_k=10)

    return JSONResponse(content={
        "fichier":               safe_name,
        "doc_type":              doc_type,
        "jsd":                   round(jsd_val, 4),
        "threshold":             round(threshold, 4),
        "derive_detectee":       derive,
        "reason":                reason,
        "documents_similaires":  similar,
        "nb_corpus":             nb_corpus,
        "mots_derives":          mots_derives,
    })


@app.delete("/rag/reset", tags=["RAG"])
def rag_reset():
    """Réinitialise le corpus RAG."""
    if CORPUS_PATH.exists():
        CORPUS_PATH.unlink()
        return {"statut": "ok", "message": "Corpus RAG réinitialisé."}
    return {"statut": "ok", "message": "Corpus déjà vide."}


# ==========================================
# MONITOR SÉMANTIQUE — STATS / REBUILD
# ==========================================

@app.get("/monitor/stats", tags=["RAG"])
def monitor_stats():
    """Statistiques du SemanticMonitor (corpus de référence JSD)."""
    from semantic_monitor import get_monitor
    monitor = get_monitor()
    refs = {
        f"{dt} [{lang}]": len(freqs)
        for (dt, lang), freqs in monitor._ref_freqs_list.items()
    }
    return {
        "total": sum(refs.values()),
        "par_type": refs,
        "seuil": monitor.JSD_ALERT_THRESHOLD,
    }


@app.post("/monitor/rebuild", tags=["RAG"])
def monitor_rebuild():
    """
    Reconstruit le SemanticMonitor depuis les fichiers samples/.
    À appeler après avoir ajouté de nouveaux documents dans samples/references/.
    """
    from semantic_monitor import reset_monitor, get_monitor
    reset_monitor()
    monitor = get_monitor()
    refs = {
        f"{dt} [{lang}]": len(freqs)
        for (dt, lang), freqs in monitor._ref_freqs_list.items()
    }
    total = sum(refs.values())
    return {
        "statut":  "ok",
        "message": f"Monitor reconstruit — {total} documents de référence chargés.",
        "total":   total,
        "par_type": refs,
        "seuil":   monitor.JSD_ALERT_THRESHOLD,
    }

