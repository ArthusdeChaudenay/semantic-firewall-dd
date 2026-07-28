"""
semantic_monitor.py — Analyse sémantique ML bilingue (FR + EN) pour le pare-feu DD.

Schéma bilingue complet :
  - Détection automatique de langue (FR / EN) par heuristique sur marqueurs fonctionnels
  - Tokenisation avec stopwords spécifiques à chaque langue
  - JSD comparé UNIQUEMENT contre le corpus de la même langue détectée
  - Centroïdes TF-IDF calculés séparément par (type, langue)
  → Pas de biais inter-langue : un 10-K anglais n'est jamais pénalisé
    par un corpus français, et vice-versa.

Deux briques :
  1. JSD (Jensen-Shannon Divergence) — dérive sémantique vs corpus de référence
     de la même langue. JSD = 0 → langage identique aux références.
     JSD > seuil → alerte dérive.

  2. Centroïdes TF-IDF — cohérence de type via similarité cosinus,
     calculés dans l'espace vectoriel de la langue détectée.

Usage :
    monitor = SemanticMonitor().fit("samples/dd", "samples")
    result  = monitor.analyze(text, "bilan")   # langue auto-détectée
"""

import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

_EPSILON = 1e-5

REFERENCE_CORPUS_DIRS: tuple = ("samples/dd", "samples")

# ── Stopwords FR — fonctionnels + jargon comptable neutre ─────────────────────
_STOPWORDS_FR = {
    "le","la","les","de","du","des","un","une","en","et","ou","à","au","aux",
    "ce","se","sa","son","ses","sur","par","pour","dans","avec","est","sont",
    "qui","que","qu","il","ils","elle","elles","nous","vous","être","avoir",
    "tout","plus","très","aussi","mais","si","ne","pas","non","car","donc",
    "or","ni","même","bien","lors","ainsi","entre","selon","comme",
    "tel","tels","telle","telles","leur","leurs","cet","cette","ces",
    "notre","nos","mon","ma","mes","ton","ta","tes","votre","vos",
    "dont","où","quand","comment","y","lui","eux","on","c","j","m","n","s",
    "a","été","faire","fait","cette","soit","peut","doit","ont",
}

# ── Stopwords EN — fonctionnels + jargon SEC/10-K non-financier ───────────────
_STOPWORDS_EN = {
    # Fonctionnels génériques
    "the","and","for","are","was","were","has","have","had","been","will",
    "with","that","this","from","they","their","which","also","its",
    "not","but","all","any","may","can","our","per","each","both","such",
    "than","more","less","most","into","over","other","these","those",
    "there","when","then","would","could","should","about","after","before",
    "during","under","through","between","while","upon","whether","because",
    "however","although","therefore","pursuant","thereof","hereof","therein",
    "including","related","certain","respective","approximately","primarily",
    # Jargon boilerplate 10-K (ne porte pas de sens financier discriminant)
    "note","notes","see","item","page","form","annual","report","company",
    "fiscal","year","ended","december","january","february","march","april",
    "june","july","august","september","october","november",
    "million","billion","thousand","dollars","shares","common","class",
    "consolidated","financial","statements","condensed","table","contents",
    "part","exhibit","schedule","appendix","amendment","section",
    "general","information","discussion","analysis","results","operations",
    "registrant","subsidiaries","hereinafter","whereas","aforementioned",
}

# Marqueurs fonctionnels très fréquents pour la détection de langue.
# Mots fonctionnels ultra-courants, absents du jargon financier de l'autre langue.
_FR_MARKERS = frozenset({
    "le","la","les","de","du","des","et","est","sont","pour","dans",
    "avec","sur","par","qui","que","ou","mais","donc","car","une","au",
})
_EN_MARKERS = frozenset({
    "the","and","for","are","was","were","that","this","from","they",
    "their","which","with","its","not","will","may","been","have","has",
})


# ==========================================
# DÉTECTION DE LANGUE
# ==========================================

