"""build_anonymous_release.py -- assemble a double-blind snapshot of the artifact.

Anonymity for a submission is not just "remove the author line". Three things leak:
the files (names, employer, personal paths), the git history (author names and email
addresses on every commit), and the hosting account itself. This script handles the
first two by writing a fresh directory with no ``.git`` at all; the third is a hosting
decision described in the README it emits.

What ships is the paper's artifact and nothing else. The private repository also holds a
FastAPI service, French-language audit reports, and hand-written sample documents naming
the authors' employer, none of which the paper uses and all of which identify.

Retained for the record after acceptance. The camera-ready paper carries a real author
block by design, so this builder now reports it and stops rather than silently stripping
the names of an accepted paper; pass --allow-authors to build a snapshot anyway.

Run:
    python -m scripts.build_anonymous_release --out ../semantic-firewall-anon
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Everything the paper needs to be re-derived, and nothing more.
INCLUDE_DIRS = ["semantic_firewall", "scripts", "tests", "data", "paper"]
INCLUDE_FILES = ["README.md", "Makefile", "requirements.txt", "requirements-dev.txt",
                 "pyproject.toml", "conftest.py", ".gitignore"]

# Excluded even inside the directories above.
EXCLUDE_PATTERNS = [
    "**/__pycache__/**",
    "paper/example.tex",          # an unrelated third party's unpublished draft
    "paper/main.aux", "paper/main.log", "paper/main.out", "paper/main.blg",
    "data/xbrl_cache/**",         # 320 MB, refetchable, not needed to reproduce
    "**/*.pyc",
    # This builder holds the identifying strings as patterns, so shipping it would
    # defeat its own check. It is a private tool, not part of the artifact.
    "scripts/build_anonymous_release.py",
    "scripts/fetch_dois.py", "scripts/verify_dois.py",
]

# Identifying strings. Each maps to a neutral replacement; the check pass fails the
# build if any survives, so a new leak cannot slip through silently.
SCRUB = {
    r"valentin[.\s]?noel@devoteam\.com": "anonymous@example.org",
    r"arthus[.\s]?de[.\s]?chaudenay": "author",
    r"arthus\.dechaudenay@studbocconi\.it": "anonymous@example.org",
    r"Valentin\s+NO[EÉ]L": "Anonymous Author",
    r"Valentin\s+No[eë]l": "Anonymous Author",
    r"Arthus\s+de\s+Chaudenay": "Anonymous Author",
    r"[Dd]evoteam": "Anonymous Institution",
    r"ArthusdeChaudenay": "anonymous",
    r"[Cc]:\\Users\\valno": r"~",
    r"/c/Users/valno": "~",
}
FORBIDDEN = [r"devoteam", r"arthus", r"chaudenay", r"valentin", r"studbocconi",
             r"Users\\valno", r"Users/valno"]

TEXT_SUFFIXES = {".py", ".md", ".tex", ".bib", ".txt", ".json", ".jsonl", ".toml",
                 ".cfg", ".sty", ".bst", ".yml", ".yaml", ""}


ANON_NOTE = """
---

## About this anonymous release

This snapshot is prepared for double-blind review. It contains the paper's artifact
only, and carries no git history, since commit metadata records author names and
addresses on every commit.

Three parts of the working repository are deliberately absent, none of which the paper
uses: a FastAPI service and its connectors, internal audit reports written in another
language, and hand-written sample documents naming the authors' institution. Their
absence does not affect reproduction: every table and figure rebuilds from `data/` and
`scripts/` alone, and the test suite runs unchanged.

