"""
Connecteur Microsoft Teams — envoie une Adaptive Card dans un canal.

Routing par niveau d'alerte :
  BLOQUANT  → style "attention" (rouge) + bandeau 🚨 INSTRUCTION BLOQUÉE
  CRITIQUE  → style "attention" (rouge) + bandeau 🔴 VÉRIFICATION OBLIGATOIRE
  HAUTE     → style "warning"   (orange)
  MOYENNE   → style "warning"   (orange)
  INFO / OK → style "good"      (vert)

Variables d'environnement :
  TEAMS_WEBHOOK_URL   URL du webhook entrant configuré dans le canal Teams
"""

import os
import requests
from .base import DDConnector

_LEVEL_STYLE = {
    "BLOQUANT": "attention",
    "CRITIQUE": "attention",
    "HAUTE":    "warning",
    "MOYENNE":  "warning",
    "INFO":     "good",
}
_VERDICT_STYLE = {
    "CERTIFIÉ": "good",
    "ANOMALIE": "warning",
    "ERREUR":   "attention",
}


class TeamsConnector(DDConnector):

    def name(self) -> str:
        return "Teams"

    def is_configured(self) -> bool:
        return bool(os.getenv("TEAMS_WEBHOOK_URL"))

    def _build_card(self, rapport: dict) -> dict:
        # Utilise le WorkflowEvent enrichi si disponible
        wf = rapport.get("workflow_event") or {}

        ent         = rapport.get("entreprise_nom") or "Sans nom"
        verdict     = rapport.get("verdict") or "—"
        date_raw    = (rapport.get("horodatage") or "")[:10]
        nb_docs     = int(rapport.get("nb_documents") or 0)
        nb_cert     = int(rapport.get("nb_certifies") or 0)
        modele      = rapport.get("modele") or ""
        alert_level = wf.get("alert_level", "")
        score_moyen = wf.get("score_moyen") or 0.0
        resume      = wf.get("resume") or {}
        action_glob = resume.get("action_globale", "")

        style = _LEVEL_STYLE.get(alert_level) or _VERDICT_STYLE.get(verdict, "default")

        body: list = []

        # ── Bandeau d'alerte prioritaire ──────────────────────────────────────
        if alert_level in ("BLOQUANT", "CRITIQUE"):
            header_text = {
                "BLOQUANT": "🚨  INSTRUCTION BLOQUÉE — Anomalie critique",
                "CRITIQUE": "🔴  VÉRIFICATION OBLIGATOIRE",
            }[alert_level]
            body.append({
                "type": "TextBlock",
                "text": header_text,
                "weight": "Bolder", "size": "Large",
                "color": "Attention", "wrap": True,
            })

        body.append({
            "type": "TextBlock",
            "text": f"Rapport DD — {ent}",
            "weight": "Bolder", "size": "Medium", "wrap": True,
            "spacing": "None" if alert_level in ("BLOQUANT", "CRITIQUE") else "Default",
        })

        # ── KPIs ─────────────────────────────────────────────────────────────
        facts = [
            {"title": "Verdict",       "value": verdict},
            {"title": "Niveau alerte", "value": alert_level or verdict},
            {"title": "Score moyen",   "value": f"{score_moyen}%"},
            {"title": "Documents",     "value": f"{nb_cert}/{nb_docs} certifiés"},
            {"title": "Date",          "value": date_raw},
            {"title": "Modèle",        "value": modele},
        ]
        body.append({"type": "FactSet", "facts": facts})

        # ── Action globale ────────────────────────────────────────────────────
        if action_glob:
            body.append({
                "type": "TextBlock",
                "text": action_glob,
                "wrap": True, "spacing": "Medium",
                "color": "Attention" if alert_level in ("BLOQUANT", "CRITIQUE") else "Default",
            })

        # ── Résumé des niveaux ────────────────────────────────────────────────
        if resume:
            level_parts = []
            for key, label in [("nb_bloquants","🚨 Bloquant"), ("nb_critiques","🔴 Critique"),
                                ("nb_hautes","⚠️ Haute"), ("nb_moyennes","📋 Moyenne")]:
                n = resume.get(key, 0)
                if n:
                    level_parts.append(f"{label}: {n}")
            if level_parts:
                body.append({
                    "type": "TextBlock",
                    "text": "  ·  ".join(level_parts),
                    "wrap": True, "isSubtle": True, "size": "Small",
                })

        # ── Top 5 alertes classées ────────────────────────────────────────────
        alertes = wf.get("alertes") or rapport.get("anomalies") or []
        if alertes:
            body.append({
                "type": "TextBlock",
                "text": f"Alertes détectées ({len(alertes)})",
                "weight": "Bolder", "size": "Small", "spacing": "Medium",
            })
            alert_facts = []
            for a in alertes[:6]:
                niv   = a.get("niveau") or a.get("statut") or "?"
                check = a.get("check") or ""
                doc   = a.get("document") or ""
                msg   = a.get("message") or ""
                alert_facts.append({
                    "title": f"[{niv}] {check} ({doc})",
                    "value": msg[:150],
                })
            body.append({"type": "FactSet", "facts": alert_facts})
            if len(alertes) > 6:
                body.append({
                    "type": "TextBlock",
                    "text": f"… et {len(alertes) - 6} autre(s)",
                    "isSubtle": True, "size": "Small",
                })

        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": body,
                    "style": style,
                },
            }],
        }

    def _push(self, rapport: dict) -> dict:
        url  = os.getenv("TEAMS_WEBHOOK_URL")
        card = self._build_card(rapport)
        resp = requests.post(url, json=card, timeout=10)
        resp.raise_for_status()
        ent   = rapport.get("entreprise_nom") or "Sans nom"
        level = (rapport.get("workflow_event") or {}).get("alert_level", "")
        return {
            "ok": True,
            "detail": f"Teams : carte [{level or 'standard'}] envoyée pour '{ent}'",
        }
