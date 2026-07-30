"""
render_report_pdf.py — Render a Markdown report to a paginated A4 PDF.

Primary path: headless Edge/Chrome (`--print-to-pdf`) for full UTF-8 fidelity
(accents, em-dashes, italics). Fallback: PyMuPDF `fitz.Story` with ASCII-safe
sanitisation if no browser is available. Dependency-free beyond PyMuPDF.

Supported Markdown subset: #/##/### headings, **bold**, *italic*, `inline code`,
fenced ``` code blocks, - and 1. lists, | tables |, --- rules, [text](link), paragraphs.

Usage:
    python scripts/render_report_pdf.py <input.md> <output.pdf> ["Document title"]
"""

from __future__ import annotations

import html as _html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ── Minimal Markdown → HTML ──────────────────────────────────────────────────
def _inline(text: str) -> str:
    text = _html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [t](u) -> t
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)          # bold first
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)  # then italic
    return text


def md_to_html_body(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    list_open: str | None = None

    def close_list():
        nonlocal list_open
        if list_open:
            out.append(f"</{list_open}>")
            list_open = None

    while i < n:
        line = lines[i]

        if line.strip().startswith("```"):
            close_list(); i += 1; buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(_html.escape(lines[i])); i += 1
            i += 1
            out.append("<pre>" + "\n".join(buf) + "</pre>")
            continue

        if not line.strip():
            close_list(); i += 1; continue

        if re.match(r"^#{1,6}\s", line):
            close_list()
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
            i += 1; continue

        if re.match(r"^(-{3,}|\*{3,})\s*$", line):
            close_list(); out.append("<hr/>"); i += 1; continue

        if line.lstrip().startswith("|") and "|" in line[1:]:
            close_list(); tbl = []
            while i < n and lines[i].lstrip().startswith("|"):
                tbl.append(lines[i]); i += 1
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in tbl]
            rows = [r for r in rows if not all(re.match(r"^:?-+:?$", c or "-") for c in r)]
            out.append("<table>")
            for r_idx, row in enumerate(rows):
                tag = "th" if r_idx == 0 else "td"
                out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in row) + "</tr>")
            out.append("</table>")
            continue

        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            if list_open != "ul":
                close_list(); out.append("<ul>"); list_open = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>"); i += 1; continue

        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            if list_open != "ol":
                close_list(); out.append("<ol>"); list_open = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>"); i += 1; continue

        close_list()
        para = [line]; i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|\s*[-*]\s|\s*\d+\.\s|\||```|-{3,})", lines[i]):
            para.append(lines[i]); i += 1
        out.append("<p>" + _inline(" ".join(s.strip() for s in para)) + "</p>")

    close_list()
    return "\n".join(out)


CSS = """
@page { size: A4; margin: 16mm 15mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; color: #1a1a1a; font-size: 10pt; line-height: 1.45; }
h1 { font-size: 19pt; color: #7a1f1f; margin: 0 0 2pt 0; }
h2 { font-size: 13.5pt; color: #1f3a5f; border-bottom: 1px solid #cfd6dd; padding-bottom: 3px; margin: 18pt 0 6pt 0; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #1f3a5f; margin: 12pt 0 4pt 0; page-break-after: avoid; }
p, li { font-size: 10pt; }
code { font-family: 'Consolas', monospace; font-size: 9pt; background: #f2f3f5; color: #7a1f1f; padding: 0 2px; border-radius: 2px; }
pre { font-family: 'Consolas', monospace; font-size: 8.5pt; background: #f6f7f9; color: #24292e; padding: 8px 10px; border-radius: 4px; border: 1px solid #e3e6ea; white-space: pre-wrap; page-break-inside: avoid; }
table { width: 100%; border-collapse: collapse; margin: 8pt 0; page-break-inside: avoid; }
th { font-size: 8.8pt; background: #1f3a5f; color: #fff; text-align: left; padding: 5px 6px; }
td { font-size: 8.8pt; border-top: 1px solid #e3e6ea; padding: 5px 6px; vertical-align: top; }
tr:nth-child(even) td { background: #f7f9fb; }
hr { border: 0; border-top: 1px solid #e3e6ea; margin: 12pt 0; }
b { color: #111; }
"""


def _build_html(md_path: Path, title: str | None) -> str:
    body = md_to_html_body(md_path.read_text(encoding="utf-8"))
    ttl = _html.escape(title or md_path.stem)
    return (f"<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
            f"<title>{ttl}</title><style>{CSS}</style></head><body>{body}</body></html>")


def _find_browsers() -> list[str]:
    """Candidate Chromium browsers, Chrome first (most reliable for print-to-pdf)."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    found = [p for p in candidates if Path(p).exists()]
    for name in ("chrome", "msedge"):
        w = shutil.which(name)
        if w and w not in found:
            found.append(w)
    return found


def _render_browser(html: str, pdf_path: Path) -> bool:
    tmp = Path(tempfile.gettempdir()) / f"_sf_report_{os.getpid()}.html"
    tmp.write_text(html, encoding="utf-8")
    uri = "file:///" + str(tmp.resolve()).replace("\\", "/")
    profile = Path(tempfile.mkdtemp(prefix="_sf_chrome_"))
    try:
        for browser in _find_browsers():
            if pdf_path.exists():
                pdf_path.unlink()
            try:
                subprocess.run(
                    [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
                     "--no-first-run", "--no-default-browser-check",
                     f"--user-data-dir={profile}", "--no-pdf-header-footer",
                     f"--print-to-pdf={pdf_path.resolve()}", uri],
                    check=True, capture_output=True, timeout=120,
                )
            except Exception as e:
                print(f"  {Path(browser).name} failed: {e}")
                continue
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return True
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
        shutil.rmtree(profile, ignore_errors=True)


def _render_fitz(html: str, pdf_path: Path) -> None:
    import fitz
    # base-14 fonts lack some typography; substitute ASCII-safe equivalents.
    for a, b in [("\u2014", " - "), ("\u2013", "-"), ("\u2019", "'"),
                 ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
                 ("\u2026", "..."), ("\u2192", "->"), ("\u2248", "~"),
                 ("\u2264", "<="), ("\u2260", "!="), ("\u2228", " OR ")]:
        html = html.replace(a, b)
    story = fitz.Story(html=html)
    writer = fitz.DocumentWriter(str(pdf_path))
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (48, 40, -48, -40)
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()


def render(md_path: Path, pdf_path: Path, title: str | None = None) -> None:
    html = _build_html(md_path, title)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    ok = _render_browser(html, pdf_path)
    engine = "browser" if ok else "fitz"
    if not ok:
        _render_fitz(html, pdf_path)
    print(f"PDF written ({engine}) -> {pdf_path}  ({pdf_path.stat().st_size:,} bytes)")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: render_report_pdf.py <input.md> <output.pdf> [title]")
    render(Path(sys.argv[1]), Path(sys.argv[2]),
           sys.argv[3] if len(sys.argv) > 3 else None)


if __name__ == "__main__":
    main()
