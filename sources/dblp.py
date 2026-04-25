"""DBLP search backend.

Ported from paper_writing/fetch_bibtex.py:search_dblp +
paper_writing/verify_dblp.py:fetch_dblp.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from ..http import http_get
from ..models import MatchCandidate, PaperQuery
from ..normalize import title_similarity, year_diff

DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"
DBLP_BIB_URL = "https://dblp.org/rec/{key}.bib"


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


def _strip_dblp_fields(bibtex: str, drop_metadata: bool = False) -> str:
    """Optionally remove DBLP-internal metadata (timestamp, biburl, bibsource).

    Default is to keep every field DBLP returns. Pass drop_metadata=True to
    strip the bookkeeping fields (matches the pre-1.1 behavior).
    """
    if not drop_metadata:
        return bibtex
    skip_prefixes = ("timestamp", "biburl", "bibsource")
    return "\n".join(
        line for line in bibtex.split("\n")
        if not line.strip().lower().startswith(skip_prefixes)
    )


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
        # Query strategies, most specific first
        queries = [query.title]
        if query.author:
            queries.append(f"{query.title} {query.author}")

        all_candidates: list[MatchCandidate] = []
        seen_keys: set[str] = set()

        for q in queries:
            params = urllib.parse.urlencode({"q": q, "format": "json", "h": str(max_hits)})
            url = f"{DBLP_SEARCH_URL}?{params}"
            try:
                data = http_get(url, verbose=verbose)
            except RuntimeError as e:
                if verbose:
                    print(f"  DBLP request failed: {e}")
                continue

            try:
                result = json.loads(data)
            except json.JSONDecodeError:
                continue
            hits = result.get("result", {}).get("hits", {}).get("hit", [])

            # Optionally upgrade arXiv-only top hit to a journal/conf version
            preferred = _prefer_canonical_key(hits, query.title, threshold=0.85)

            for h in hits:
                info = h.get("info", {})
                hkey = info.get("key", "")
                if not hkey or hkey in seen_keys:
                    continue
                # If we have a preferred journal/conf key, only emit that one
                # at top score (others stay as alternates)
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
        dblp_id = candidate.canonical_key.removeprefix("DBLP:")
        bib = http_get(DBLP_BIB_URL.format(key=dblp_id), verbose=verbose)
        bib = _strip_dblp_fields(bib)
        # Re-key in case DBLP returns a different cite key than the search did
        # (rare, but happens for crossref-style entries)
        m = re.search(r'@\w+\{([^,]+),', bib)
        if m and m.group(1) != f"DBLP:{dblp_id}":
            bib = bib.replace(m.group(1), f"DBLP:{dblp_id}", 1)
        return bib.strip()
