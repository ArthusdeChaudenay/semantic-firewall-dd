# Réponse à l'audit — Semantic Firewall DD

Ce document trace **chaque défaut (D1–D12) et chaque expérience (E0–E7)** de l'audit
vers ce qui a été fait. Deux statuts :

- ✅ **Corrigé (code)** — la correction est dans le code, testée, exécutable maintenant.
- 🧪 **Scaffolding runnable** — le module/CLI existe et tourne, mais produire les
  *chiffres du papier* exige des données XBRL SEC + des exécutions LLM (clés/compute).
  Aucun résultat n'est fabriqué : c'est précisément l'intégrité que l'audit exige.

## Défauts bloquants

| Réf | Défaut | Statut | Où / comment |
|-----|--------|--------|--------------|
| **D1** | Métrique tautologique (M4 corrige puis se note sur la même formule) | ✅ | Détecteur et correcteur **strictement séparés**. Le détecteur (`validation/dd_base.py` `DDTaxonomy`) tourne sur l'extraction **brute**, avant toute correction ; `score_confiance` et anomalies en découlent. Le correcteur (`validation/corrector.py`) est un second système optionnel, journalisé (`corrections_appliquees`) et évalué à part. Voir `pipeline._certify_dd`. Test : `tests/test_corrector.py::test_detector_is_measured_on_raw_not_after_correction`. |
| **D2** | Pas de vérité de terrain ; « rappel » = taux de non-nuls | ✅ | **Vérité de terrain réelle construite et versionnée** : `evaluation/benchmark_ground_truth.py` résout ticker→CIK et tire les valeurs XBRL tagguées depuis SEC `companyfacts` pour **46 sociétés / 49 docs** → `data/benchmark_xbrl_ground_truth.json` (checksummé, `sha256:27764ee9…`). Join = CIK + **période (date de fin)** + form 10-K + unité USD ; le piège XBRL `fy` (colonnes comparatives) est corrigé en sélectionnant par date de fin de période, non par le tag `fy`. La FY cible par doc = période la plus proche de l'année du doc, **indépendante de toute sortie modèle** (pas de biais optimiste). `benchmark_compare.compute_metrics` **remplace** le « rappel » non-null par `field_accuracy_pct` = proportion de champs **corrects** vs XBRL à tolérance explicite (±1 %) ; le non-null est conservé mais renommé `coverage_pct` (couverture, pas justesse). Sans GT → `None`, jamais de chiffre inventé. Le regex-backfill qui gonflait le rappel de M4 est isolé (`corrector.regex_backfill`) et exclu de la sortie brute. Tests : `tests/test_benchmark_metric.py`. |
| **D3** | Étiquettes d'anomalie lues dans les noms de fichiers | ✅ | Matrices de confusion filename-based **supprimées** de `evaluation/dd_eval.py` et `evaluation/run_all_eval.py`. Remplacées par des étiquettes dérivées XBRL dans `evaluation/run_experiment.py` (E1). |
| **D4** | Baseline regex = homme de paille | ✅ | `benchmark_compare.method1_regex` importe désormais **exactement** `corrector.REGEX_BACKFILL_PATTERNS` (les motifs du système), même seuil `>= 100`. |
| **D5** | Provenance fabriquée (fnspid « SEC 10-K », dérives synthétiques) | ✅ | En-têtes `samples/fnspid/*.txt` réécrits : **DOCUMENT SYNTHÉTIQUE**, ne provient pas de FNSPID. Les 3 scripts de dérive synthétique supprimés (voir D9). Les vraies dérives sont l'objet d'E4. |
| **D6** | Détecteur d'ancrage = code mort + auto-neutralisé | ✅ | Déplacé dans `validation/anchoring.py`, **branché** dans `pipeline._certify_dd`. Le repli « chercher dans tout le document » est **supprimé**. Fenêtre paramétrable (`config.ANCHOR_WINDOW_*`) pour le balayage E5. |
| **D7** | Seuil JSD 0,55 recopié en dur en 8 endroits | ✅ | Source unique : `config.JSD_ALERT_THRESHOLD`. `semantic_monitor` l'importe ; marqué `# TO SWEEP (E4)`. Tous les consommateurs lisent cette valeur, y compris `product/` (`rag_corpus.THRESHOLD_RAG`, le repli de `dd_generate_report`, les docstrings d'`api.py`). Plus aucun littéral `0.55` fonctionnel hors la définition dans `config.py`. |
| **D8** | Corpus trop petit / déséquilibré ; comparaison FR/EN = longueur | 🧪 | Résolu par les données, pas par le code : E0 fournit des centaines de dépôts ; E9 apparie longueur/registre. Aucune conclusion FR/EN publiée d'ici là (documenté dans le README). |
| **D9** | 3 scripts inexécutables (`os.chdir` en dur, `fit()` incomplet) | ✅ | Les 3 scripts **supprimés** (`git rm`). Ils donnaient une image fausse du corpus. |
| **D10** | Modèle de base trop faible (8B seul) | 🧪 | `baselines.BaselineContext.model` permet un override par palier ; `run_experiment --model <id>` accepte ≥ 2 paliers. Décodage contraint par schéma JSON = B2. À lancer avec un modèle de pointe. |
| **D11** | Non reproductible (pas de README, deps, graines, snapshot) | ✅ | `README.md`, `requirements*.txt` épinglés, `Makefile`, `pyproject.toml`, graine unique `config.RANDOM_SEED`/`seed_everything`, manifeste GT figé + checksums (`xbrl_ground_truth.build_manifest`). |
| **D12** | Dépôt-produit (api.py 72 ko, connecteurs, HTML) | ✅ | Tout le code produit déplacé dans `product/`. L'artefact `semantic_firewall/` ne contient qu'extraction, validation, évaluation, monitoring. |

