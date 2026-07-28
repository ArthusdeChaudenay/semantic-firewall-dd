"""
convert_htm_to_pdf.py — Convertit les 10-K HTM EDGAR en PDF via Chromium (playwright).

Stratégie :
  Pour chaque HTM, extrait le fragment HTML Item 8 (styles inclus), le wrappe
  dans un document autonome minimaliste, puis l'imprime en PDF A4.
  → PDFs légers (2-8 Mo) centrés sur les états financiers, prêts pour pdfplumber.

Usage :
    .\\venv\\Scripts\\python.exe convert_htm_to_pdf.py [--limit N]
"""

import re
import sys
import time
import argparse
from pathlib import Path

INPUT_DIR  = Path("samples/real_world/sec_10k")
OUTPUT_DIR = Path("samples/real_world/sec_10k_pdf")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Patterns de détection des sections ───────────────────────────────────────

ITEM8_RAW_RE = re.compile(
    r"item\s*8[\.\-\s]*(?:financial\s+statements|consolidated\s+financial|"
    r"supplementary\s+data|financial\s+statements\s+and)",
    re.I,
)
ITEM9_RAW_RE = re.compile(r"item\s*9[\.\-\s]", re.I)


# ── Extraction du fragment HTML Item 8 ───────────────────────────────────────

def _extract_styles(html: str) -> str:
    """Récupère tous les blocs <style> de la page originale."""
    blocks = re.findall(r"<style[^>]*>.*?</style>", html, re.DOTALL | re.IGNORECASE)
    return "\n".join(blocks)


def _strip_tags_for_search(html: str) -> str:
    """Version texte brut pour la détection de sections (ne garde pas les offsets)."""
    return re.sub(r"<[^>]+>", " ", html)


def _find_tag_offset(html: str, text_offset: int) -> int:
    """
    Convertit un offset dans le texte dépouillé en offset approximatif dans le HTML brut.
    Avance caractère par caractère en sautant les balises.
    """
    tag_pos   = 0
    text_seen = 0
    n         = len(html)

    while tag_pos < n and text_seen < text_offset:
        if html[tag_pos] == "<":
            end = html.find(">", tag_pos)
            tag_pos = end + 1 if end != -1 else n
        else:
            text_seen += 1
            tag_pos   += 1

    return tag_pos


def extract_item8_html(html: str) -> str:
    """
    Retourne un document HTML autonome contenant uniquement la section Item 8.
    Conserve les <style> de la page originale pour préserver la mise en forme des tableaux.
    Prend la DERNIÈRE occurrence d'Item 8 pour éviter de capturer la table des matières.
    """
    plain = _strip_tags_for_search(html)

    m8_list = list(ITEM8_RAW_RE.finditer(plain))
    if not m8_list:
        # Fallback : prendre les 400 Ko centraux du document
        mid   = len(plain) // 2
        start = max(0, mid - 200_000)
        end   = min(len(plain), mid + 200_000)
        m8_start, m8_end = start, end
    else:
        # Prendre la dernière occurrence (contenu réel) plutôt que la première (souvent TOC)
        m8   = m8_list[-1]
        m9   = ITEM9_RAW_RE.search(plain, m8.end() + 500)
        m8_start = max(0, m8.start() - 500)
        m8_end   = m9.start() if m9 else min(len(plain), m8.end() + 600_000)

    # Convertit les offsets texte → offsets HTML
    html_start = _find_tag_offset(html, m8_start)
    html_end   = _find_tag_offset(html, m8_end)
    fragment   = html[html_start:html_end]

    styles = _extract_styles(html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body  {{ font-family: Arial, sans-serif; font-size: 8.5pt;
          margin: 0; padding: 0; color: #000; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 4px; }}
  td, th {{ padding: 2px 5px; border: 1px solid #bbb; vertical-align: top; }}
  p     {{ margin: 2px 0; }}
</style>
{styles}
</head>
<body>
{fragment}
</body>
</html>"""


# ── Conversion Playwright ─────────────────────────────────────────────────────

def convert_file(htm_path: Path, pdf_path: Path) -> bool:
    from playwright.sync_api import sync_playwright

    try:
        raw_html = htm_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"ERREUR lecture : {e}")
        return False

    item8_html = extract_item8_html(raw_html)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page()
            page.set_viewport_size({"width": 1400, "height": 900})
            page.set_content(item8_html, wait_until="domcontentloaded", timeout=60_000)
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "10mm", "bottom": "10mm",
                        "left": "10mm", "right": "10mm"},
            )
            browser.close()
        return True
    except Exception as e:
        print(f"ERREUR playwright : {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main(limit: int = 50):
    print("=== convert_htm_to_pdf — Item 8 HTM → PDF (Chromium) ===\n")

    files = sorted(INPUT_DIR.glob("10k_*.htm"))
    if not files:
        print(f"Aucun fichier HTM dans {INPUT_DIR}/")
        return

    to_process = files[:limit]
    ok = errors = skipped = 0

    for i, htm_path in enumerate(to_process):
        pdf_path = OUTPUT_DIR / ("compte_resultat_" + htm_path.with_suffix(".pdf").name)
        label    = htm_path.name

        print(f"  [{i+1:2d}/{len(to_process)}] {label:<55} ", end="", flush=True)

        if pdf_path.exists() and pdf_path.stat().st_size > 1_000:
            print(f"déjà converti  ({pdf_path.stat().st_size // 1024} Ko)")
            skipped += 1
            continue

        t0      = time.time()
        success = convert_file(htm_path, pdf_path)
        elapsed = time.time() - t0

        if success and pdf_path.exists() and pdf_path.stat().st_size > 500:
            size_kb = pdf_path.stat().st_size // 1024
            print(f"OK  {size_kb:6d} Ko  ({elapsed:.1f}s)")
            ok += 1
        else:
            if pdf_path.exists():
                pdf_path.unlink()
            print(f"ECHEC ({elapsed:.1f}s)")
            errors += 1

    print(f"\n{'='*60}")
    print(f"  Convertis : {ok}  |  Déjà présents : {skipped}  |  Erreurs : {errors}")
    print(f"  Sortie    : {OUTPUT_DIR.resolve()}")
    print(f"{'='*60}")
    if ok + skipped > 0:
        print(f"\nLancez ensuite :")
        print(f"  .\\venv\\Scripts\\python.exe parse_financial_statements.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50,
                        help="Nombre max de fichiers à convertir (défaut: 50)")
    args = parser.parse_args()
    main(args.limit)