The snapshot is produced by a script that scrubs identifying strings and then fails the
build if any survives, so the release cannot silently regress.
"""

_OLD_PRODUCT = ("product/                    NOT part of the artifact: "
                "FastAPI service, connectors")
_NEW_PRODUCT = ("                            (a FastAPI service and connectors live in\n"
                "                             the working repository; not shipped here)")
_OLD_SAMPLES = ("Synthetic documents remaining under\n`samples/` are labelled as "
                "synthetic in their headers and none entered the 104-filing\ncorpus.")
_NEW_SAMPLES = ("Synthetic documents used during development are labelled as synthetic\n"
                "in their headers, are not shipped with this snapshot, and none entered\n"
                "the 104-filing corpus.")


def _patch_readme(dst) -> None:
    """Resolve references to directories this snapshot does not ship, and say why."""
    p = dst / "README.md"
    if not p.exists():
        return
    t = p.read_text(encoding="utf-8")
    t = t.replace(_OLD_PRODUCT, _NEW_PRODUCT).replace(_OLD_SAMPLES, _NEW_SAMPLES)
    if "About this anonymous release" not in t:
        t = t.rstrip() + "\n" + ANON_NOTE
    p.write_text(t, encoding="utf-8")


def excluded(rel: Path) -> bool:
    posix = str(rel).replace("\\", "/")
    for pat in EXCLUDE_PATTERNS:
        base = pat.rstrip("*").rstrip("/")
        if rel.match(pat) or posix == pat or posix.startswith(base + "/"):
            return True
    return False


def scrub_text(text: str) -> tuple[str, int]:
    n = 0
    for pat, rep in SCRUB.items():
        text, k = re.subn(pat, rep, text)
        n += k
    return text, n


def copy_tree(root: Path, sub: str, dst: Path) -> tuple[int, int, int]:
    """Copy one top-level directory. Paths are kept relative to the repository ROOT so
    that exclusion patterns such as ``data/xbrl_cache/**`` match as written; making them
    relative to the subdirectory silently copied a 320 MB cache."""
    files = scrubbed = skipped = 0
    for path in sorted((root / sub).rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if excluded(rel):
            skipped += 1
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(path, target)
                files += 1
                continue
            new, n = scrub_text(text)
            if n:
                scrubbed += 1
            target.write_text(new, encoding="utf-8")
        else:
            shutil.copy2(path, target)
        files += 1
    return files, scrubbed, skipped


def check(dst: Path) -> list[tuple[str, str]]:
    """Fail loudly on anything identifying that survived."""
    hits = []
    rx = re.compile("|".join(FORBIDDEN), re.IGNORECASE)
    for path in sorted(dst.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    hits.append((f"{path.relative_to(dst)}:{i}", line.strip()[:90]))
        except UnicodeDecodeError:
            continue
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../semantic-firewall-anon")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-authors", action="store_true",
                    help="tolerate the camera-ready author block in paper/main.tex")
    a = ap.parse_args()

    src, dst = Path("."), Path(a.out).resolve()
    if dst.exists():
        if not a.force:
            print(f"  {dst} exists; pass --force to replace it.")
            return
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    total = scrubbed = skipped = 0
    for d in INCLUDE_DIRS:
        if not (src / d).exists():
            print(f"  [skip] {d} not present")
            continue
        f, s, k = copy_tree(src, d, dst)
        total += f; scrubbed += s; skipped += k
        print(f"  {d:<20} {f:>5} files ({s} scrubbed, {k} excluded)")
    for f in INCLUDE_FILES:
        p = src / f
        if p.exists():
            text, n = scrub_text(p.read_text(encoding="utf-8"))
            (dst / f).write_text(text, encoding="utf-8")
            total += 1
            scrubbed += bool(n)

    _patch_readme(dst)

    print(f"\n  {total} files written, {scrubbed} scrubbed, {skipped} excluded")
    print(f"  no .git directory is created: the history carries author names on every commit")

    hits = check(dst)
    if a.allow_authors:
        hits = [h for h in hits if not h[0].startswith("paper" + os.sep + "main.tex")
                and not h[0].startswith("paper/main.tex")]
    if hits:
        print(f"\n  FAILED: {len(hits)} identifying string(s) survived")
        for where, line in hits[:20]:
            print(f"    {where}  {line}")
        sys.exit(1)
    print("  check passed: no identifying string found in the snapshot")
    print(f"\n  -> {dst}")


if __name__ == "__main__":
    main()
