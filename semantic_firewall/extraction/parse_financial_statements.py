"""
parse_financial_statements.py — Extraction Item 8 + géométrie spatiale bounding box.

Sources (par ordre de priorité) :
  PDF  samples/real_world/sec_10k_pdf/  → pdfplumber : pages, tables (bbox), mots (bbox)
  HTM  samples/real_world/sec_10k/      → html.parser : texte brut uniquement

Sorties dans samples/real_world/parsed/ :
  {stem}_item8.txt      — texte brut de la section Item 8
  {stem}_spatial.json   — métadonnées spatiales (bboxes, tables, pages)

Usage :
    .\\venv\\Scripts\\python.exe parse_financial_statements.py
"""

import json
import re
import sys
from pathlib import Path

HTM_DIR    = Path("samples/real_world/sec_10k")
PDF_DIR    = Path("samples/real_world/sec_10k_pdf")
OUTPUT_DIR = Path("samples/real_world/parsed")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Patterns de détection des sections ───────────────────────────────────────

ITEM8_RE = re.compile(
    r"item\s*8[\.\-\s]*(?:financial\s+statements|consolidated\s+financial|"
    r"supplementary\s+data|financial\s+statements\s+and|"
    r"états\s+financiers)",
    re.I,
)
ITEM9_RE = re.compile(r"item\s*9[\.\-\s]", re.I)

# Mots et patterns financièrement pertinents pour l'extraction spatiale
FIN_WORD_RE = re.compile(
    r"^-?[\d,.\s]+$"
    r"|^[\(\d][\d,.]+\)?$"
    r"|^(total|revenue|revenues|income|loss|assets?"
    r"|liabilit|liabilities|equity|ebitda|ebit|net|gross|operating"
    r"|depreciation|amortization|interest|goodwill|impairment"
    r"|chiffre|actif|passif|capitaux|résultat|bilan)$",
    re.I,
)


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _find_item8_range(text: str) -> tuple[int, int]:
    m8 = ITEM8_RE.search(text)
    if not m8:
        return 0, min(len(text), 500_000)
    m9 = ITEM9_RE.search(text, m8.end() + 500)
    return m8.start(), (m9.start() if m9 else len(text))


def _clean_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, rep in [
        ("&nbsp;",  " "), ("&#160;",  " "), ("&amp;",  "&"),
        ("&lt;",    "<"), ("&gt;",    ">"), ("&ldquo;", '"'),
        ("&rdquo;", '"'), ("&lsquo;", "'"), ("&rsquo;", "'"),
        ("&#8211;", "-"), ("&#8212;", "—"), ("&#8217;", "'"),
    ]:
        text = text.replace(ent, rep)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Parseur PDF ───────────────────────────────────────────────────────────────

def parse_pdf(path: Path) -> dict | None:
    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber non installé")
        return None

    pages_spatial: list[dict] = []
    item8_parts:   list[str]  = []
    in_item8  = False
    item8_end = False
    total_pages = 0

    try:
        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)
            print(f"({total_pages} p.) ", end="", flush=True)

            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""

                if not in_item8 and ITEM8_RE.search(text):
                    in_item8 = True
                # Les PDFs Item-8-only démarrent dès la page 1
                if i == 0 and not in_item8 and len(text) > 200:
                    in_item8 = True

                if in_item8 and not item8_end and i > 0 and ITEM9_RE.search(text):
                    item8_end = True

                if not in_item8 or item8_end:
                    continue

                item8_parts.append(text)

                # ── Mots financiers avec bbox ─────────────────────────────
                fin_words: list[dict] = []
                try:
                    for w in page.extract_words(x_tolerance=3, y_tolerance=3):
                        if FIN_WORD_RE.match(w.get("text", "")):
                            fin_words.append({
                                "text": w["text"],
                                "bbox": [
                                    round(w["x0"],    1),
                                    round(w["top"],   1),
                                    round(w["x1"],    1),
                                    round(w["bottom"],1),
                                ],
                                "page": i + 1,
                            })
                except Exception:
                    pass

                # ── Tables avec bbox (pdfplumber 0.7+) ───────────────────
                table_bboxes: list[dict] = []
                try:
                    for tbl in page.find_tables():
                        rows = tbl.extract()
                        if not rows:
                            continue
                        table_bboxes.append({
                            "bbox":          [round(c, 1) for c in tbl.bbox],
                            "rows":          len(rows),
                            "cols":          max((len(r) for r in rows if r), default=0),
                            "header_sample": (rows[0][:6] if rows else []),
                        })
                except Exception:
                    pass

                pages_spatial.append({
                    "page":            i + 1,
                    "text_chars":      len(text),
                    "table_bboxes":    table_bboxes[:15],
                    "financial_words": fin_words[:200],
                })

    except Exception as e:
        print(f"ERREUR pdfplumber : {e}")
        return None

    return {
        "source":        path.name,
        "format":        "pdf",
        "total_pages":   total_pages,
        "item8_pages":   len(pages_spatial),
        "item8_text":    "\n".join(item8_parts),
        "pages_spatial": pages_spatial,
    }


