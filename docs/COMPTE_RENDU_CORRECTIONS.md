# Compte rendu des corrections — Semantic Firewall DD

**Objet.** Réponse technique à l'audit *Semantic Firewall DD (cible FinNLP @ EMNLP)*.
Ce document liste **chaque défaut D1–D12** et les défauts mineurs, la **correction
effectuée**, les **fichiers concernés**, et l'**impact sur l'article de recherche**
(ce qu'il faut retirer, reformuler ou ajouter dans le papier).

**Date.** 2026-07-30 · **Périmètre traité.** D1–D7, D9, D11, D12 corrigés (code) ;
**D2 corrigé avec vérité de terrain XBRL réelle** ; D8 et D10 volontairement hors
périmètre ; expériences E0–E7 fournies en *scaffolding* exécutable (non exécutées).

---

## 1. Résumé exécutif

L'idée scientifique — *les identités comptables sont des détecteurs d'erreur
gratuits, sans annotation* — est conservée intacte. Les corrections portent sur
l'**évaluation** et l'**intégrité**, qui étaient les points rédhibitoires.

Trois changements structurants pour le papier :

1. **Détecteur et correcteur strictement séparés (D1).** Le détecteur est mesuré sur
   l'extraction *brute*, avant toute correction — la « cohérence EBITDA à 100 % » n'est
   plus vraie par construction.
2. **Vérité de terrain XBRL réelle (D2).** 46 sociétés / 49 documents, valeurs SEC
   tagguées, versionnées et checksummées. Le « rappel » (taux de non-nuls) est remplacé
   par la **justesse par champ** vs XBRL à tolérance explicite.
3. **Dépôt = artefact de recherche.** Package `semantic_firewall/` (extraction,
   validation, monitoring, évaluation) séparé du code produit (`product/`), reproductible
   (README, requirements épinglés, graines, Makefile), 28 tests unitaires hors-ligne.

---

## 2. Tableau de synthèse

| Réf | Défaut | Correction | Statut | Impact article |
|-----|--------|-----------|--------|----------------|
| D1 | Métrique tautologique | Détecteur (RAW) séparé du correcteur | ✅ | Retirer « cohérence EBITDA 100 % » ; reporter l'AUROC du détecteur |
| D2 | Aucune vérité de terrain ; rappel = non-nuls | Vérité XBRL réelle + justesse vs XBRL | ✅ | Remplacer « rappel » par justesse par champ ; nouveau tableau |
| D3 | Étiquettes lues dans les noms de fichiers | Matrices de confusion supprimées | ✅ | Supprimer le F1 sur ~14 docs |
| D4 | Baseline regex = homme de paille | Mêmes motifs que le système | ✅ | Re-rapporter M1 à motifs équitables |
| D5 | Provenance fabriquée | En-têtes marqués synthétiques ; scripts supprimés | ✅ | Retirer toute mention FNSPID/Montariol comme source |
| D6 | Détecteur d'ancrage = code mort | Branché, repli global retiré, fenêtre paramétrable | ✅ | Décrire l'ancrage comme 2ᵉ signal d'erreur |
| D7 | Seuil JSD 0,55 recopié 8× | Constante unique `config.py` | ✅ | Présenter 0,55 comme point de fonctionnement (à balayer) |
| D8 | Corpus petit/déséquilibré | *Hors périmètre* (dépend des données) | ⬜ | Ne pas conclure FR/EN tant que longueur non appariée |
| D9 | 3 scripts inexécutables | Supprimés | ✅ | — |
| D10 | Modèle de base trop faible | *Hors périmètre* (hook multi-modèle prêt) | ⬜ | Ajouter un modèle de pointe avant soumission |
| D11 | Non reproductible | README, requirements, graines, Makefile, manifeste | ✅ | Ajouter la section reproductibilité |
| D12 | Dépôt-produit | Code produit isolé dans `product/` | ✅ | L'artefact soumis = extraction+validation+évaluation |

Légende : ✅ corrigé · ⬜ hors périmètre (choix explicite).

---

## 3. Détail des défauts bloquants

### D1 — Métrique tautologique  ✅

**Problème.** Le pare-feu réécrivait EBITDA avec `ebit + d&a`, puis la métrique
vérifiait `EBITDA ≈ ebit + d&a` à la même tolérance de 15 % : « cohérence EBITDA »
valait 100 % *par construction*, et `score_confiance` était non informatif car calculé
après correction.

**Correction.** Séparation architecturale stricte :

- Le **détecteur** (`DDTaxonomy`, `validation/dd_base.py`) tourne sur l'extraction
  **brute**, avant toute correction. `score_confiance`, anomalies et contrôles en
  découlent → mérités, non tautologiques.
- Le **correcteur** (`validation/corrector.py`) est un **second système optionnel**,
  journalisé (`corrections_appliquees`), avec re-audit séparé
  (`controles_post_correction`). Application en **point fixe** (idempotente,
  insensible à l'ordre) ; règles nommées et ablables une par une.

**Fichiers.** `validation/corrector.py` (nouveau), `pipeline.py` (`_certify_dd`
réécrit), `validation/dd_base.py`.

**Impact article.** Retirer toute affirmation de « cohérence EBITDA ≈ 100 % ».
La contribution devient : *un détecteur d'erreur non supervisé dont on mesure
l'AUROC/PR-AUC sur de vrais dépôts* ; le correcteur est évalué à part sur le gain
de justesse réel qu'il apporte.

### D2 — Aucune vérité de terrain  ✅ (corrigé avec données réelles)

**Problème.** `recall_pct` = proportion de champs *non nuls* (un taux d'extraction,
pas une justesse) ; aucun nombre extrait n'était comparé à un nombre vrai. Le
regex-backfill de M4 gonflait encore ce « rappel ».

**Correction.**

1. **Vérité de terrain XBRL réelle et versionnée.**
   `evaluation/benchmark_ground_truth.py` résout chaque société (ticker→CIK via
   `company_tickers.json`) et tire les valeurs `us-gaap` tagguées de l'API SEC
   `companyfacts` : **46 sociétés, 49 documents**, écrites dans
   `data/benchmark_xbrl_ground_truth.json` (checksum `sha256:27764ee9…`, déterministe).
   - **Piège XBRL corrigé.** Le champ `fy` d'un fait désigne le *dépôt* (qui contient
     les colonnes comparatives) : filtrer sur `fy` renvoyait la mauvaise année
     (Apple affichait le CA FY2023 sous le tag 2025). La sélection se fait désormais
     par **date de fin de période** → Apple FY2025 = **416,161 Md$** (fin 2025-09-27),
     valeur correcte de l'exercice courant.
   - **Join** : CIK + date de fin + form 10-K + unité USD. La FY cible par document =
     période la plus proche de l'année du document, **indépendante de toute sortie
     modèle** → pas de biais optimiste. EBITDA = *dérivé* (`OperatingIncomeLoss +
     DepreciationDepletionAndAmortization`), ce qui est précisément ce qui rend
     l'identité arithmétique testable.
2. **Métrique redéfinie.** `benchmark_compare.compute_metrics` remplace le rappel
   non-null par `field_accuracy_pct` = **proportion de champs corrects** vs XBRL à
   **tolérance explicite (±1 %)**. Le non-null est conservé mais renommé
   `coverage_pct` (couverture ≠ justesse). Sans vérité de terrain → `None`, jamais un
   chiffre inventé.

**Fichiers.** `evaluation/xbrl_ground_truth.py`, `evaluation/benchmark_ground_truth.py`
(nouveaux), `evaluation/benchmark_compare.py` (`compute_metrics` + runner),
`data/benchmark_xbrl_ground_truth.json` (manifeste versionné),
`tests/test_benchmark_metric.py`.

**Vérification.** Extraction correcte → 100 % ; CA faux de 10 % + EBITDA manquant →
justesse 60 % pour une couverture 80 % ; toutes valeurs doublées → justesse 0 %,
couverture 100 % (exactement le mensonge de l'ancienne métrique).

**Impact article.** Remplacer le tableau « rappel » par un tableau de **justesse par
champ vs XBRL** (avec tolérance annoncée), sur des centaines de dépôts. Distinguer
explicitement couverture et justesse. Les banques (Wells Fargo, Goldman…) n'ayant pas
d'`OperatingIncomeLoss`/D&A tagué, l'identité EBITDA n'y est pas définie : c'est un
résultat sectoriel à mentionner, pas un échec.

### D3 — Étiquettes d'anomalie lues dans les noms de fichiers  ✅

**Problème.** `gt_positive = any(kw in nom for kw in ("erreur","atypique"))` :
précision/rappel/F1 calculés sur ~14 documents dont l'auteur a encodé l'étiquette dans
le nom — un test auto-réalisateur présenté comme évaluation.

**Correction.** Les deux matrices de confusion filename-based supprimées de
`evaluation/dd_eval.py` et `evaluation/run_all_eval.py`. Les métriques de
classification passent par des étiquettes dérivées de XBRL.

**Fichiers.** `evaluation/dd_eval.py`, `evaluation/run_all_eval.py`.

**Impact article.** Supprimer tout F1/précision/rappel calculé sur le petit corpus
étiqueté par nom de fichier. Ne publier des métriques de classification que sur
plusieurs centaines de documents étiquetés XBRL.

### D4 — Baseline regex = homme de paille  ✅

**Problème.** M1 recevait des motifs cassés (`Net\s+sales?\s+([\d,]+)` — nombre
accolé) tandis que le repli de M4 utilisait des motifs souples absorbant les points de
conduite. Les échecs de M1 étaient un artefact.

**Correction.** `method1_regex` importe désormais **exactement**
`corrector.REGEX_BACKFILL_PATTERNS` (les motifs du système) et le même seuil `≥ 100`.
Tout écart résiduel M1↔système est une propriété de la logique, pas des motifs.

**Fichiers.** `evaluation/benchmark_compare.py`, `validation/corrector.py`.

**Impact article.** Re-rapporter la baseline M1 (B1) avec les motifs équitables ; si
elle remonte, c'est une information à assumer, pas un problème.

### D5 — Provenance fabriquée  ✅

**Problème.** (a) `samples/fnspid/*.txt` annoncés « Source : FNSPID dataset — SEC 10-K »
alors que FNSPID (presse + cours) ne contient aucun bilan ; (b) études de dérive
synthétiques revendiquant le cadre Montariol et al.

**Correction.** En-têtes réécrits : **DOCUMENT SYNTHÉTIQUE… n'est pas extrait du
dataset FNSPID**. Les 3 scripts de dérive synthétique supprimés (voir D9).

**Fichiers.** `samples/fnspid/*.txt`, suppression des 3 `generate_*_drift_report.py`.

**Impact article.** Retirer toute prétention de provenance FNSPID/Montariol. Les vraies
études de dérive relèvent d'une expérience à part, sur de vrais décalages.

### D6 — Détecteur d'ancrage = code mort et auto-neutralisé  ✅

**Problème.** `check_transcription_divergence` (détecteur d'hallucination par ancrage
textuel) n'était jamais appelé, et se rabattait sur une recherche dans *tout* le
document — détruisant le signal localisé qu'il devait mesurer.

**Correction.** Déplacé dans `validation/anchoring.py`, **branché** dans
`pipeline._certify_dd`. Le repli global est **supprimé**. Fenêtre d'ancrage
paramétrable (`config.ANCHOR_WINDOW_*`).

**Fichiers.** `validation/anchoring.py` (nouveau), `pipeline.py`, `config.py`.

**Impact article.** Présenter l'ancrage comme un **second signal d'erreur**,
indépendant des identités arithmétiques, et décrire la combinaison
« identité ∨ ancrage ».

### D7 — Seuil JSD 0,55 recopié en dur  ✅

**Problème.** `0.55` recopié dans ~8 endroits (dont des chaînes de rapport) :
désynchronisation silencieuse possible.

**Correction.** Source unique `config.JSD_ALERT_THRESHOLD` ; `semantic_monitor`
l'importe ; marqué `# TO SWEEP` (point de fonctionnement, non justifié par un balayage).

**Fichiers.** `config.py`, `monitoring/semantic_monitor.py`.

**Impact article.** Présenter 0,55 comme un point de fonctionnement, pas un optimum ;
annoncer qu'un balayage/AUROC reste à faire (hors périmètre présent).

### D9 — Trois scripts inexécutables  ✅

**Problème.** `os.chdir(r'c:\Users\arthus...')` en dur + `fit()` incomplet : les
chiffres des PDF ne correspondaient pas au système.

**Correction.** Les 3 scripts supprimés.

**Impact article.** Aucun chiffre issu de ces scripts ne doit figurer dans le papier.

### D11 — Non reproductible  ✅

**Problème.** Pas de README, requirements, graines, snapshot.

**Correction.** `README.md`, `requirements*.txt` épinglés, `Makefile`, `pyproject.toml`,
graine unique (`config.RANDOM_SEED` / `seed_everything`), manifeste XBRL figé et
checksummé, `conftest.py`.

**Impact article.** Ajouter une section reproductibilité (versions, graines, manifeste
de données, commandes Makefile).

### D12 — Dépôt-produit  ✅

**Problème.** `api.py` (72 ko dont ~830 lignes HTML), connecteurs Notion/Airtable/
Teams/SharePoint : dilue la relecture.

**Correction.** Tout le code produit déplacé dans `product/`. L'artefact
`semantic_firewall/` ne contient qu'extraction, validation, évaluation, monitoring.

**Impact article.** L'artefact soumis se limite à ces quatre briques.

---

## 4. Défauts mineurs corrigés

- **Détection de langue.** `detect_language` : égalité (et chaîne vide) → `fr` au lieu
  d'un biais systématique vers `en` ; classe de caractères complétée (œ, ÿ).
- **Correcteur non idempotent / ordre non testé.** Point fixe borné → insensible à
  l'ordre et idempotent ; tests dédiés.
- **Pertes silencieuses.** Correcteur sign-aware : EBIT/EBITDA négatifs (sociétés
  déficitaires, ex. Boeing) gérés, plus écartés par des gardes `> 0`.
- **Troncature invisible.** `pipeline._certify_dd` renvoie un bloc `truncation`
  (caractères d'origine / envoyés / perdus).
- **Latence faussée.** `time.sleep` retiré de `dd_eval.call_dd_extraction` et du chemin
  chronométré de `benchmark_compare`.

---

## 5. Hors périmètre (choix explicite)

- **D8 (corpus petit/déséquilibré)** et **D10 (modèle de base trop faible)** : dépendent
  entièrement de données/compute supplémentaires. Le hook multi-modèle
  (`baselines.BaselineContext.model`) est prêt ; aucune conclusion FR/EN ne doit être
  publiée tant que longueur et registre ne sont pas appariés.
- **Expériences E0–E7** : fournies en scaffolding exécutable
  (`evaluation/xbrl_ground_truth.py`, `detector.py`, `metrics.py`, `baselines.py`,
  `run_experiment.py`) — non exécutées ici (nécessitent clés LLM + campagnes de calcul).
  Aucun résultat n'est fabriqué.

---

## 6. Annexe — structure et vérification

**Nouvelle structure.**

```
semantic_firewall/   extraction · validation · monitoring · evaluation · config · pipeline
product/             api, connectors, report generators (hors artefact)
scripts/  tests/  docs/  data/benchmark_xbrl_ground_truth.json (versionné)
```

**Vérification.** L'ensemble du paquet se compile et s'importe ; **28 tests unitaires
hors-ligne passent** (parseur `DDTaxonomy._f`, idempotence/ordre/pertes du correcteur,
métriques ROC-AUC/PR-AUC, et régression D2 de la métrique de justesse). Vérité de
terrain construite en direct depuis SEC (46 sociétés), déterministe (même checksum à la
reconstruction).

*Détail défaut par défaut également dans `docs/AUDIT_RESPONSE.md`.*
