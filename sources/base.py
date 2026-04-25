"""SourceBackend protocol — what every search source must implement."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import MatchCandidate, PaperQuery


@runtime_checkable
class SourceBackend(Protocol):
    """A search backend (DBLP, CrossRef, OpenAlex, arXiv, ...).

    `name` is the short string used in source lists and reports.
    `search` returns a list of MatchCandidate sorted by score (best first).
    `fetch_bibtex` returns a BibTeX string for an accepted candidate.
    `fetch_makes_http`: True iff `fetch_bibtex` performs an HTTP round-trip.
        When False (the default), the backend synthesizes BibTeX locally
        from the search-time payload in `candidate.raw`. The cascade
        scheduler uses this to skip the inter-fetch `api_delay` sleep
        when no network call was actually issued.
    """

    name: str
    fetch_makes_http: bool = False

    def search(self, query: PaperQuery, *, max_hits: int = 5,
               verbose: bool = False) -> list[MatchCandidate]: ...

    def fetch_bibtex(self, candidate: MatchCandidate, *,
                     verbose: bool = False) -> str: ...


# Convenience re-export so consumers can write `from .sources.base import Hit`.
Hit = MatchCandidate
