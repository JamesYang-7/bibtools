"""bibtools — reusable BibTeX search/verify across DBLP, CrossRef, OpenAlex, arXiv.

Public API:
    PaperQuery      — input record (title, author, year, ...)
    MatchResult     — per-query result (status, source, candidate, mismatches)
    SearchReport    — aggregate result with summary() method
    search_papers() — main entry point

Example:
    from bibtools import search_papers, PaperQuery
    queries = [PaperQuery(id="x", title="...", author="...", year="2020")]
    report = search_papers(queries, out_bib="refs.bib", out_json="results.json")
    report.summary()
"""

from .models import PaperQuery, MatchCandidate, MatchResult, SearchReport
from .search import search_papers, classify

__all__ = [
    "PaperQuery",
    "MatchCandidate",
    "MatchResult",
    "SearchReport",
    "search_papers",
    "classify",
]
