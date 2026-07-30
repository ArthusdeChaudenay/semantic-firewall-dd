"""
ConnectorManager — dispatch vers tous les connecteurs configurés.

Avant chaque dispatch, enrichit le rapport avec un WorkflowEvent classifié
(dd_workflow.classify_alerts) exposé sous la clé "workflow_event".
Chaque connecteur peut ainsi lire le niveau d'alerte et l'action requise
sans recalculer la logique métier.

Instancié une seule fois au démarrage de l'API (singleton via get_manager()).
"""

from .notion      import NotionConnector
from .airtable    import AirtableConnector
from .teams       import TeamsConnector
from .sharepoint  import SharePointConnector


def _enrich(rapport: dict) -> dict:
    """Injecte workflow_event dans le rapport si pas déjà présent."""
    if rapport.get("workflow_event"):
        return rapport
    try:
        from product.dd_workflow import classify_alerts
        event = classify_alerts(rapport)
        return {**rapport, "workflow_event": event.to_dict()}
    except Exception:
        return rapport


class ConnectorManager:

    def __init__(self):
        self._connectors = [
            NotionConnector(),
            AirtableConnector(),
            TeamsConnector(),
            SharePointConnector(),
        ]

    def active(self) -> list:
        return [c for c in self._connectors if c.is_configured()]

    def status(self) -> dict:
        return {
            c.name(): "configuré" if c.is_configured() else "non configuré"
            for c in self._connectors
        }

    def push_dossier(self, rapport: dict) -> dict:
        """
        Enrichit le rapport avec le WorkflowEvent classifié, puis pousse
        vers tous les connecteurs. Retourne les résultats sans bloquer
        en cas d'erreur individuelle.
        """
        enriched = _enrich(rapport)
        results  = {}
        for connector in self._connectors:
            results[connector.name()] = connector.push_dossier(enriched)
        return results


# ── Singleton global ──────────────────────────────────────────────────────────

_manager: ConnectorManager | None = None


def get_manager() -> ConnectorManager:
    global _manager
    if _manager is None:
        _manager = ConnectorManager()
    return _manager
