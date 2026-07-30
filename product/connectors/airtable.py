"""
Connecteur Airtable — crée ou met à jour un enregistrement dans une table DD.

Variables d'environnement :
  AIRTABLE_TOKEN        Personal access token (pat_xxx)
  AIRTABLE_BASE_ID      ID de la base (appXXX)
  AIRTABLE_TABLE_NAME   Nom de la table (ex: "Due Diligence")

La table doit contenir les colonnes suivantes (noms exacts) :
  Entreprise        Single line text  (champ de recherche pour upsert)
  Verdict           Single select     (CERTIFIÉ | ANOMALIE | ERREUR)
  Niveau alerte     Single select     (BLOQUANT | CRITIQUE | HAUTE | MOYENNE | INFO)
  Score moyen       Number (décimal)
  Anomalies         Number (entier)
  Documents         Number (entier)
  Date audit        Date
  Modèle            Single line text
  Dossier ID        Single line text
  Action requise    Long text
  Détail anomalies  Long text
"""

import os
import requests
from urllib.parse import quote
from .base import DDConnector

_API = "https://api.airtable.com/v0"


class AirtableConnector(DDConnector):

    def name(self) -> str:
        return "Airtable"

    def is_configured(self) -> bool:
        return bool(
            os.getenv("AIRTABLE_TOKEN")
            and os.getenv("AIRTABLE_BASE_ID")
            and os.getenv("AIRTABLE_TABLE_NAME")
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_TOKEN')}",
            "Content-Type": "application/json",
        }

    def _table_url(self) -> str:
        base  = os.getenv("AIRTABLE_BASE_ID")
        table = quote(os.getenv("AIRTABLE_TABLE_NAME", "Due Diligence"))
        return f"{_API}/{base}/{table}"

    def _find_record(self, entreprise: str) -> str | None:
        """Retourne l'ID du record existant pour cette entreprise, ou None."""
        formula = f'{{Entreprise}}="{entreprise}"'
        resp = requests.get(
            self._table_url(),
            headers=self._headers(),
            params={"filterByFormula": formula, "maxRecords": 1},
            timeout=10,
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])
        return records[0]["id"] if records else None

    def _build_fields(self, rapport: dict) -> dict:
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
        wf          = rapport.get("workflow_event") or {}
        alert_level = wf.get("alert_level") or ""
        resume      = wf.get("resume") or {}
        action_glob = resume.get("action_globale") or ""

        # Détail des alertes classées (format enrichi si dispo, fallback anomalies brutes)
        alertes   = wf.get("alertes") or rapport.get("anomalies") or []
        detail    = "\n".join(
            f"[{a.get('niveau') or a.get('statut','?')}] {a.get('check','')} "
            f"— {a.get('document','')}: {a.get('message','')}"
            for a in alertes
        )

        fields = {
            "Entreprise":       ent,
            "Verdict":          verdict,
            "Score moyen":      score_avg,
            "Anomalies":        len(alertes),
            "Documents":        int(rapport.get("nb_documents") or 0),
            "Modèle":           str(rapport.get("modele") or ""),
            "Dossier ID":       str(rapport.get("dossier_id") or ""),
            "Détail anomalies": detail[:100000],
        }
        if alert_level:
            fields["Niveau alerte"] = alert_level
        if action_glob:
            fields["Action requise"] = action_glob[:100000]
        if date_raw:
            fields["Date audit"] = date_raw
        return fields

    def _push(self, rapport: dict) -> dict:
        ent       = rapport.get("entreprise_nom") or "Sans nom"
        record_id = self._find_record(ent)
        fields    = self._build_fields(rapport)

        if record_id:
            resp = requests.patch(
                f"{self._table_url()}/{record_id}",
                headers=self._headers(),
                json={"fields": fields},
                timeout=10,
            )
            action = "mis à jour"
        else:
            resp = requests.post(
                self._table_url(),
                headers=self._headers(),
                json={"fields": fields},
                timeout=10,
            )
            action = "créé"
            record_id = resp.json().get("id", "")

        resp.raise_for_status()
        return {
            "ok": True,
            "detail": f"Airtable : enregistrement '{ent}' {action} (id: {str(record_id)[:8]}…)",
        }