## Défauts mineurs

| Défaut | Statut | Où |
|--------|--------|----|
| `detect_language` biaisé 'en' sur égalité / chaîne vide ; œ, ÿ absents | ✅ | `monitoring/semantic_monitor.detect_language` : tie → `fr`, classe de caractères complétée. |
| Règles de correction non idempotentes / ordre non testé | ✅ | Correcteur en **point fixe** (convergence bornée) → ordre-insensible et idempotent. Tests dédiés. |
| Pertes silencieusement ignorées (guards `> 0`) | ✅ | Correcteur sign-aware (EBIT/EBITDA négatifs gérés). Test `test_loss_making_firm_not_dropped`. |
| Troncature invisible | ✅ | `pipeline._certify_dd` renvoie un bloc `truncation` (chars d'origine / envoyés / perdus). |
| Latence faussée par `time.sleep` | ✅ | `sleep` retiré de `dd_eval.call_dd_extraction` et du chemin mesuré de `benchmark_compare`. |
| Provenance `bilan_devoteam_2022` à vérifier | ⚠️ | À rapprocher du rapport annuel public **avant** diffusion d'un artefact ouvert (non automatisable ici). |

## Expériences

| Réf | Objet | Statut | Entrée |
|-----|-------|--------|--------|
| **E0** | Vérité de terrain XBRL | 🧪 | `evaluation/xbrl_ground_truth.py` — `run_experiment e0 --manifest cik_fy.csv`. Requiert `SEC_USER_AGENT` + réseau. |
| **E1** | Le détecteur seul : une violation prédit-elle une erreur ? (ROC-AUC, PR-AUC, P@k, calibration) | 🧪 | `evaluation/detector.py` + `metrics.py` — `run_experiment e1`. Requiert GT (E0) + LLM. |
| **E2** | Ladder B0–B6, ≥ 2 paliers de modèle | 🧪 | `evaluation/baselines.py` — `run_experiment e2 --baselines ... --model ...`. |
| **E3** | B4 (CoT) vs B5 : le CoT corrige-t-il l'arithmétique ? | 🧪 | `run_experiment e3` — matrice « valeur changée » × « devenue correcte ». |
| **E4** | Vraie dérive de distribution + balayage seuil JSD (AUROC) | ⬜ | À implémenter avec de vrais 10-K diachroniques/synchroniques (voir EXPERIMENTS.md). Le seuil est déjà `# TO SWEEP`. |
| **E5** | Ancrage comme détecteur d'hallucination + balayage fenêtre | 🧪 | Détecteur branché (D6) ; fenêtre paramétrable. Scorer contre XBRL comme E1. |
| **E6** | Ablation des règles de correction | 🧪 | `corrector.apply_corrections(disabled_rules=...)` — chaque règle est nommée et retirable. Test `test_ablation_...`. |
| **E7** | Analyse d'erreur stratifiée par secteur | ⬜ | La taxonomie sectorielle vit dans `benchmark_compare` (docs) ; à croiser avec GT E0 par (secteur × champ). |

⬜ = pas encore de code dédié (dépend entièrement de données réelles) ; le chemin est décrit dans `docs/EXPERIMENTS.md`.
