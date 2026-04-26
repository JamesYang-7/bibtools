"""OpenAlex search backend.

API docs: https://docs.openalex.org/api-entities/works/search-works
No API key required; passing a mailto in the User-Agent gets the polite pool.
"""

from __future__ import annotations

import json
import urllib.parse

from ..http import http_get
from ..models import MatchCandidate, PaperQuery
from ..normalize import title_search_variants, title_similarity, year_diff

OPENALEX_URL = "https://api.openalex.org/works"


def _work_authors(work: dict) -> list[str]:
    out = []
    for a in work.get("authorships", []):
        name = (a.get("author") or {}).get("display_name")
        if name:
            out.append(name)
    return out


def _type_to_bibtex(work_type: str) -> str:
    return {
        "article": "article",
        "journal-article": "article",
        "proceedings-article": "inproceedings",
        "book": "book",
        "book-chapter": "incollection",
        "preprint": "misc",
    }.get(work_type, "misc")


def _build_bibtex(work: dict, cite_key: str) -> str:
    bib_type = _type_to_bibtex(work.get("type", ""))
    fields: dict[str, str] = {}

    authors = _work_authors(work)
    if authors:
        # Reformat "Given Family" -> "Family, Given" for BibTeX
        norm = []
        for a in authors:
            parts = a.split()
            if len(parts) >= 2:
                norm.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            else:
                norm.append(a)
        fields["author"] = " and ".join(norm)

    title = work.get("title", "")
    if title:
        fields["title"] = "{" + title + "}"

    year = work.get("publication_year", "")
    if year:
        fields["year"] = str(year)

    venue = (work.get("primary_location") or {}).get("source", {}) or {}
    venue_name = venue.get("display_name", "")
    if venue_name:
        fields["journal" if bib_type == "article" else "booktitle"] = venue_name

    biblio = work.get("biblio", {}) or {}
    if biblio.get("volume"):     fields["volume"] = str(biblio["volume"])
    if biblio.get("issue"):      fields["number"] = str(biblio["issue"])
    if biblio.get("first_page") and biblio.get("last_page"):
        fields["pages"] = f"{biblio['first_page']}--{biblio['last_page']}"

    doi = work.get("doi", "")
    if doi:
        fields["doi"] = doi.removeprefix("https://doi.org/")

    lines = [f"@{bib_type}{{{cite_key},"]
    for k, v in fields.items():
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


class OpenAlexSource:
    name = "openalex"

    def search(self, query: PaperQuery, *, max_hits: int = 5,
               verbose: bool = False) -> list[MatchCandidate]:
        for vi, t_variant in enumerate(title_search_variants(query.title)):
            candidates = self._search_one_variant(t_variant, query,
                                                  max_hits=max_hits,
                                                  verbose=verbose)
            if candidates:
                if vi > 0:
                    msg = (f"matched OpenAlex via normalized title "
                           f"{t_variant!r} (original {query.title!r} "
                           f"returned no hits)")
                    print(f"  WARN openalex: {msg}")
                    for c in candidates:
                        c.warnings.append(msg)
                return candidates
        return []

    def _search_one_variant(self, t_query: str, query: PaperQuery, *,
                            max_hits: int, verbose: bool
                            ) -> list[MatchCandidate]:
        params = urllib.parse.urlencode({
            "search": t_query,
            "per-page": str(max_hits),
        })
        url = f"{OPENALEX_URL}?{params}"
        try:
            data = http_get(url, verbose=verbose)
        except RuntimeError as e:
            print(f"  WARN OpenAlex search request failed for {t_query!r}: "
                  f"{type(e).__name__}: {e}")
            return []

        try:
            result = json.loads(data)
        except json.JSONDecodeError as e:
            print(f"  WARN OpenAlex returned non-JSON for {t_query!r}: "
                  f"{type(e).__name__}: {e}")
            return []

        works = result.get("results", [])
        candidates: list[MatchCandidate] = []
        for w in works:
            wt = w.get("title") or ""
            if not wt:
                continue
            # Score against the original (un-normalized) title.
            score = title_similarity(query.title, wt)
            wy = str(w.get("publication_year", ""))
            yd = year_diff(query.year, wy)
            if yd == 0:
                score += 0.05
            elif yd is not None and yd > 2:
                score -= 0.10

            oid = w.get("id", "")  # e.g. "https://openalex.org/W123"
            key = "openalex:" + oid.rsplit("/", 1)[-1] if oid else f"openalex:{wt[:30]}"
            candidates.append(MatchCandidate(
                source=self.name,
                score=score,
                title=wt,
                authors=_work_authors(w),
                year=wy,
                canonical_key=key,
                raw=w,
            ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max_hits]

    def fetch_bibtex(self, candidate: MatchCandidate, *,
                     verbose: bool = False) -> str:
        cite_key = candidate.canonical_key.replace(":", "_")
        return _build_bibtex(candidate.raw, cite_key)