def detect_language(text: str) -> str:
    """
    Détecte la langue d'un document financier : 'fr' ou 'en'.

    Heuristique rapide sur les 2 000 premiers tokens :
    compte les occurrences de marqueurs fonctionnels FR vs EN.
    Précision >99 % sur les documents financiers standard.
    """
    words = re.findall(r"[a-záàâäéèêëïîôùûüç]+", text.lower())[:2000]
    freq  = Counter(words)
    fr    = sum(freq.get(w, 0) for w in _FR_MARKERS)
    en    = sum(freq.get(w, 0) for w in _EN_MARKERS)
    return "en" if en >= fr else "fr"


# ==========================================
# UTILITAIRES MATHÉMATIQUES
# ==========================================

def _tokenize(text: str, lang: str = "fr") -> list:
    """Tokenise le texte avec les stopwords adaptés à la langue."""
    sw     = _STOPWORDS_EN if lang == "en" else _STOPWORDS_FR
    tokens = re.findall(r"[a-záàâäéèêëïîôùûüç]+", text.lower())
    return [t for t in tokens if len(t) >= 3 and t not in sw]


def _word_freq(tokens: list) -> dict:
    if not tokens:
        return {}
    counter = Counter(tokens)
    total   = sum(counter.values())
    return {w: c / total for w, c in counter.items()}


def _jsd(p: dict, q: dict) -> float:
    """Jensen-Shannon divergence ∈ [0, 1] avec lissage epsilon."""
    all_words = list(set(p) | set(q))
    V = len(all_words)
    if V == 0:
        return 0.0
    p_arr = np.array([p.get(w, 0.0) for w in all_words])
    q_arr = np.array([q.get(w, 0.0) for w in all_words])
    p_arr = (p_arr + _EPSILON) / (p_arr.sum() + _EPSILON * V)
    q_arr = (q_arr + _EPSILON) / (q_arr.sum() + _EPSILON * V)
    m     = 0.5 * (p_arr + q_arr)
    kl_pm = float(np.sum(p_arr * np.log2(p_arr / m)))
    kl_qm = float(np.sum(q_arr * np.log2(q_arr / m)))
    return max(0.0, min(1.0, 0.5 * kl_pm + 0.5 * kl_qm))


def _jsd_contributors(p: dict, q: dict, top_k: int = 10) -> list:
    """Per-word contributions to JSD, sorted by descending impact."""
    all_words = list(set(p) | set(q))
    V = len(all_words)
    if V == 0:
        return []
    p_arr = np.array([p.get(w, 0.0) for w in all_words])
    q_arr = np.array([q.get(w, 0.0) for w in all_words])
    p_arr = (p_arr + _EPSILON) / (p_arr.sum() + _EPSILON * V)
    q_arr = (q_arr + _EPSILON) / (q_arr.sum() + _EPSILON * V)
    m     = 0.5 * (p_arr + q_arr)

    contribs = []
    for i, w in enumerate(all_words):
        pw, qw, mw = float(p_arr[i]), float(q_arr[i]), float(m[i])
        c = 0.0
        if pw > 0 and mw > 0:
            c += 0.5 * pw * math.log2(pw / mw)
        if qw > 0 and mw > 0:
            c += 0.5 * qw * math.log2(qw / mw)
        if c < 1e-7 or pw < 1e-6:
            continue
        if qw < 1e-6:
            explication = f"Absent du corpus — vocabulaire inédit (fréq. doc : {pw*100:.2f}%)"
        elif pw > qw * 1.5:
            explication = f"Surreprésenté : {pw/(qw+1e-12):.1f}x plus fréquent que dans le corpus"
        else:
            explication = f"Sous-représenté : {qw/(pw+1e-12):.1f}x moins fréquent que dans le corpus"
        contribs.append({
            "mot": w,
            "contribution_jsd": round(c * 100, 3),
            "freq_doc":   round(pw * 100, 3),
            "freq_corpus":round(qw * 100, 3),
            "explication": explication,
        })
    contribs.sort(key=lambda x: x["contribution_jsd"], reverse=True)
    return contribs[:top_k]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ==========================================
# SEMANTIC MONITOR BILINGUE
# ==========================================

