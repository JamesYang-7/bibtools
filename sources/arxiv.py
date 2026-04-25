"""arXiv search backend.

API docs: https://info.arxiv.org/help/api/user-manual.html
Returns Atom XML; we parse it with the stdlib xml.etree.ElementTree.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from ..http import http_get
from ..models import MatchCandidate, PaperQuery
from ..normalize import title_similarity, year_diff

ARXIV_URL = "http://export.arxiv.org/api/query"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom",
           "arxiv": "http://arxiv.org/schemas/atom"}


def _entry_authors(entry: ET.Element) -> list[str]:
    out = []
    for a in entry.findall("a:author/a:name", ATOM_NS):
        if a.text:
            out.append(a.text.strip())
    return out


def _entry_year(entry: ET.Element) -> str:
    pub = entry.findtext("a:published", default="", namespaces=ATOM_NS)
    if pub and len(pub) >= 4:
        return pub[:4]
    return ""


def _entry_id(entry: ET.Element) -> str:
    """Extract the arXiv ID (e.g. '2406.12345') from the entry id URL."""
    raw = entry.findtext("a:id", default="", namespaces=ATOM_NS)
    # Format: http://arxiv.org/abs/2406.12345v1
    m = re.search(r"abs/([\w.\-]+?)(?:v\d+)?$", raw)
    return m.group(1) if m else raw


def _build_bibtex(entry: ET.Element, cite_key: str) -> str:
    fields: dict[str, str] = {}

    authors = _entry_authors(entry)
    if authors:
        norm = []
        for a in authors:
            parts = a.split()
            if len(parts) >= 2:
                norm.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            else:
                norm.append(a)
        fields["author"] = " and ".join(norm)

    title = entry.findtext("a:title", default="", namespaces=ATOM_NS).strip()
    title = re.sub(r"\s+", " ", title)
    if title:
        fields["title"] = "{" + title + "}"

    year = _entry_year(entry)
    if year:
        fields["year"] = year

    arxiv_id = _entry_id(entry)
    if arxiv_id:
        fields["eprint"] = arxiv_id
        fields["archivePrefix"] = "arXiv"

    primary = entry.find("arxiv:primary_category", ATOM_NS)
    if primary is not None:
        cat = primary.get("term", "")
        if cat:
            fields["primaryClass"] = cat

    lines = [f"@misc{{{cite_key},"]
    for k, v in fields.items():
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


class ArXivSource:
    name = "arxiv"

    def search(self, query: PaperQuery, *, max_hits: int = 5,
               verbose: bool = False) -> list[MatchCandidate]:
        # Build a structured search: title AND optional author surname
        terms = [f'ti:"{query.title}"']
        if query.author:
            from ..normalize import first_author_surname
            sn = first_author_surname(query.author)
            if sn:
                terms.append(f"au:{sn}")
        search_q = " AND ".join(terms)

        params = urllib.parse.urlencode({
            "search_query": search_q,
            "max_results": str(max_hits),
        })
        url = f"{ARXIV_URL}?{params}"
        try:
            data = http_get(url, verbose=verbose)
        except RuntimeError as e:
            print(f"  WARN arXiv search request failed for {query.title!r}: "
                  f"{type(e).__name__}: {e}")
            return []

        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            print(f"  WARN arXiv returned non-XML for {query.title!r}: "
                  f"{type(e).__name__}: {e}")
            return []

        candidates: list[MatchCandidate] = []
        for entry in root.findall("a:entry", ATOM_NS):
            title = entry.findtext("a:title", default="", namespaces=ATOM_NS).strip()
            title = re.sub(r"\s+", " ", title)
            if not title:
                continue

            score = title_similarity(query.title, title)
            yr = _entry_year(entry)
            yd = year_diff(query.year, yr)
            if yd == 0:
                score += 0.05
            elif yd is not None and yd > 3:
                score -= 0.10

            arxiv_id = _entry_id(entry)
            key = f"arxiv:{arxiv_id}" if arxiv_id else f"arxiv:{title[:30]}"

            # Stash the parsed XML element for later bibtex generation
            raw = {"_arxiv_xml": ET.tostring(entry, encoding="unicode"),
                   "id": arxiv_id, "title": title, "year": yr}
            candidates.append(MatchCandidate(
                source=self.name,
                score=score,
                title=title,
                authors=_entry_authors(entry),
                year=yr,
                canonical_key=key,
                raw=raw,
            ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max_hits]

    def fetch_bibtex(self, candidate: MatchCandidate, *,
                     verbose: bool = False) -> str:
        xml_str = candidate.raw.get("_arxiv_xml")
        if not xml_str:
            raise RuntimeError("arXiv candidate missing _arxiv_xml in raw payload")
        entry = ET.fromstring(xml_str)
        cite_key = candidate.canonical_key.replace(":", "_").replace(".", "_")
        return _build_bibtex(entry, cite_key)
