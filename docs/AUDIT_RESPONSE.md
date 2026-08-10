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
| **D7** | Seuil JSD 0,55 recopié en dur en 8 endroits | ✅ | Source unique : `config.JSD_ALERT_THRESHOLD`. `semantic_monitor` l'importe ; marqué `# TO SWEEP (E4)`. Les générateurs de rapport HTML (product/) doivent lire cette valeur. |
| **D8** | Corpus trop petit / déséquilibré ; comparaison FR/EN = longueur | 🧪 | Résolu par les données, pas par le code : E0 fournit des centaines de dépôts ; E9 apparie longueur/registre. Aucune conclusion FR/EN publiée d'ici là (documenté dans le README). |
| **D9** | 3 scripts inexécutables (`os.chdir` en dur, `fit()` incomplet) | ✅ | Les 3 scripts **supprimés** (`git rm`). Ils donnaient une image fausse du corpus. |
| **D10** | Modèle de base trop faible (8B seul) | 🧪 | `baselines.BaselineContext.model` permet un override par palier ; `run_experiment --model <id>` accepte ≥ 2 paliers. Décodage contraint par schéma JSON = B2. À lancer avec un modèle de pointe. |
| **D11** | Non reproductible (pas de README, deps, graines, snapshot) | ✅ | `README.md`, `requirements*.txt` épinglés, `Makefile`, `pyproject.toml`, graine unique `config.RANDOM_SEED`/`seed_everything`, manifeste GT figé + checksums (`xbrl_ground_truth.build_manifest`). |
| **D12** | Dépôt-produit (api.py 72 ko, connecteurs, HTML) | ✅ | Tout le code produit déplacé dans `product/`. L'artefact `semantic_firewall/` ne contient qu'extraction, validation, évaluation, monitoring. |

## Défauts découverts au second passage d'audit (D13–D15)

Ces trois défauts ont été introduits ou révélés par les corrections D1–D12 elles-mêmes.
D13 était **bloquant** : le code tournait, produisait des nombres, et ils étaient tous
faux dans le même sens — pire qu'un plantage.

| Réf | Défaut | Statut | Où / comment |
|-----|--------|--------|--------------|
| **D13** | **Incompatibilité d'échelle.** XBRL est en dollars absolus (`416161000000`), les tableaux impriment des millions (`416161`), et le prompt dit « ne PAS multiplier ». Résultat : **100 % des comparaisons échouaient**, donc 0 % de justesse pour toutes les baselines et une AUROC indéfinie faute de négatifs. | ✅ | Nouveau module `extraction/scale.py` : `infer_document_scale()` lit le multiplicateur **dans le texte du dépôt** et enregistre la chaîne de preuve. Le sens de l'inférence est essentiel — aligner sur la puissance de 1000 la plus proche de la GT ferait fuiter l'étiquette dans la prédiction et recréerait D1 ailleurs. `score_extraction()` et `compute_metrics()` prennent désormais `scale=`. Les dépôts sans échéle déclarée sont **exclus** (13 documents), pas devinés. Bonus : `classify_error()` distingue `correct` / `scale` / `sign` / `wrong_value`, donc une erreur d'unité n'est plus comptée comme une erreur de lecture — et comme elle laisse toutes les identités exactement satisfaites, elle est exclue des étiquettes du détecteur. Tests : `tests/test_scale.py` (18 tests, dont des fixtures aux unités **différentes** de la GT — c'est précisément ce que l'ancienne suite ne faisait pas). |
| **D14** | **Décalage d'un an sur 32/46 enregistrements.** `nearest_fiscal_year()` retombait sur l'exercice disponible le plus proche ; tous les docs étiquetés 2026 se résolvaient en FY2025. Un an d'écart sur le CA dépasse toute tolérance et est indiscernable d'une erreur d'extraction. | ✅ | Corrigé **à la racine** : `scripts/build_benchmark_corpus.py` lit l'exercice dans le `reportDate` du dépôt lui-même, donc document et étiquette portent sur la même période par construction — le décalage ne peut plus apparaître. Filet de sécurité conservé : chaque enregistrement porte `year_mismatch`, et `load()` les exclut par défaut en rapportant le décompte (`require_year_match=False` pour une analyse de sensibilité). Tests : `tests/test_ground_truth_hygiene.py`. |
| **D15** | **Trous de couverture masqués.** 20/46 sans D&A, 13/46 sans EBIT : l'identité EBITDA n'était évaluable que sur 26/46 docs, sans que ce soit dit. | ✅ | `coverage_report()` publie le *n* effectif **par champ** et compte les champs `derived`. Le papier rapporte cette table (couverture 63,5 % à 100 % selon le champ) et signale que l'EBITDA de référence est dérivé, donc couplé à l'identité testée. |

