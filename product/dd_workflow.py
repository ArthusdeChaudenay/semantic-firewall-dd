"""
dd_workflow.py — Moteur de classification des alertes DD + format d'échange canonique.

Rôle : transformer un rapport brut (pipeline.certify_dossier) en WorkflowEvent
structuré, prêt à être consommé par les connecteurs (Teams, Notion, Airtable,
SharePoint) et par les endpoints /workflow/* de l'API FastAPI.

Niveaux d'alerte (par ordre croissant de criticité) :
  INFO      : dérive sémantique détectée, pour information
  MOYENNE   : champs manquants, marge inconsistante — recommandation de vérification
  HAUTE     : incohérence matérielle (EBIT, décomposition actif) — clarification requise
  CRITIQUE  : anomalie financière majeure (EBITDA, valorisation, prix/action) — comité bloqué
  BLOQUANT  : intégrité des données rompue (bilan déséquilibré, cap table invalide) — instruction bloquée

Format d'échange (WorkflowEvent.to_dict()) :
  {
    "event_type":      "dd.certification.complete",
    "schema_version":  "1.0",
    "dossier_id":      "uuid",
    "entreprise":      "TechVenture SAS",
    "timestamp":       "2026-07-02T09:42:34",
    "verdict":         "ANOMALIE",
    "alert_level":     "CRITIQUE",
    "score_moyen":     72.5,
    "nb_documents":    16,
    "alertes": [
      {
        "niveau":        "CRITIQUE",
        "check":         "coherence_ebitda",
        "document":      "compte_resultat.txt",
        "message":       "CA - Pers - Opex = 2.0M ≠ EBITDA 2.4M",
        "action_requise":"Vérification obligatoire par le lead analyst",
        "erreur_type":   "EBITDA_Inconsistency"
      }
    ],
    "resume": {
      "nb_bloquants": 0, "nb_critiques": 1, "nb_hautes": 0,
      "nb_moyennes": 0,  "nb_infos": 0,
      "nb_derives_semantiques": 0,
      "action_globale": "Vérification obligatoire avant comité d'investissement"
    },
    "documents": [
      {"fichier":"bilan.txt","type":"bilan","statut":"CERTIFIÉ","score":100.0}
    ]
  }
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# NIVEAUX D'ALERTE
# ──────────────────────────────────────────────────────────────────────────────

class AlertLevel(IntEnum):
    INFO     = 0
    MOYENNE  = 1
    HAUTE    = 2
    CRITIQUE = 3
    BLOQUANT = 4

    def label(self) -> str:
        return self.name

    def emoji(self) -> str:
        return {
            AlertLevel.INFO:     "ℹ️",
            AlertLevel.MOYENNE:  "📋",
            AlertLevel.HAUTE:    "⚠️",
            AlertLevel.CRITIQUE: "🔴",
            AlertLevel.BLOQUANT: "🚨",
        }[self]


# ──────────────────────────────────────────────────────────────────────────────
# MAPPING CHECK → NIVEAU + ACTIONS REQUISES
# ──────────────────────────────────────────────────────────────────────────────

#: Pour chaque contrôle DDTaxonomy : (AlertLevel, action_requise, justification)
CHECK_CATALOG: dict[str, tuple[AlertLevel, str, str]] = {

    # ── BLOQUANT — intégrité des données rompue ──────────────────────────────
    "equilibre_bilan": (
        AlertLevel.BLOQUANT,
        "Bloquer l'instruction — le bilan est déséquilibré. "
        "Aucune décision d'investissement possible avant correction et re-certification.",
        "Un bilan Actif ≠ Passif indique une erreur comptable majeure ou une manipulation.",
    ),
    "total_pct": (
        AlertLevel.BLOQUANT,
        "Bloquer l'instruction — la cap table est invalide (total ≠ 100%). "
        "Demander un cap table certifié par un tiers (avocat ou expert-comptable).",
        "Une cap table dont les parts ne totalisent pas 100 % est inexploitable pour la structuration du deal.",
    ),

    # ── CRITIQUE — anomalie financière majeure ───────────────────────────────
    "coherence_ebitda": (
        AlertLevel.CRITIQUE,
        "Vérification obligatoire par le lead analyst avant présentation au comité. "
        "Demander le grand livre et les notes annexes justifiant l'EBITDA déclaré.",
        "L'EBITDA manipulé est l'indicateur de fraude financière le plus fréquent en M&A.",
    ),
    "valorisation_post": (
        AlertLevel.CRITIQUE,
        "Vérification obligatoire — la valorisation post-money ne correspond pas à "
        "pré-money + levée. Recontacter le CFO pour reconciliation avant toute term sheet.",
        "Une erreur de valorisation invalide le calcul de dilution et le prix d'entrée.",
    ),
    "prix_par_action": (
        AlertLevel.CRITIQUE,
        "Vérification obligatoire — le prix par action ne correspond pas à "
        "post-money / total actions. Bloquer la signature du BSA/SPA jusqu'à correction.",
        "Un prix par action incorrect rend nul et non avenu tout instrument de souscription.",
    ),

    # ── HAUTE — incohérence matérielle ──────────────────────────────────────
    "decomposition_actif": (
        AlertLevel.HAUTE,
        "Demander à la cible de fournir le détail des composantes du bilan (immobilisations, "
        "stocks, créances) avec les justificatifs comptables.",
        "La somme Immob+Circ+Tréso ≠ Actif total signale une erreur de saisie ou un actif non déclaré.",
    ),
    "coherence_ebit": (
        AlertLevel.HAUTE,
        "Demander clarification sur la politique d'amortissement et les dotations aux provisions. "
        "Comparer avec le tableau des immobilisations.",
        "Un EBIT incohérent avec EBITDA−D&A peut masquer des provisions exceptionnelles.",
    ),

    # ── MOYENNE — complétude / marge ────────────────────────────────────────
    "coherence_marge_ebitda": (
        AlertLevel.MOYENNE,
        "Signaler à l'analyste — la marge EBITDA affichée diffère du calcul. "
        "Vérifier si la marge est calculée sur chiffre d'affaires net ou brut.",
        "Peut indiquer une différence de convention comptable ou une erreur de calcul mineure.",
    ),
    "completeness": (
        AlertLevel.MOYENNE,
        "Relancer la cible pour fournir les champs manquants avant la prochaine session de DD.",
        "Des champs obligatoires manquants réduisent la couverture de l'analyse automatique.",
    ),

    # ── CRITIQUE — divergence de transcription (hallucination LLM) ─────────
    "transcription_divergence": (
        AlertLevel.CRITIQUE,
        "Vérification manuelle obligatoire — un ou plusieurs montants extraits n'apparaissent "
        "pas textuellement dans le document source. Possible hallucination LLM. "
        "Comparer les valeurs JSON avec le document original avant toute décision.",
        "Un montant présent dans le JSON mais absent du texte brut indique une invention du LLM, "
        "non une erreur comptable de la cible.",
    ),

    # ── INFO — extraction / dérive sémantique ───────────────────────────────
    "extraction": (
        AlertLevel.MOYENNE,
        "Le LLM n'a pas pu analyser ce document. Vérifier que le fichier est lisible "
        "(texte non scanné, encodage UTF-8) et relancer manuellement.",
        "Erreur technique — non imputable à la cible.",
    ),
    "derive_semantique": (
        AlertLevel.INFO,
        "Pour information — le vocabulaire de ce document s'écarte du corpus de référence. "
        "Peut indiquer un document d'un autre secteur ou une version préliminaire.",
        "Dérive JSD > seuil 55 % détectée par le moniteur sémantique.",
    ),
}

# Fallback pour les checks inconnus
_DEFAULT_CATALOG = (
    AlertLevel.MOYENNE,
    "Vérification manuelle recommandée.",
    "Contrôle non répertorié dans le catalogue.",
)


def _get_catalog(check: str) -> tuple[AlertLevel, str, str]:
    return CHECK_CATALOG.get(check, _DEFAULT_CATALOG)


# ──────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DDAlert:
    niveau:         str
    check:          str
    document:       str
    message:        str
    action_requise: str
    justification:  str
    erreur_type:    Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "niveau":         self.niveau,
            "check":          self.check,
            "document":       self.document,
            "message":        self.message,
            "action_requise": self.action_requise,
        }
        if self.erreur_type:
            d["erreur_type"] = self.erreur_type
        return d


@dataclass
class WorkflowEvent:
    dossier_id:  str
    entreprise:  str
    timestamp:   str
    verdict:     str
    alert_level: AlertLevel
    score_moyen: float
    nb_documents:int
    alertes:     list[DDAlert]      = field(default_factory=list)
    documents:   list[dict]         = field(default_factory=list)
    nb_derives:  int                = 0

    # ── dérivés ──────────────────────────────────────────────────────────────

    def _counts(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "BLOQUANT": 0, "CRITIQUE": 0, "HAUTE": 0, "MOYENNE": 0, "INFO": 0
        }
        for a in self.alertes:
            counts[a.niveau] = counts.get(a.niveau, 0) + 1
        return counts

    def action_globale(self) -> str:
        c = self._counts()
        if c["BLOQUANT"] > 0:
            return (f"🚨 INSTRUCTION BLOQUÉE — {c['BLOQUANT']} anomalie(s) bloquante(s) "
                    "détectée(s). Aucune décision d'investissement avant correction.")
        if c["CRITIQUE"] > 0:
            return (f"🔴 Vérification obligatoire — {c['CRITIQUE']} anomalie(s) critique(s). "
                    "Ne pas présenter au comité avant validation du lead analyst.")
        if c["HAUTE"] > 0:
            return (f"⚠️ Clarification requise — {c['HAUTE']} incohérence(s) matérielle(s). "
                    "Demander documents complémentaires à la cible.")
        if c["MOYENNE"] > 0:
            return (f"📋 Vérification recommandée — {c['MOYENNE']} point(s) à éclaircir "
                    "avant décision finale.")
        if self.nb_derives > 0:
            return "ℹ️ Dossier certifié avec dérive sémantique détectée — contrôle informatif uniquement."
        return "✅ Dossier certifié conforme — instruction peut continuer."

    def to_dict(self) -> dict:
        c = self._counts()
        return {
            "event_type":     "dd.certification.complete",
            "schema_version": "1.0",
            "dossier_id":     self.dossier_id,
            "entreprise":     self.entreprise,
            "timestamp":      self.timestamp,
            "verdict":        self.verdict,
            "alert_level":    self.alert_level.label(),
            "score_moyen":    self.score_moyen,
            "nb_documents":   self.nb_documents,
            "alertes":        [a.to_dict() for a in self.alertes],
            "resume": {
                "nb_bloquants":           c["BLOQUANT"],
                "nb_critiques":           c["CRITIQUE"],
                "nb_hautes":              c["HAUTE"],
                "nb_moyennes":            c["MOYENNE"],
                "nb_infos":               c["INFO"],
                "nb_derives_semantiques": self.nb_derives,
                "action_globale":         self.action_globale(),
            },
            "documents": self.documents,
        }


# ──────────────────────────────────────────────────────────────────────────────
# CLASSIFICATEUR
# ──────────────────────────────────────────────────────────────────────────────

def classify_alerts(rapport: dict) -> WorkflowEvent:
    """
    Transforme un rapport brut (pipeline.certify_dossier) en WorkflowEvent classifié.

    Gère également la dérive sémantique détectée par SemanticMonitor (jsd_alert).
    """
    alertes: list[DDAlert]  = []
    max_level: AlertLevel   = AlertLevel.INFO
    nb_derives              = 0

    # ── Anomalies DDTaxonomy ──────────────────────────────────────────────────
    for ano in (rapport.get("anomalies") or []):
        if not isinstance(ano, dict):
            continue
        check   = ano.get("check") or "inconnu"
        lvl, action, justif = _get_catalog(check)
        max_level = max(max_level, lvl)

        alertes.append(DDAlert(
            niveau        = lvl.label(),
            check         = check,
            document      = ano.get("document") or "",
            message       = ano.get("message") or "",
            action_requise= action,
            justification = justif,
            erreur_type   = ano.get("erreur_type"),
        ))

    # ── Dérive sémantique (jsd_alert) ────────────────────────────────────────
    for cert in (rapport.get("certifications") or []):
        if not isinstance(cert, dict):
            continue
        sa = cert.get("semantic_analysis") or {}
        if isinstance(sa, dict) and sa.get("jsd_alert"):
            nb_derives += 1
            jsd_pct = round((sa.get("jsd_score") or 0) * 100, 1)
            lvl, action, justif = _get_catalog("derive_semantique")
            alertes.append(DDAlert(
                niveau        = lvl.label(),
                check         = "derive_semantique",
                document      = cert.get("document") or "",
                message       = f"JSD={jsd_pct}% — vocabulaire atypique vs corpus de référence",
                action_requise= action,
                justification = justif,
            ))

    # Tri : BLOQUANT → CRITIQUE → HAUTE → MOYENNE → INFO
    alertes.sort(key=lambda a: -AlertLevel[a.niveau].value)

    # ── Score moyen ───────────────────────────────────────────────────────────
    certs_valid = [c for c in (rapport.get("certifications") or [])
                   if isinstance(c, dict) and c.get("statut") not in ("ERREUR", None)]
    score_moyen = (
        round(sum(float(c.get("score_confiance") or 0) for c in certs_valid)
              / len(certs_valid), 1)
        if certs_valid else 0.0
    )

    # ── Documents summary ─────────────────────────────────────────────────────
    documents = [
        {
            "fichier": cert.get("document") or "",
            "type":    cert.get("doc_type") or "inconnu",
            "statut":  cert.get("statut") or "—",
            "score":   float(cert.get("score_confiance") or 0),
        }
        for cert in (rapport.get("certifications") or [])
        if isinstance(cert, dict)
    ]

    return WorkflowEvent(
        dossier_id  = rapport.get("dossier_id") or "",
        entreprise  = rapport.get("entreprise_nom") or "Sans nom",
        timestamp   = rapport.get("horodatage") or "",
        verdict     = rapport.get("verdict") or "—",
        alert_level = max_level,
        score_moyen = score_moyen,
        nb_documents= int(rapport.get("nb_documents") or 0),
        alertes     = alertes,
        documents   = documents,
        nb_derives  = nb_derives,
    )


# ──────────────────────────────────────────────────────────────────────────────
# SCHÉMA DE RÉFÉRENCE (pour GET /workflow/schema)
# ──────────────────────────────────────────────────────────────────────────────

WORKFLOW_SCHEMA = {
    "description": "Format d'échange canonique entre le Pare-feu Sémantique DD et les outils de workflow",
    "schema_version": "1.0",
    "event_types": ["dd.certification.complete"],
    "alert_levels": {
        "BLOQUANT": "Intégrité des données rompue — instruction impossible",
        "CRITIQUE": "Anomalie financière majeure — comité bloqué",
        "HAUTE":    "Incohérence matérielle — clarification requise",
        "MOYENNE":  "Données incomplètes ou marge inconsistante",
        "INFO":     "Dérive sémantique — informatif uniquement",
    },
    "checks_catalog": {
        check: {
            "niveau":         lvl.label(),
            "action_requise": action,
            "justification":  justif,
        }
        for check, (lvl, action, justif) in CHECK_CATALOG.items()
    },
    "routing": {
        "Teams": {
            "BLOQUANT": "Canal #dd-alertes-critiques — mention @equipe-investissement",
            "CRITIQUE": "Canal #dd-alertes-critiques — mention @lead-analyst",
            "HAUTE":    "Canal #dd-workflow — notification standard",
            "MOYENNE":  "Canal #dd-workflow — notification standard",
            "INFO":     "Canal #dd-workflow — silencieux (log uniquement)",
        },
        "Notion": "Base DD Pipeline — upsert par entreprise + champ Niveau alerte",
        "Airtable": "Table Due Diligence — upsert par entreprise + champ Niveau alerte",
        "SharePoint": "Bibliothèque Documents partagés/Rapports DD — PDF du dossier",
    },
    "payload_example": {
        "event_type":     "dd.certification.complete",
        "schema_version": "1.0",
        "dossier_id":     "befcf3db-7098-405a-990a-81b18ca48e8c",
        "entreprise":     "TechVenture SAS",
        "timestamp":      "2026-07-02T09:42:34",
        "verdict":        "ANOMALIE",
        "alert_level":    "CRITIQUE",
        "score_moyen":    72.5,
        "nb_documents":   16,
        "alertes": [
            {
                "niveau":         "BLOQUANT",
                "check":          "equilibre_bilan",
                "document":       "bilan_erreur.txt",
                "message":        "Actif (2.5M €) ≠ Passif (2.65M €), écart 150K €",
                "action_requise": "Bloquer l'instruction — vérification manuelle obligatoire",
                "erreur_type":    "Balance_Sheet_Imbalance",
            }
        ],
        "resume": {
            "nb_bloquants": 1, "nb_critiques": 0, "nb_hautes": 0,
            "nb_moyennes": 0, "nb_infos": 0, "nb_derives_semantiques": 0,
            "action_globale": "🚨 INSTRUCTION BLOQUÉE — 1 anomalie(s) bloquante(s) détectée(s).",
        },
        "documents": [
            {"fichier": "bilan_erreur.txt", "type": "bilan", "statut": "ANOMALIE", "score": 66.7}
        ],
    },
}
