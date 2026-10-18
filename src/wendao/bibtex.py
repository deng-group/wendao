"""BibTeX -> a publication graph in the "wendao-graph/1" format, for the graph embed (`wendao bibtex`).

Themes come from each paper's `keywords`, people from co-authors with enough joint papers, and papers keep their
title (with <sub>/<sup>), authors, venue, year, and DOI link. A small reader, so no extra package is needed: it
handles the usual {...} and "..." fields, @string abbreviations are left as they are, and @comment/@preamble are
skipped.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ACCENTS = {
    '"': {"a": "ä", "o": "ö", "u": "ü", "e": "ë", "i": "ï", "A": "Ä", "O": "Ö", "U": "Ü"},
    "'": {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "y": "ý", "c": "ć", "n": "ń", "s": "ś", "z": "ź",
          "E": "É", "A": "Á", "O": "Ó"},
    "`": {"a": "à", "e": "è", "i": "ì", "o": "ò", "u": "ù"},
    "^": {"a": "â", "e": "ê", "i": "î", "o": "ô", "u": "û"},
    "~": {"a": "ã", "n": "ñ", "o": "õ"},
    "c": {"c": "ç", "C": "Ç"},
    "v": {"c": "č", "s": "š", "z": "ž", "r": "ř", "e": "ě", "C": "Č", "S": "Š", "Z": "Ž"},
}


class BibtexError(ValueError):
    pass


def parse(text: str) -> list[dict]:
    """The entries of a BibTeX file: [{"type": "article", "key": "...", "fields": {"title": "...", ...}}]."""
    entries = []
    at = 0
    while True:
        match = re.compile(r"@\s*(\w+)\s*([{(])").search(text, at)
        if not match:
            return entries
        kind, opening = match.group(1).lower(), match.group(2)
        closing = "}" if opening == "{" else ")"
        body_start = match.end()
        depth, i = 1, body_start
        while i < len(text) and depth:
            if text[i] == opening or (opening == "{" and text[i] == "{"):
                depth += 1
            elif text[i] == closing or (opening == "{" and text[i] == "}"):
                depth -= 1
            i += 1
        body = text[body_start:i - 1]
        at = i
        if kind in {"comment", "preamble", "string"}:
            continue
        key, _, rest = body.partition(",")
        entries.append({"type": kind, "key": key.strip(), "fields": fields(rest)})


def fields(text: str) -> dict[str, str]:
    result = {}
    i = 0
    while i < len(text):
        match = re.compile(r"\s*([\w-]+)\s*=\s*").match(text, i)
        if not match:
            i += 1
            continue
        name, i = match.group(1).lower(), match.end()
        value, i = read_value(text, i)
        result[name] = value
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
    return result


def read_value(text: str, i: int) -> tuple[str, int]:
    parts = []
    while i < len(text):
        char = text[i]
        if char == "{":
            depth, j = 1, i + 1
            while j < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            parts.append(text[i + 1:j - 1])
            i = j
        elif char == '"':
            j = i + 1
            depth = 0
            while j < len(text) and not (text[j] == '"' and depth == 0):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            parts.append(text[i + 1:j])
            i = j + 1
        else:
            match = re.compile(r"[^,#}\s]+").match(text, i)
            if match:
                parts.append(match.group(0))
                i = match.end()
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i < len(text) and text[i] == "#":  # "a" # "b"
            i += 1
            while i < len(text) and text[i] in " \t\r\n":
                i += 1
            continue
        return "".join(parts), i
    return "".join(parts), i


def clean(value: str) -> str:
    """BibTeX text as plain text: accents, no braces, single spaces."""
    value = re.sub(r"\\([\"'`^~cv])\s*\{?\s*(\w)\s*\}?", lambda m: ACCENTS.get(m.group(1), {}).get(m.group(2), m.group(2)), value)
    value = value.replace("\\&", "&").replace("--", "–").replace("~", " ")
    return " ".join(value.replace("{", "").replace("}", "").split())


def title_html(value: str) -> str:
    """The title for a card: keeps <sub>/<sup> (formulae), drops other tags."""
    return re.sub(r"<(?!/?(sub|sup)>)[^>]*>", "", clean(value))


def plain(value: str) -> str:
    return re.sub(r"<[^>]*>", "", title_html(value))


def person(name: str) -> str:
    """'Deng, Zeyu' -> 'Zeyu Deng'; marks such as * (corresponding author) or # are dropped."""
    name = clean(name).replace("*", "").replace("#", "").strip()
    last, comma, first = name.partition(",")
    return f"{first.strip()} {last.strip()}" if comma else name


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def doi_link(entry: dict) -> str:
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean(entry.get("doi", "")), flags=re.I)
    if doi:
        return f"https://doi.org/{doi}"
    url = clean(entry.get("url", ""))
    return url if url.startswith(("http://", "https://")) else ""