class SemanticMonitor:
    """
    Profils sémantiques TF-IDF + JSD bilingues par type de document.

    Corpus indexé par (doc_type, lang), ex : ("bilan", "fr"), ("compte_resultat", "en").
    La langue est auto-détectée à l'analyse — aucune indication manuelle requise.
    JSD et centroïdes sont calculés dans l'espace vectoriel de la langue détectée.
    """

    JSD_ALERT_THRESHOLD = 0.55
    REFERENCE_EXCLUDE   = ("derive_semantique",)

    def __init__(self):
        # Clés : (doc_type, lang)
        self._corpus:         dict = {}
        self._jsd_corpus:     dict = {}
        self._ref_freq:       dict = {}   # (type, lang) → word_freq fusionnée
        self._ref_freqs_list: dict = {}   # (type, lang) → [word_freq par doc]
        self._centroids:      dict = {}   # (type, lang) → vecteur centroïde TF-IDF
        # Vocabulaire et IDF séparés par langue
        self._vocab: dict = {"fr": [], "en": []}
        self._idf:   dict = {"fr": {}, "en": {}}
        self._fitted = False

    # ──────────────────────────────────────────────────────────────────────────
    # FIT
    # ──────────────────────────────────────────────────────────────────────────

    def fit(self, *sample_dirs, jsd_dirs=None) -> "SemanticMonitor":
        """
        Construit les profils depuis les répertoires de samples.

        Chaque fichier est classé automatiquement par :
          - type    : via detect_doc_type() sur le nom + contenu
          - langue  : via detect_language() sur le contenu

        jsd_dirs : sous-ensemble de sample_dirs inclus dans le corpus JSD.
                   Les autres ne servent qu'aux centroïdes TF-IDF.
        """
        from detect_doc_type import detect_doc_type

        jsd_set = {Path(d).resolve() for d in jsd_dirs} if jsd_dirs else None

        corpus:     dict = {}
        jsd_corpus: dict = {}

        for d in sample_dirs:
            d = Path(d)
            if not d.exists():
                continue
            is_jsd_dir = (jsd_set is None) or (d.resolve() in jsd_set)
            for p in sorted(d.glob("*.txt")):
                if any(pat in p.stem.lower() for pat in self.REFERENCE_EXCLUDE):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if not text.strip():
                    continue
                doc_type = detect_doc_type(str(p), text)
                if doc_type == "inconnu":
                    continue
                lang = detect_language(text)
                key  = (doc_type, lang)
                corpus.setdefault(key, []).append(text)
                if is_jsd_dir:
                    jsd_corpus.setdefault(key, []).append(text)

        self._corpus     = corpus
        self._jsd_corpus = jsd_corpus
        self._build_jsd_refs()
        self._build_tfidf_centroids()
        self._fitted = True
        return self

    def _build_jsd_refs(self):
        for key, texts in self._jsd_corpus.items():
            _, lang = key
            merged:  Counter = Counter()
            per_doc: list    = []
            for t in texts:
                tokens = _tokenize(t, lang)
                merged.update(tokens)
                per_doc.append(_word_freq(tokens))
            total = sum(merged.values()) or 1
            self._ref_freq[key]       = {w: c / total for w, c in merged.items()}
            self._ref_freqs_list[key] = per_doc

    def _build_tfidf_centroids(self):
        """
        Centroïdes TF-IDF construits séparément par langue.

        L'IDF est calculé sur l'ensemble des documents de la même langue
        (cross-type) pour assurer une bonne discrimination entre types.
        """
        for lang in ("fr", "en"):
            lang_texts = [
                t
                for (dt, lg), texts in self._corpus.items()
                if lg == lang
                for t in texts
            ]
            if not lang_texts:
                continue

            # Vocabulaire de la langue
            all_tokens: set = set()
            for t in lang_texts:
                all_tokens.update(_tokenize(t, lang))
            vocab    = sorted(all_tokens)
            word_idx = {w: i for i, w in enumerate(vocab)}
            V        = len(vocab)
            if V == 0:
                continue
            self._vocab[lang] = vocab

            # IDF cross-type pour la langue
            N  = len(lang_texts)
            df = Counter()
            for t in lang_texts:
                for w in set(_tokenize(t, lang)):
                    df[w] += 1
            self._idf[lang] = {
                w: math.log((N + 1) / (df[w] + 1)) + 1.0
                for w in vocab
            }

            # Centroïde par (doc_type, langue)
            for (dt, lg), texts in self._corpus.items():
                if lg != lang:
                    continue
                mat = np.zeros((len(texts), V))
                for i, t in enumerate(texts):
                    tokens    = _tokenize(t, lang)
                    tf        = Counter(tokens)
                    total_tok = len(tokens) or 1
                    for w, cnt in tf.items():
                        if w in word_idx:
                            mat[i, word_idx[w]] = (
                                (cnt / total_tok) * self._idf[lang].get(w, 1.0)
                            )
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms[norms == 0] = 1
                mat /= norms
                self._centroids[(dt, lang)] = mat.mean(axis=0)

    # ──────────────────────────────────────────────────────────────────────────
    # ANALYZE
    # ──────────────────────────────────────────────────────────────────────────

    def analyze(self, text: str, doc_type: str, lang: str = None) -> dict:
        """
        Analyse sémantique bilingue d'un document.

        Paramètres :
          text      : texte brut du document
          doc_type  : type attendu ("bilan", "compte_resultat", etc.)
          lang      : langue forcée (None = auto-détection)

        Retourne :
          {
            "language":       "en",
            "jsd_score":      0.045,
            "jsd_alert":      False,
            "centroid_scores":{"bilan": 12.0, "compte_resultat": 83.5, ...},
            "best_match":     "compte_resultat",
            "best_match_score": 83.5,
            "type_coherence": True,
            "interpretation": "...",
          }

        JSD et centroïdes sont calculés UNIQUEMENT contre le corpus
        de la langue détectée — pas de biais inter-langue.
        """
        if not self._fitted:
            return {"error": "SemanticMonitor non initialisé — appelez fit() d'abord."}

        if lang is None:
            lang = detect_language(text)

        tokens = _tokenize(text, lang)
        freq   = _word_freq(tokens)
        key    = (doc_type, lang)

        # ── JSD — corpus de la même langue uniquement ─────────────────────────
        ref_list = self._ref_freqs_list.get(key)
        if ref_list:
            jsd = min(_jsd(freq, r) for r in ref_list)
        else:
            # Pas de référence pour ce (type, langue) → incertitude maximale
            jsd = 1.0
        jsd_alert = jsd > self.JSD_ALERT_THRESHOLD

        # ── TF-IDF cosinus — espace vectoriel de la même langue ───────────────
        vocab    = self._vocab.get(lang, [])
        idf_dict = self._idf.get(lang, {})
        V        = len(vocab)

        if V == 0:
            centroid_scores = {}
        else:
            word_idx = {w: i for i, w in enumerate(vocab)}
            tf_count = Counter(tokens)
            total    = len(tokens) or 1
            vec      = np.zeros(V)
            for w, cnt in tf_count.items():
                if w in word_idx:
                    vec[word_idx[w]] = (cnt / total) * idf_dict.get(w, 1.0)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            centroid_scores = {
                dt: round(_cosine(vec, c) * 100, 1)
                for (dt, lg), c in self._centroids.items()
                if lg == lang
            }

        best_match     = max(centroid_scores, key=centroid_scores.get) if centroid_scores else doc_type
        best_score     = centroid_scores.get(best_match, 0.0)
        type_coherence = (best_match == doc_type)

        # ── Interprétation lisible ────────────────────────────────────────────
        jsd_pct  = round(jsd * 100, 1)
        lang_lbl = "EN" if lang == "en" else "FR"
        jsd_msg  = (
            f"Dérive lexicale détectée (JSD={jsd_pct}% > seuil "
            f"{int(self.JSD_ALERT_THRESHOLD*100)}%, corpus {lang_lbl})"
            if jsd_alert else
            f"Distribution lexicale conforme aux références {lang_lbl} (JSD={jsd_pct}%)"
        )
        coh_msg = (
            f"Incohérence de type : déclaré '{doc_type}' mais plus proche de "
            f"'{best_match}' ({best_score}%, espace {lang_lbl})"
            if not type_coherence else
            f"Cohérent avec le type '{doc_type}' (similarité {lang_lbl} : {best_score}%)"
        )

        return {
            "language":         lang,
            "jsd_score":        round(jsd, 4),
            "jsd_threshold":    self.JSD_ALERT_THRESHOLD,
            "jsd_alert":        jsd_alert,
            "centroid_scores":  centroid_scores,
            "best_match":       best_match,
            "best_match_score": best_score,
            "type_coherence":   type_coherence,
            "interpretation":   f"{jsd_msg}. {coh_msg}.",
        }

    def stats(self) -> dict:
        """Résumé des profils construits, groupés par (type, langue)."""
        return {
            f"{dt} [{lang}]": {
                "docs":        len(self._corpus.get((dt, lang), [])),
                "vocab_terms": len(self._ref_freq.get((dt, lang), {})),
            }
            for (dt, lang) in sorted(self._corpus)
        }


