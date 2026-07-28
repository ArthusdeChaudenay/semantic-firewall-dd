"""
rag_corpus.py — Corpus RAG avec gate Jensen-Shannon pour fonds d'investissement.

Architecture :
  - Un cluster par type de document (bilan / compte_resultat / captable / facture)
  - Seuil statique THRESHOLD_RAG = 0.55 (calibré sur la frontière légitime/dérivant)
  - Indexation batch par data room, jamais en continu
  - Persistance JSON → output/rag_corpus.json

Fonctionnement du gate :
  JSD ≤ seuil  → conforme au corpus de référence → indexé
  JSD > seuil  → dérive de distribution lexicale détectée → exclu du corpus RAG

Usage typique (ouverture d'une data room) :
    corpus = RagCorpus.load()
    rapport = corpus.index_batch(["bilan_2025.txt", "cr_2025.txt"], deal="AlphaTech")

Recherche :
    docs = corpus.query("bilan actif immobilisé", doc_type="bilan", top_k=3)
"""

from __future__ import annotations

import json
import numpy as np
from pathlib import Path

from semantic_monitor import _tokenize, _word_freq, _cosine, get_monitor

# ── Constantes ────────────────────────────────────────────────────────────────

CORPUS_PATH   = Path("output/rag_corpus.json")
THRESHOLD_RAG = 0.55   # seuil statique calibré — JSD ≤ 0.55 → indexé, > 0.55 → exclu


# ==========================================
# RAG CORPUS
# ==========================================

