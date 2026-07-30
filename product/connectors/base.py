"""Base class pour tous les connecteurs DD."""

from abc import ABC, abstractmethod


class DDConnector(ABC):
    """
    Interface commune pour les connecteurs de sortie.
    Chaque connecteur reçoit un rapport de dossier certifié
    et le pousse vers son système cible.
    """

    @abstractmethod
    def name(self) -> str:
        """Nom lisible du connecteur."""

    @abstractmethod
    def is_configured(self) -> bool:
        """True si toutes les variables d'environnement requises sont présentes."""

    def push_dossier(self, rapport: dict) -> dict:
        """
        Pousse le rapport de dossier vers le système cible.
        Retourne {"ok": bool, "detail": str}.
        Ne doit jamais lever d'exception.
        """
        if not self.is_configured():
            return {"ok": False, "detail": f"{self.name()} : non configuré (variables manquantes)"}
        try:
            return self._push(rapport)
        except Exception as exc:
            return {"ok": False, "detail": f"{self.name()} : {exc}"}

    @abstractmethod
    def _push(self, rapport: dict) -> dict:
        """Implémentation métier — peut lever des exceptions."""
