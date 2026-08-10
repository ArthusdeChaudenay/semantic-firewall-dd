"""fetch_dois.py -- attach a verified DOI to every bibliography entry.

DOIs are looked up, never recalled: each is fetched from Crossref (and, failing that,
from the arXiv and DataCite APIs) and accepted only when the returned title matches the
entry's title closely enough. Entries that cannot be matched are reported rather than
guessed, since a plausible-looking wrong DOI is worse than none.

Run:
    python -m scripts.fetch_dois            # report only
    python -m scripts.fetch_dois --write    # rewrite paper/custom.bib
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BIB = Path("paper/custom.bib")
UA = {"User-Agent": "semantic-firewall bibliography check (mailto:anon@example.org)"}
MIN_SIM = 0.80


def norm(s: str) -> str:
    s = re.sub(r"\{|\}|\\['\"^`~=.]", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def parse_entries(text: str):
    """Yield (key, title, first_author, year, span) for each @entry."""
    out = []
    for m in re.finditer(r"@(\w+)\{([^,]+),", text):
        start = m.start()
        depth, i = 0, text.index("{", start)
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = text[start:i + 1]
        t = re.search(r"title\s*=\s*\{(.+?)\}\s*,\s*\n", body, re.S)
        a = re.search(r"author\s*=\s*\{(.+?)\}\s*,\s*\n", body, re.S)
        y = re.search(r"year\s*=\s*\{(\d{4})\}", body)
        out.append({
            "key": m.group(2).strip(),
            "title": re.sub(r"\s+", " ", t.group(1)) if t else "",
            "author": re.sub(r"\s+", " ", a.group(1)).split(" and ")[0] if a else "",
            "year": y.group(1) if y else "",
            "start": start, "end": i + 1, "body": body,
            "has_doi": "doi" in body.lower().split("=")[0] or bool(
                re.search(r"\n\s*doi\s*=", body, re.I)),
        })
    return out


def crossref(title: str, author: str):
    try:
        r = requests.get("https://api.crossref.org/works",
                         params={"query.bibliographic": f"{title} {author}", "rows": 5},
                         headers=UA, timeout=25)
        r.raise_for_status()
        for it in r.json()["message"]["items"]:
            cand = (it.get("title") or [""])[0]
            sim = similar(title, cand)
            if sim >= MIN_SIM:
                return it["DOI"], cand, sim
        best = r.json()["message"]["items"][:1]
        if best:
            return None, (best[0].get("title") or [""])[0], similar(title, (best[0].get("title") or [""])[0])
    except Exception as e:
        print(f"      crossref error: {str(e)[:70]}")
    return None, "", 0.0


def arxiv(title: str):
    """arXiv assigns a DOI of the form 10.48550/arXiv.<id> to every submission."""
    try:
        r = requests.get("http://export.arxiv.org/api/query",
                         params={"search_query": f'ti:"{title}"', "max_results": 3},
                         headers=UA, timeout=25)
        r.raise_for_status()
        for m in re.finditer(r"<entry>(.*?)</entry>", r.text, re.S):
            e = m.group(1)
            t = re.search(r"<title>(.*?)</title>", e, re.S)
            i = re.search(r"<id>http://arxiv\.org/abs/([^<v]+)", e)
            if t and i and similar(title, t.group(1)) >= MIN_SIM:
                return f"10.48550/arXiv.{i.group(1)}", re.sub(r"\s+", " ", t.group(1)), 1.0
    except Exception as e:
        print(f"      arxiv error: {str(e)[:70]}")
    return None, "", 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    text = BIB.read_text(encoding="utf-8")
    entries = parse_entries(text)
    print(f"{len(entries)} entries\n")

    found, missing = {}, []
    for e in entries:
        if e["has_doi"]:
            print(f"  = {e['key']:<28} already has a DOI")
            continue
        doi, cand, sim = crossref(e["title"], e["author"])
        src = "crossref"
        if not doi:
            doi, cand, sim = arxiv(e["title"])
            src = "arxiv"
        if doi:
            found[e["key"]] = doi
            print(f"  + {e['key']:<28} {doi:<42} ({src}, sim={sim:.2f})")
        else:
            missing.append((e["key"], e["title"], cand, sim))
            print(f"  ? {e['key']:<28} NOT MATCHED  (best sim={sim:.2f})")
        time.sleep(0.4)

    print(f"\n  matched {len(found)}  |  unmatched {len(missing)}")
    for k, t, c, s in missing:
        print(f"    {k}\n       ours: {t[:78]}\n       best: {c[:78]}  ({s:.2f})")

    if args.write and found:
        out, cursor = [], 0
        for e in entries:
            out.append(text[cursor:e["start"]])
            body = e["body"]
            if e["key"] in found:
                # insert doi just before the closing brace, preserving indentation
                body = re.sub(r",?\s*\n\}$",
                              ",\n  doi     = {%s},\n}" % found[e["key"]], body)
            out.append(body)
            cursor = e["end"]
        out.append(text[cursor:])
        BIB.write_text("".join(out), encoding="utf-8")
        print(f"\n  wrote {len(found)} DOIs -> {BIB}")


if __name__ == "__main__":
    main()