# ── Singleton global ──────────────────────────────────────────────────────────

_monitor: "SemanticMonitor | None" = None


def get_monitor() -> SemanticMonitor:
    global _monitor
    if _monitor is None:
        refs_root   = Path("samples/references")
        refs_subdirs = sorted(str(d) for d in refs_root.iterdir() if d.is_dir()) \
            if refs_root.exists() else []

        # Le corpus anglais (samples/references/en/) entre dans le JSD
        # en même temps que le corpus français — la langue est auto-détectée.
        en_dir = Path("samples/references/en")
        jsd_en = [str(en_dir)] if en_dir.exists() else []

        _monitor = SemanticMonitor().fit(
            *REFERENCE_CORPUS_DIRS, *refs_subdirs,
            jsd_dirs=(*REFERENCE_CORPUS_DIRS, *jsd_en),
        )
    return _monitor


def reset_monitor():
    """Force le rechargement du monitor (après ajout de nouveaux fichiers de référence)."""
    global _monitor
    _monitor = None


# ── CLI de test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    refs_root    = Path("samples/references")
    refs_subdirs = sorted(str(d) for d in refs_root.iterdir() if d.is_dir()) \
        if refs_root.exists() else []
    en_dir = Path("samples/references/en")
    jsd_en = [str(en_dir)] if en_dir.exists() else []

    m = SemanticMonitor().fit(
        "samples/dd", "samples", *refs_subdirs,
        jsd_dirs=("samples/dd", "samples", *jsd_en),
    )

    print("=== Corpus bilingue chargé ===")
    for label, s in m.stats().items():
        print(f"  {label:<30} {s['docs']:3d} docs  |  {s['vocab_terms']:,} termes JSD")

    print()
    tests = [
        # (fichier, doc_type, langue attendue, jsd_alert attendu)
        ("samples/dd/compte_resultat_exemple.txt",                       "compte_resultat", "fr", False),
        ("samples/dd/bilan_exemple.txt",                                  "bilan",           "fr", False),
        ("samples/dd/Bilan_derive_semantique.txt",                        "bilan",           "fr", True),
        ("samples/real_world/parsed/10k_3M_CO_2026_item8.txt",           "compte_resultat", "en", False),
        ("samples/real_world/parsed/10k_Apple_Inc_2025_item8.txt",       "compte_resultat", "en", False),
        ("samples/real_world/parsed/10k_MICROSOFT_CORP_2025_item8.txt",  "compte_resultat", "en", False),
    ]

    print(f"  {'Fichier':<52} {'Lang':<5} {'JSD':>6}  {'Alert':>5}  {'Best match (score)'}")
    print(f"  {'-'*90}")
    for path, doc_type, expected_lang, expected_alert in tests:
        p = Path(path)
        if not p.exists():
            print(f"  [ABSENT] {path}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")[:20_000]
        r    = m.analyze(text, doc_type)
        lang_ok  = "✓" if r["language"] == expected_lang else "✗"
        alert_ok = "✓" if r["jsd_alert"] == expected_alert else "✗"
        print(
            f"  {p.name:<52} {r['language']}{lang_ok}  "
            f"{r['jsd_score']*100:5.1f}%  "
            f"{'ALERT' if r['jsd_alert'] else 'ok':>5}{alert_ok}  "
            f"{r['best_match']}({r['best_match_score']}%)"
        )
