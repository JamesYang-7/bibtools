"""CrossRef search backend.

Ported from paper_writing/fetch_bibtex.py:search_crossref + crossref_to_bibtex.
"""

from __future__ import annotations

import json
import urllib.parse

from ..http import http_get
from ..models import MatchCandidate, PaperQuery
from ..normalize import title_similarity, year_diff

CROSSREF_URL = "https://api.crossref.org/works"


def _item_year(item: dict) -> str:
    pub = item.get("published") or item.get("published-print") or item.get("created") or {}
    parts = pub.get("date-parts", [[]])
    if parts and parts[0]:
        return str(parts[0][0])
    return ""


def _item_authors(item: dict) -> list[str]:
    out = []
    for a in item.get("author", []):
        given = a.get("given", "")
        family = a.get("family", "")
        if family and given:
            out.append(f"{given} {family}")
        elif family:
            out.append(family)
    return out


def _crossref_to_bibtex(item: dict, cite_key: str) -> str:
    type_map = {
        "journal-article": "article",
        "proceedings-article": "inproceedings",
        "book": "book",
        "book-chapter": "incollection",
        "monograph": "book",
    }
    bib_type = type_map.get(item.get("type", ""), "article")

    fields: dict[str, str] = {}
    authors = item.get("author", [])
    if authors:
        author_strs = []
        for a in authors:
            given = a.get("given", "")
            family = a.get("family", "")
            if given and family:
                author_strs.append(f"{family}, {given}")
            elif family:
                author_strs.append(family)
        if author_strs:
            fields["author"] = " and ".join(author_strs)

    titles = item.get("title", [])
    if titles:
        fields["title"] = "{" + titles[0] + "}"

    year = _item_year(item)
    if year:
        fields["year"] = year

    container = item.get("container-title", [])
    if container:
        fields["journal" if bib_type == "article" else "booktitle"] = container[0]

    if item.get("volume"): fields["volume"] = item["volume"]
    if item.get("issue"):  fields["number"] = item["issue"]
    if item.get("page"):   fields["pages"] = item["page"].replace("-", "--")
    if item.get("DOI"):    fields["doi"] = item["DOI"]
    if item.get("publisher"): fields["publisher"] = item["publisher"]

    lines = [f"@{bib_type}{{{cite_key},"]
    for k, v in fields.items():
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


class CrossRefSource:
    name = "crossref"

    def search(self, query: PaperQuery, *, max_hits: int = 5,
               verbose: bool = False) -> list[MatchCandidate]:
        q = query.title + (f" {query.author}" if query.author else "")
        params = urllib.parse.urlencode({
            "query.bibliographic": q,
            "rows": str(max_hits),
        })
        url = f"{CROSSREF_URL}?{params}"
        try:
            data = http_get(url, verbose=verbose)
        except RuntimeError as e:
            print(f"  WARN CrossRef search request failed for {query.title!r}: "
                  f"{type(e).__name__}: {e}")
            return []

        try:
            result = json.loads(data)
        except json.JSONDecodeError as e:
            print(f"  WARN CrossRef returned non-JSON for {query.title!r}: "
                  f"{type(e).__name__}: {e}")
            return []

        items = result.get("message", {}).get("items", [])
        candidates: list[MatchCandidate] = []
        for item in items:
            titles = item.get("title", [])
            if not titles:
                continue
            it_title = titles[0]
            score = title_similarity(query.title, it_title)
            it_year = _item_year(item)
            yd = year_diff(query.year, it_year)
            if yd == 0:
                score += 0.05
            elif yd is not None and yd > 2:
                # CrossRef can hallucinate cross-decade matches, penalize
                score -= 0.10

            doi = item.get("DOI", "")
            key = f"doi:{doi}" if doi else f"crossref:{titles[0][:30]}"
            candidates.append(MatchCandidate(
                source=self.name,
                score=score,
                title=it_title,
                authors=_item_authors(item),
                year=it_year,
                canonical_key=key,
                raw=item,
            ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max_hits]

    def fetch_bibtex(self, candidate: MatchCandidate, *,
                     verbose: bool = False) -> str:
        # Build BibTeX locally; CrossRef's content negotiation API is flaky.
        cite_key = candidate.raw.get("DOI") or candidate.canonical_key
        cite_key = cite_key.replace("/", "_").replace(".", "_")
        return _crossref_to_bibtex(candidate.raw, cite_key)