# ── Parseur HTM ───────────────────────────────────────────────────────────────

def parse_htm(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"ERREUR lecture : {e}")
        return None

    clean = _clean_html(raw)
    s, e_ = _find_item8_range(clean)
    item8 = clean[s:e_].strip()

    return {
        "source":        path.name,
        "format":        "htm",
        "total_chars":   len(clean),
        "item8_chars":   len(item8),
        "item8_text":    item8,
        "pages_spatial": [],
    }


# ── Pipeline principal ────────────────────────────────────────────────────────

def process(path: Path) -> bool:
    suffix = path.suffix.lower()
    src    = "PDF" if suffix == ".pdf" else "HTM"
    print(f"  {path.name:<55} [{src}] ", end="", flush=True)

    if suffix == ".pdf":
        result = parse_pdf(path)
    elif suffix in (".htm", ".html"):
        result = parse_htm(path)
    else:
        print("format ignoré.")
        return False

    if not result:
        return False

    stem      = path.stem
    txt_path  = OUTPUT_DIR / f"{stem}_item8.txt"
    json_path = OUTPUT_DIR / f"{stem}_spatial.json"

    txt_path.write_text(result["item8_text"], encoding="utf-8")
    spatial_meta = {k: v for k, v in result.items() if k != "item8_text"}
    json_path.write_text(
        json.dumps(spatial_meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    item8_kb  = len(result["item8_text"]) // 1024
    n_tables  = sum(len(p.get("table_bboxes",    [])) for p in result.get("pages_spatial", []))
    n_words   = sum(len(p.get("financial_words", [])) for p in result.get("pages_spatial", []))
    n_pages   = result.get("item8_pages", 0) or result.get("item8_chars", 0) // 3000

    print(f"Item8={item8_kb:4d}ko  pages~{n_pages:3d}  "
          f"tables={n_tables:3d}  mots_fin={n_words:5d}")
    return True


def main():
    # ── Construction de la file : PDF prioritaire sur HTM ──────────────────
    # Clé normalisée = stem sans les préfixes de type (compte_resultat_, bilan_…)
    _TYPE_PREFIXES = ("compte_resultat_", "bilan_", "captable_", "facture_")

    def _base_key(p: Path) -> str:
        s = p.stem
        for pfx in _TYPE_PREFIXES:
            if s.startswith(pfx):
                return s[len(pfx):]
        return s

    file_map: dict[str, Path] = {}

    for f in sorted(HTM_DIR.glob("10k_*.htm")):
        file_map[_base_key(f)] = f

    if PDF_DIR.exists():
        for f in sorted(PDF_DIR.glob("*.pdf")):
            file_map[_base_key(f)] = f   # PDF écrase le HTM pour le même document

    files = sorted(file_map.values(), key=lambda p: p.name)

    if not files:
        print(f"Aucun fichier 10k_* dans {HTM_DIR}/ ni {PDF_DIR}/\n"
              f"Lancez d'abord : .\\venv\\Scripts\\python.exe download_sec_corpus.py")
        return

    n_pdf = sum(1 for f in files if f.suffix == ".pdf")
    n_htm = sum(1 for f in files if f.suffix == ".htm")
    print(f"=== parse_financial_statements — {len(files)} fichiers "
          f"({n_pdf} PDF + {n_htm} HTM) ===\n")

    ok = errors = 0
    for f in files:
        try:
            if process(f):
                ok += 1
            else:
                errors += 1
        except Exception as e:
            print(f"EXCEPTION : {e}")
            errors += 1

    # ── Résumé global ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Parsés : {ok}  |  Erreurs : {errors}")
    print(f"  Sortie : {OUTPUT_DIR.resolve()}")

    total_tables = total_words = total_item8_pages = 0
    for jf in sorted(OUTPUT_DIR.glob("*_spatial.json")):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            for p in d.get("pages_spatial", []):
                total_tables      += len(p.get("table_bboxes",    []))
                total_words       += len(p.get("financial_words", []))
                total_item8_pages += 1
        except Exception:
            pass

    if total_item8_pages:
        print(f"\n  Annotations spatiales (PDFs) :")
        print(f"    Pages Item 8  : {total_item8_pages}")
        print(f"    Tables (bbox) : {total_tables}")
        print(f"    Mots financ.  : {total_words}")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