def build(entries: list[dict], themes: dict[str, str | None] | None = None, me: str = "", min_shared: int = 3,
          max_authors: int = 6) -> dict:
    """The publication graph: themes (from keywords), papers, and frequent collaborators.

    `themes` maps a keyword (any case) to a theme name, or to None to leave that keyword out; other keywords
    become themes named after themselves. `me` is the page owner, left out of the collaborators. People need
    `min_shared` joint papers to appear.
    """
    names = {key.lower(): value for key, value in (themes or {}).items()}
    me = person(me).lower() if me.strip() else ""  # "Deng, Zeyu" and "Zeyu Deng" both work
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    papers = []
    for entry in entries:
        f = entry["fields"]
        if not f.get("title"):
            continue
        paper_id = f"paper:{entry['key']}"
        authors = [person(a) for a in re.split(r"\s+and\s+", clean(f.get("author", ""))) if a.strip()]
        paper_themes = []
        for keyword in (k.strip() for k in clean(f.get("keywords", "")).split(",")):
            if not keyword:
                continue
            label = names[keyword.lower()] if keyword.lower() in names else " ".join(w[:1].upper() + w[1:] for w in keyword.split())
            if label and f"theme:{slug(label)}" not in paper_themes:
                paper_themes.append(f"theme:{slug(label)}")
                nodes.setdefault(f"theme:{slug(label)}", {"id": f"theme:{slug(label)}", "type": "theme", "label": label})
        year = clean(f.get("year", ""))
        nodes[paper_id] = {
            "id": paper_id, "type": "paper", "label": plain(f["title"]), "title": title_html(f["title"]),
            "year": int(year) if year.isdigit() else year,
            "venue": clean(f.get("journal") or f.get("booktitle") or f.get("school") or f.get("publisher") or ""),
            "authors": ", ".join(authors[:max_authors]) + (", et al." if len(authors) > max_authors else ""),
            "url": doi_link(f),
        }
        edges += [{"source": paper_id, "target": theme} for theme in paper_themes]
        papers.append((paper_id, authors))

    shared = Counter(a for _, authors in papers for a in set(authors) if a.lower() != me)
    for paper_id, authors in papers:
        for name in dict.fromkeys(authors):
            if name.lower() == me or shared[name] < min_shared:
                continue
            node_id = f"person:{slug(name)}"
            nodes.setdefault(node_id, {"id": node_id, "type": "person", "label": name, "papers": shared[name]})
            edges.append({"source": paper_id, "target": node_id})

    linked = {end for edge in edges for end in (edge["source"], edge["target"])}
    return {
        "format": "wendao-graph/1",
        "types": {
            "theme": {"label": "Themes", "kind": "Research theme", "hub": True, "labels": "always", "color": 1,  # blue
                      "card": {"list": "paper", "sort": "-year", "detail": "year"}},
            "paper": {"label": "Papers", "kind": "Paper", "labels": "focus", "color": 2,  # grey
                      "card": {"fields": ["authors", "venue, year"], "tags": "theme"}},
            "person": {"label": "Collaborators", "kind": "Collaborator", "labels": "zoom", "color": 3,  # amber
                       "card": {"list": "paper", "sort": "-year", "detail": "year"}},
        },
        "nodes": [node for node in nodes.values() if node["id"] in linked],
        "edges": edges,
    }


def from_file(path: Path, **options) -> dict:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    entries = parse(text)
    if not entries:
        raise BibtexError(f"No BibTeX entries found in {path}.")
    return build(entries, **options)
