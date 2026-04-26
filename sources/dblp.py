"""DBLP search backend.

Ported from paper_writing/fetch_bibtex.py:search_dblp +
paper_writing/verify_dblp.py:fetch_dblp. Search hits land in
`MatchCandidate.raw` so that `fetch_bibtex` can synthesize a BibTeX
entry locally without a second HTTP round-trip — matching the contract
of the crossref/openalex/arxiv backends.
"""

from __future__ import annotations

import json
import urllib.parse

from ..http import http_get
from ..models import MatchCandidate, PaperQuery
from ..normalize import title_search_variants, title_similarity, year_diff

DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"


def _hit_authors(info: dict) -> list[str]:
    a = info.get("authors", {})
    if not a:
        return []
    items = a.get("author", [])
    if isinstance(items, dict):
        items = [items]
    out = []
    for it in items:
        if isinstance(it, dict):
            name = it.get("text") or ""
        else:
            name = str(it)
        if name:
            out.append(name)
    return out


def _bib_type_for(info: dict) -> tuple[str, str]:
    """Map DBLP's `type` label to (bibtex_entry_type, venue_field_name).

    Defaults to inproceedings/booktitle for unknown types — matches the
    most common case in our workload.
    """
    t = info.get("type", "")
    if "Journal" in t:
        return "article", "journal"
    if "Book" in t or "Thes" in t:
        return "book", "publisher"
    if "Informal" in t:                # arXiv / preprints
        return "article", "journal"    # DBLP canonical: @article + journal=CoRR
    if "Reference" in t or "Editorship" in t:
        return "misc", "howpublished"
    return "inproceedings", "booktitle"


def _synthesize_bibtex(info: dict, dblp_id: str) -> str:
    """Build a BibTeX entry locally from a DBLP search-response info dict.

    Includes every field present in `info` that maps to a standard BibTeX
    slot (no filtering); supplements with the deterministic `biburl` and
    `bibsource` fields DBLP attaches to its canonical /rec/{key}.bib
    output. Fields the search response does not carry — long booktitle,
    publisher, timestamp — are not fabricated; eliminating them is the
    deliberate trade for skipping the second HTTP round-trip.
    """
    bib_type, venue_field = _bib_type_for(info)
    key = f"DBLP:{dblp_id}"

    fields: list[tuple[str, str]] = []

    authors = _hit_authors(info)
    if authors:
        fields.append(("author", " and\n                  ".join(authors)))

    title = info.get("title", "").rstrip(".")
    if title:
        fields.append(("title", title))

    venue = info.get("venue")
    if venue:
        fields.append((venue_field, venue))

    for src_key, bib_key in (
        ("volume", "volume"),
        ("number", "number"),
        ("pages", "pages"),
        ("year", "year"),
        ("doi", "doi"),
        ("ee", "url"),                 # electronic-edition URL
    ):
        v = info.get(src_key)
        if v:
            fields.append((bib_key, str(v)))

    # Deterministic bookkeeping that DBLP's canonical .bib also attaches.
    fields.append(("biburl", f"https://dblp.org/rec/{dblp_id}.bib"))
    fields.append(("bibsource",
                   "dblp computer science bibliography, https://dblp.org"))

    lines = [f"@{bib_type}{{{key},"]
    for i, (name, value) in enumerate(fields):
        sep = "," if i < len(fields) - 1 else ""
        lines.append(f"  {name:<12} = {{{value}}}{sep}")
    lines.append("}")
    return "\n".join(lines)


def _prefer_canonical_key(hits: list[dict], query_title: str,
                          threshold: float) -> str | None:
    """Prefer journals/conf entries over arXiv-only ones if both match."""
    for h in hits:
        info = h.get("info", {})
        key = info.get("key", "")
        if "journals/" in key or "conf/" in key:
            ht = info.get("title", "").rstrip(".")
            if title_similarity(query_title, ht) >= threshold:
                return key
    return None