class RagCorpus:
    """
    Corpus RAG avec gate JSD sur corpus de référence immuable.

    Le gate utilise SemanticMonitor (corpus golden statique) pour calculer
    le JSD — pas un centroïde glissant qui dérive à chaque indexation.

    Attributs persistés :
      _docs : liste des documents indexés (métadonnées + freq)
    """

    def __init__(self):
        self._docs: list[dict] = []

    # ─────────────────────────────────────────────────────────────────────────
    # PERSISTANCE
    # ─────────────────────────────────────────────────────────────────────────

    def save(self, path: Path = CORPUS_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"docs": self._docs}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path = CORPUS_PATH) -> "RagCorpus":
        c = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                c._docs = data.get("docs", [])
            except Exception:
                pass
        return c

    # ─────────────────────────────────────────────────────────────────────────
    # GATE JSD (seuil statique)
    # ─────────────────────────────────────────────────────────────────────────

    def gate(self, text: str, doc_type: str, source: str = "") -> dict:
        """
        Évalue si un document mérite d'être indexé dans le corpus RAG.

        Ordre des vérifications :
          1. Doublon exact (même source déjà indexée) → exclu immédiatement
          2. Monitor non initialisé (error dans analyze) → exclu par précaution
          3. JSD == 0.0 sans référence pour ce type → exclu par précaution
          4. JSD < THRESHOLD_RAG → conforme → indexé
          5. JSD ≥ THRESHOLD_RAG → dérive de distribution lexicale → exclu
        """
        # ── 1. Vérification doublon ────────────────────────────────────────────
        if source and any(d.get("source") == source for d in self._docs):
            return {
                "should_index": False,
                "jsd":          None,
                "threshold":    THRESHOLD_RAG,
                "reason":       f"Doublon déjà indexé : {source}",
            }

        # ── 2. Appel monitor ──────────────────────────────────────────────────
        monitor  = get_monitor()
        analysis = monitor.analyze(text, doc_type)

        if "error" in analysis:
            return {
                "should_index": False,
                "jsd":          None,
                "threshold":    THRESHOLD_RAG,
                "reason":       f"Monitor non initialisé — {analysis['error']}",
            }

        jsd_val = analysis["jsd_score"]

        # ── 3. JSD == 0.0 sans référence pour ce type → exclusion préventive ──
        if jsd_val == 0.0 and not monitor._ref_freqs_list.get(doc_type):
            return {
                "should_index": False,
                "jsd":          0.0,
                "threshold":    THRESHOLD_RAG,
                "reason":       f"Aucun document de référence pour le type '{doc_type}' — exclu par précaution.",
            }

        # ── 4 & 5. Décision gate ──────────────────────────────────────────────
        should = jsd_val < THRESHOLD_RAG
        reason = (
            f"JSD={jsd_val:.3f} < seuil={THRESHOLD_RAG} → conforme au corpus de référence, indexé."
            if should else
            f"JSD={jsd_val:.3f} >= seuil={THRESHOLD_RAG} → dérive de distribution lexicale détectée, exclu."
        )
        return {"should_index": should, "jsd": round(jsd_val, 4), "threshold": THRESHOLD_RAG, "reason": reason}

    # ─────────────────────────────────────────────────────────────────────────
    # INDEXATION
    # ─────────────────────────────────────────────────────────────────────────

    def index_doc(self, text: str, doc_type: str, source: str = "", deal: str = "") -> dict:
        """Indexe un document si le gate JSD l'autorise. Ne sauvegarde pas — appeler save()."""
        gate_result = self.gate(text, doc_type, source=source)
        if not gate_result["should_index"]:
            return {"indexed": False, "source": source, **gate_result}

        freq   = _word_freq(_tokenize(text))
        doc_id = f"{doc_type}_{len(self._docs):04d}"
        self._docs.append({
            "doc_id":   doc_id,
            "doc_type": doc_type,
            "deal":     deal,
            "source":   source,
            "summary":  text[:500],
            "freq":     freq,
            "jsd":      gate_result["jsd"],
        })

        return {"indexed": True, "doc_id": doc_id, "source": source, **gate_result}

    def index_batch(self, file_paths: list, deal: str = "") -> dict:
        """
        Indexe un batch de fichiers (data room complète).
        Sauvegarde automatique à la fin du batch.
        """
        from detect_doc_type import detect_doc_type

        results  = []
        indexed  = 0
        skipped  = 0

        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                results.append({"source": str(p), "indexed": False, "reason": "Fichier introuvable."})
                skipped += 1
                continue

            try:
                if p.suffix.lower() in (".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"):
                    from llm_extractor import extract_text_from_file
                    text = extract_text_from_file(str(p))
                else:
                    text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                results.append({"source": str(p), "indexed": False, "reason": str(e)})
                skipped += 1
                continue

            doc_type = detect_doc_type(str(p), text)
            if doc_type == "inconnu":
                results.append({"source": str(p), "indexed": False, "reason": "Type inconnu, ignoré."})
                skipped += 1
                continue

            r = self.index_doc(text, doc_type, source=p.name, deal=deal)
            results.append(r)
            if r["indexed"]:
                indexed += 1
            else:
                skipped += 1

        self.save()

        return {
            "deal":        deal,
            "total":       len(file_paths),
            "indexed":     indexed,
            "skipped":     skipped,
            "corpus_size": len(self._docs),
            "details":     results,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # REQUÊTE
    # ─────────────────────────────────────────────────────────────────────────

    def query(self, text: str, doc_type: str | None = None, top_k: int = 3) -> list[dict]:
        """
        Retourne les top_k documents les plus proches (similarité cosinus TF-IDF).
        Si doc_type est fourni, filtre sur ce type uniquement.
        """
        if not self._docs:
            return []

        freq_q    = _word_freq(_tokenize(text))
        candidates = [d for d in self._docs if doc_type is None or d["doc_type"] == doc_type]
        if not candidates:
            return []

        all_words = list({w for d in candidates for w in d["freq"]} | set(freq_q))
        V         = len(all_words)
        idx       = {w: i for i, w in enumerate(all_words)}

        def to_vec(freq: dict) -> np.ndarray:
            v = np.zeros(V)
            for w, f in freq.items():
                if w in idx:
                    v[idx[w]] = f
            n = np.linalg.norm(v)
            return v / n if n > 0 else v

        q_vec  = to_vec(freq_q)
        scored = []
        for d in candidates:
            sim = float(np.dot(q_vec, to_vec(d["freq"])))
            scored.append({
                "doc_id":    d["doc_id"],
                "doc_type":  d["doc_type"],
                "deal":      d["deal"],
                "source":    d["source"],
                "summary":   d["summary"],
                "similarity": round(sim * 100, 1),
            })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    # ─────────────────────────────────────────────────────────────────────────
    # DIAGNOSTIC
    # ─────────────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        clusters: dict = {}
        for d in self._docs:
            dt = d["doc_type"]
            if dt not in clusters:
                clusters[dt] = {"count": 0, "deals": set()}
            clusters[dt]["count"] += 1
            if d["deal"]:
                clusters[dt]["deals"].add(d["deal"])

        return {
            "total_docs": len(self._docs),
            "threshold":  THRESHOLD_RAG,
            "clusters":   {
                dt: {
                    "count": v["count"],
                    "deals": sorted(v["deals"]),
                }
                for dt, v in clusters.items()
            },
        }


# ── Singleton global ──────────────────────────────────────────────────────────

_corpus: "RagCorpus | None" = None


def get_corpus() -> RagCorpus:
    global _corpus
    if _corpus is None:
        _corpus = RagCorpus.load()
    return _corpus


# ── CLI de test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== RagCorpus — test d'indexation ===\n")

    corpus = RagCorpus()   # corpus vierge pour le test

    # Batch 1 : deal AlphaTech (bilans + CR nominaux)
    batch1 = sorted(str(p) for p in Path("samples/dd").glob("*.txt")
                    if "derive" not in p.stem and "erreur" not in p.stem)
    print(f"Batch 1 — deal 'AlphaTech' ({len(batch1)} fichiers) :")
    r1 = corpus.index_batch(batch1, deal="AlphaTech")
    print(f"  Indexés : {r1['indexed']}  |  Ignorés : {r1['skipped']}")
    for d in r1["details"]:
        flag = "✓" if d["indexed"] else "✗"
        print(f"  {flag} {d['source']:45s}  JSD={d.get('jsd', '-'):.3f}  seuil={d.get('threshold', '-'):.3f}")

    print()

    # Batch 2 : même docs → doit être largement ignoré (JSD faible, corpus déjà nourri)
    print(f"Batch 2 — même deal, mêmes documents (doublons attendus) :")
    r2 = corpus.index_batch(batch1, deal="AlphaTech")
    print(f"  Indexés : {r2['indexed']}  |  Ignorés : {r2['skipped']}")

    print()

    # Batch 3 : factures (nouveau cluster)
    batch3 = sorted(str(p) for p in Path("samples/factures").glob("*.txt"))
    if not batch3:
        batch3 = sorted(str(p) for p in Path("samples").glob("*.txt"))
    print(f"Batch 3 — deal 'BetaCorp' factures ({len(batch3)} fichiers) :")
    r3 = corpus.index_batch(batch3, deal="BetaCorp")
    print(f"  Indexés : {r3['indexed']}  |  Ignorés : {r3['skipped']}")

    print()
    st = corpus.stats()
    print(f"Stats corpus final (seuil statique = {st['threshold']}) :")
    for dt, s in st["clusters"].items():
        print(f"  {dt:20s}  {s['count']} docs  |  deals: {s['deals']}")

    print()
    print("Requête : 'bilan actif immobilisé résultat net' (top 3 bilans) :")
    hits = corpus.query("bilan actif immobilisé résultat net", doc_type="bilan", top_k=3)
    for h in hits:
        print(f"  [{h['similarity']}%] {h['source']}  ({h['deal']})")