**Ce que ces corrections ont permis de mesurer** (et qui était invisible avant) :
le détecteur d'identité est **inerte sur le compte de résultat** — l'EBITDA n'étant pas
une ligne GAAP, le modèle renvoie `null` dans 0/69 cas et l'identité n'est jamais
calculable, d'où une AUROC de 0,500 qui est une dégénérescence, pas une mesure. Sur le
bilan, dont les termes sont imprimés partout, la même mécanique est calculable sur
68/69 et atteint **AUROC 0,753 avec précision 1,00 sur ses documents les mieux
classés**. Voir `paper/main.tex`.

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
| **E0** | Vérité de terrain XBRL | ✅ **exécuté** | 104 dépôts 10-K réels, 55 sociétés, 11 secteurs, FY2024–2026. `data/corpus.jsonl` + `data/ground_truth.jsonl`, checksummés. Exercice lu dans le dépôt (D14). |
| **E1** | Le détecteur seul : une violation prédit-elle une erreur ? | ✅ **exécuté** | **Compte de résultat : identité calculable 0/69 → AUROC 0,500 (dégénérée).** Bilan : calculable 68/69 → **AUROC 0,753, PR-AUC 0,791, P@6 = 1,00**. C'est le résultat central du papier. |
| **E2** | Ladder B1–B6, 2 paliers de modèle | ✅ **exécuté** | 8B : 66,9 / 73,3 / 73,3 / 79,7 / 73,3 / **84,5**. 49B : 66,9 / 77,4 / – / 89,9 / 77,4 / **91,2**. Comparaison **appariée** sur les 69 docs communs aux deux paliers. |
| **E3** | B4 (CoT) vs B6 : le CoT corrige-t-il l'arithmétique ? | ✅ **exécuté** | Le CoT **aide** (19 champs corrigés / 0 dégradé sur 8B ; 37/0 sur 49B) mais reste sous le correcteur déterministe pour le double d'appels. |
| **E4** | Vraie dérive de distribution + balayage seuil JSD | ⬜ | Non fait, et **aucun résultat de dérive n'est publié** — le papier le dit explicitement en Limitations plutôt que de reprendre les anciens chiffres synthétiques. |
| **E5** | Ancrage comme détecteur d'hallucination + balayage fenêtre | ✅ **exécuté** | Fenêtre la plus étroite optimale : 8B P=69,0 R=38,5 **F1=49,4** ; 49B F1=20,6. Signal complémentaire, pas compétitif. |
| **E6** | Ablation des règles de correction | ✅ **exécuté** | **Le leave-one-out mentait** : les 8 règles sont individuellement inertes (Δ=0,0) alors que l'ablation **par bloc** montre qu'elles portent tout le gain (−11,2 pt / −13,8 pt). Jeu de règles redondant. Le regex-backfill ne contribue **rien** (0,0 pt). |
| **E7** | Analyse d'erreur stratifiée par secteur | ✅ **exécuté** | 33,3 % (Énergie) à 100 % (Paiements). Banque, Automobile et Énergie ne gagnent **rien** au pare-feu : pas de ligne *Operating income* ou D&A hors compte de résultat. |

**Résultats bruts** : `data/paper_results.json`. **Rejouables sans réseau** depuis
`data/extractions/<modèle>/<méthode>/` via
`python -m scripts.run_paper_experiments report --paired --models <a>,<b>`.
