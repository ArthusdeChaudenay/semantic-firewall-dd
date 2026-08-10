"""verify_dois.py -- resolve each candidate DOI and show what it actually points to.

A title-similarity match against a search index is not proof: the top hit can be a blog
post about the paper, a later journal version, or a different paper with a similar
title. Resolving the DOI and printing the registered metadata is the check that matters.
"""
from __future__ import annotations

import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "semantic-firewall bibliography check (mailto:anon@example.org)"}

CANDIDATES = {
    "xie2024finben": "10.52202/079017-3033",
    "10.1145/3768292.3770387": "10.2139/ssrn.5447274",
    "wei2022chain": "10.52202/068431-1800",
    "wang2023selfconsistency": "10.59350/73qcj-wyt28",
    "ratner2017snorkel": "10.1007/s00778-019-00552-1",
    "sculley2015hidden": "10.7551/mitpress/12440.003.0011",
    "ribeiro2020beyond": "10.24963/ijcai.2021/659",
    "zhou2023docmath": "10.18653/v1/2024.acl-long.852",
    "kamoi2024when": "10.1162/tacl_a_00713",
    "dhuliawala2023chain": "10.18653/v1/2024.findings-acl.212",
    "dong2024fnspid": "10.1145/3637528.3671629",
    "es2024ragas": "10.18653/v1/2024.eacl-demo.16",
}

# Alternatives worth checking when the candidate looks wrong.
ALTERNATIVES = {
    "wang2023selfconsistency": ["10.48550/arXiv.2203.11171"],
    "ribeiro2020beyond": ["10.18653/v1/2020.acl-main.442"],
    "ratner2017snorkel": ["10.14778/3157794.3157797"],
    "sculley2015hidden": ["10.48550/arXiv.1908.09635"],
    "wei2022chain": ["10.48550/arXiv.2201.11903"],
    "xie2024finben": ["10.48550/arXiv.2402.12659"],
    "10.1145/3768292.3770387": ["10.1145/3768292.3770387"],
}


def resolve(doi: str):
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", headers=UA, timeout=25)
        if r.status_code != 200:
            return None
        m = r.json()["message"]
        return {
            "title": (m.get("title") or ["(none)"])[0],
            "type": m.get("type", "?"),
            "container": (m.get("container-title") or ["(none)"])[0],
            "year": (m.get("issued", {}).get("date-parts", [[None]])[0] or [None])[0],
        }
    except Exception as e:
        return {"title": f"error: {str(e)[:50]}", "type": "?", "container": "", "year": ""}


def show(key, doi, tag=""):
    info = resolve(doi)
    if info is None:
        print(f"    {tag}{doi:<40} NOT REGISTERED in Crossref")
        return
    print(f"    {tag}{doi:<40} [{info['type']}] {info['year']}")
    print(f"        title    : {info['title'][:88]}")
    print(f"        container: {info['container'][:88]}")


def main():
    for key, doi in CANDIDATES.items():
        print(f"\n{key}")
        show(key, doi, "candidate  ")
        for alt in ALTERNATIVES.get(key, []):
            if alt != doi:
                show(key, alt, "alternative")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
