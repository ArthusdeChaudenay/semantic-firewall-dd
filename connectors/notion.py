"""
Connecteur Notion — crée ou met à jour une page dans une base DD.

Variables d'environnement :
  NOTION_TOKEN          Secret d'intégration (secret_xxx)
  NOTION_DATABASE_ID    ID de la base Notion cible

La base doit contenir les propriétés suivantes (noms exacts) :
  Entreprise      Title
  Verdict         Select  (CERTIFIÉ | ANOMALIE | ERREUR)
  Niveau alerte   Select  (BLOQUANT | CRITIQUE | HAUTE | MOYENNE | INFO)
  Score moyen     Number
  Anomalies       Number
  Documents       Number
  Date audit      Date
  Modèle          Rich text
  Dossier ID      Rich text
  Action requise  Rich text
"""

import os
import json
import requests
from .base import DDConnector

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


class NotionConnector(DDConnector):

    def name(self) -> str:
        return "Notion"

    def is_configured(self) -> bool:
        return bool(os.getenv("NOTION_TOKEN") and os.getenv("NOTION_DATABASE_ID"))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {os.getenv('NOTION_TOKEN')}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    def _find_page(self, entreprise: str) -> str | None:
        """Retourne l'ID de la page existante pour cette entreprise, ou None."""
        db_id = os.getenv("NOTION_DATABASE_ID")
        resp = requests.post(
            f"{_API}/databases/{db_id}/query",
            headers=self._headers(),
            json={"filter": {"property": "Entreprise", "title": {"equals": entreprise}}},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None

    def _build_properties(self, rapport: dict) -> dict:
        ent      = rapport.get("entreprise_nom") or "Sans nom"
        verdict  = rapport.get("verdict") or "—"
        date_raw = (rapport.get("horodatage") or "")[:10] or None
        certs    = [c for c in (rapport.get("certifications") or [])
                    if isinstance(c, dict) and c.get("statut") not in ("ERREUR", None)]
        score_avg = (
            round(sum(float(c.get("score_confiance") or 0) for c in certs) / len(certs), 1)
            if certs else 0.0
        )
        # WorkflowEvent enrichi
        wf           = rapport.get("workflow_event") or {}
        alert_level  = wf.get("alert_level") or ""
        resume       = wf.get("resume") or {}
        action_glob  = resume.get("action_globale") or ""

        props = {
            "Entreprise":  {"title": [{"text": {"content": ent}}]},
            "Verdict":     {"select": {"name": verdict}},
            "Score moyen": {"number": score_avg},
            "Anomalies":   {"number": len(rapport.get("anomalies") or [])},
            "Documents":   {"number": int(rapport.get("nb_documents") or 0)},
            "Modèle":      {"rich_text": [{"text": {"content": str(rapport.get("modele") or "")}}]},
            "Dossier ID":  {"rich_text": [{"text": {"content": str(rapport.get("dossier_id") or "")}}]},
        }
        if alert_level:
            props["Niveau alerte"] = {"select": {"name": alert_level}}
        if action_glob:
            props["Action requise"] = {"rich_text": [{"text": {"content": action_glob[:2000]}}]}
        if date_raw:
            props["Date audit"] = {"date": {"start": date_raw}}
        return props

    def _build_body_blocks(self, rapport: dict) -> list:
        """Blocs de contenu : liste des anomalies + tableau des docs."""
        blocks = []

        anomalies = rapport.get("anomalies") or []
        if anomalies:
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": f"Anomalies ({len(anomalies)})"}}]},
            })
            for a in anomalies[:20]:
                doc   = a.get("document") or ""
                check = a.get("check") or ""
                msg   = a.get("message") or ""
                line  = f"[{a.get('statut','?')}] {check} — {doc} : {msg}"
                blocks.append({
                    "object": "block", "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"text": {"content": line[:2000]}}]},
                })
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": "Aucune anomalie — dossier certifié."}}]},
            })

        return blocks

    def _push(self, rapport: dict) -> dict:
        db_id   = os.getenv("NOTION_DATABASE_ID")
        ent     = rapport.get("entreprise_nom") or "Sans nom"
        page_id = self._find_page(ent)
        props   = self._build_properties(rapport)

        if page_id:
            # Mise à jour de la page existante
            resp = requests.patch(
                f"{_API}/pages/{page_id}",
                headers=self._headers(),
                json={"properties": props},
                timeout=10,
            )
            resp.raise_for_status()
            action = "mise à jour"
        else:
            # Création d'une nouvelle page avec blocs de contenu
            payload = {
                "parent": {"database_id": db_id},
                "properties": props,
                "children": self._build_body_blocks(rapport),
            }
            resp = requests.post(
                f"{_API}/pages",
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            action = "créée"
            page_id = resp.json().get("id", "")

        return {
            "ok": True,
            "detail": f"Notion : page '{ent}' {action} (id: {str(page_id)[:8]}…)",
        }
