"""
Connecteur SharePoint — dépose le rapport PDF dans une bibliothèque de documents.

Variables d'environnement :
  SHAREPOINT_TENANT_ID       ID du tenant Azure AD
  SHAREPOINT_CLIENT_ID       App ID enregistrée dans Azure AD (permission Sites.ReadWrite.All)
  SHAREPOINT_CLIENT_SECRET   Secret de l'application
  SHAREPOINT_SITE_URL        URL du site SharePoint (ex: https://contoso.sharepoint.com/sites/DD)
  SHAREPOINT_FOLDER          Chemin dans la bibliothèque (ex: Documents partagés/Rapports DD)

Prérequis : pip install msal requests
"""

import os
import requests
from pathlib import Path
from .base import DDConnector


class SharePointConnector(DDConnector):

    def name(self) -> str:
        return "SharePoint"

    def is_configured(self) -> bool:
        required = [
            "SHAREPOINT_TENANT_ID", "SHAREPOINT_CLIENT_ID",
            "SHAREPOINT_CLIENT_SECRET", "SHAREPOINT_SITE_URL",
        ]
        return all(os.getenv(k) for k in required)

    def _get_token(self) -> str:
        try:
            import msal
        except ImportError:
            raise RuntimeError("Package 'msal' manquant — lancez : pip install msal")

        tenant_id     = os.getenv("SHAREPOINT_TENANT_ID")
        client_id     = os.getenv("SHAREPOINT_CLIENT_ID")
        client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET")

        app = msal.ConfidentialClientApplication(
            client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise RuntimeError(f"MSAL token error : {result.get('error_description', result)}")
        return result["access_token"]

    def _get_site_id(self, token: str) -> str:
        site_url  = os.getenv("SHAREPOINT_SITE_URL", "").rstrip("/")
        # Extrait hostname et site-path depuis l'URL
        # ex: https://contoso.sharepoint.com/sites/DD → contoso.sharepoint.com + /sites/DD
        from urllib.parse import urlparse
        parsed = urlparse(site_url)
        hostname = parsed.netloc
        site_path = parsed.path.lstrip("/")
        url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{site_path}"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        resp.raise_for_status()
        return resp.json()["id"]

    def _upload_pdf(self, token: str, site_id: str, folder: str,
                    filename: str, pdf_bytes: bytes) -> str:
        """Dépose le fichier et retourne l'URL de partage."""
        folder = folder.strip("/")
        url = (f"https://graph.microsoft.com/v1.0/sites/{site_id}"
               f"/drive/root:/{folder}/{filename}:/content")
        resp = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/pdf",
            },
            data=pdf_bytes,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("webUrl", "")

    def _push(self, rapport: dict) -> dict:
        dossier_id = rapport.get("dossier_id") or ""
        ent        = rapport.get("entreprise_nom") or "sans_nom"

        # Cherche le PDF généré localement
        pdf_path = Path("output/dossiers") / f"{dossier_id}.pdf"
        if not pdf_path.exists():
            return {"ok": False, "detail": f"SharePoint : PDF introuvable ({pdf_path})"}

        pdf_bytes = pdf_path.read_bytes()
        date_str  = (rapport.get("horodatage") or "")[:10].replace("-", "")
        filename  = f"DD_{ent}_{date_str}_{dossier_id[:8]}.pdf"
        folder    = os.getenv("SHAREPOINT_FOLDER", "Documents partagés/Rapports DD")

        token   = self._get_token()
        site_id = self._get_site_id(token)
        web_url = self._upload_pdf(token, site_id, folder, filename, pdf_bytes)

        return {
            "ok": True,
            "detail": f"SharePoint : '{filename}' déposé → {web_url}",
        }