class DBLPSource:
    name = "dblp"

    def search(self, query: PaperQuery, *, max_hits: int = 5,
               verbose: bool = False) -> list[MatchCandidate]:
        """Search DBLP, falling back to normalized title variants if the
        original returns zero hits.

        DBLP's tokenizer drops characters like '^' silently — a query
        containing them returns no match even if the paper is indexed.
        We try variants from `title_search_variants` in order and stop at
        the first that produces candidates. When a non-original variant
        wins, every emitted candidate gets a `warnings` entry so the user
        sees in the run log why the canonical title differs.
        """
        for vi, t_variant in enumerate(title_search_variants(query.title)):
            candidates = self._search_one_variant(t_variant, query,
                                                  max_hits=max_hits,
                                                  verbose=verbose)
            if candidates:
                if vi > 0:
                    msg = (f"matched DBLP via normalized title "
                           f"{t_variant!r} (original {query.title!r} "
                           f"returned no hits)")
                    print(f"  WARN dblp: {msg}")
                    for c in candidates:
                        c.warnings.append(msg)
                return candidates
        return []

    def _search_one_variant(self, t_query: str, query: PaperQuery, *,
                            max_hits: int, verbose: bool
                            ) -> list[MatchCandidate]:
        # Query strategies, most specific first
        queries = [t_query]
        if query.author:
            queries.append(f"{t_query} {query.author}")

        all_candidates: list[MatchCandidate] = []
        seen_keys: set[str] = set()

        for q in queries:
            params = urllib.parse.urlencode({"q": q, "format": "json", "h": str(max_hits)})
            url = f"{DBLP_SEARCH_URL}?{params}"
            try:
                data = http_get(url, verbose=verbose)
            except RuntimeError as e:
                print(f"  WARN DBLP search request failed for {q!r}: "
                      f"{type(e).__name__}: {e}")
                continue

            try:
                result = json.loads(data)
            except json.JSONDecodeError as e:
                print(f"  WARN DBLP returned non-JSON for {q!r}: "
                      f"{type(e).__name__}: {e}")
                continue
            hits = result.get("result", {}).get("hits", {}).get("hit", [])

            # Optionally upgrade arXiv-only top hit to a journal/conf version.
            # Always score against the *original* title so the threshold
            # logic stays calibrated, regardless of which variant we used.
            preferred = _prefer_canonical_key(hits, query.title, threshold=0.85)

            for h in hits:
                info = h.get("info", {})
                hkey = info.get("key", "")
                if not hkey or hkey in seen_keys:
                    continue
                seen_keys.add(hkey)

                hit_title = info.get("title", "").rstrip(".")
                score = title_similarity(query.title, hit_title)
                yd = year_diff(query.year, info.get("year", ""))
                if yd == 0:
                    score += 0.05
                if hkey == preferred:
                    score += 0.02

                all_candidates.append(MatchCandidate(
                    source=self.name,
                    score=score,
                    title=hit_title,
                    authors=_hit_authors(info),
                    year=str(info.get("year", "")),
                    canonical_key=f"DBLP:{hkey}",
                    raw=info,
                ))

        all_candidates.sort(key=lambda c: c.score, reverse=True)
        return all_candidates[:max_hits]

    def fetch_bibtex(self, candidate: MatchCandidate, *,
                     verbose: bool = False) -> str:
        """Synthesize BibTeX from the search-time `info` payload — no HTTP.

        Avoiding the canonical /rec/{key}.bib endpoint eliminates the
        most rate-limit-prone HTTP round-trip in the pipeline and brings
        DBLP into parity with the other backends, which all build bib
        locally from their cached search responses.
        """
        info = candidate.raw or {}
        if not info:
            raise RuntimeError(
                "DBLP candidate missing search-time `info` payload "
                "(was the candidate created by an older bibtools version?)"
            )
        dblp_id = candidate.canonical_key.removeprefix("DBLP:")
        return _synthesize_bibtex(info, dblp_id)
