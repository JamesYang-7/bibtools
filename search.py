"""Search orchestrator: cascade across sources, classify each query."""

from __future__ import annotations

import time
from pathlib import Path

from .bibtex import write_bib_file
from .interactive import interactive_review
from .models import MatchCandidate, MatchResult, PaperQuery, SearchReport
from .normalize import (author_match, normalize_for_comparison,
                        title_similarity, year_diff)
from .sources import DEFAULT_ORDER, SOURCES

# Tunable thresholds — overridable via search_papers() kwargs
EXACT_TITLE_THRESHOLD = 0.95
FUZZY_TITLE_THRESHOLD = 0.75
ACCEPTABLE_YEAR_DIFF = 2          # |query_year - hit_year| considered "close"

# Title-evidence override: when a candidate's title sim with the query is
# this high, the title alone is treated as decisive. If the title resolves
# to a single paper across all collected candidates, the chosen candidate
# is promoted from fuzzy to exact regardless of author/year mismatch.
# When multiple distinct papers share the title, year diff <= 1 AND
# first-author match against the query are required to disambiguate.
TITLE_EVIDENCE_THRESHOLD = 0.95
TITLE_EVIDENCE_DISAMBIG_YEAR_DIFF = 1


def classify(query: PaperQuery, candidate: MatchCandidate, *,
             exact_threshold: float = EXACT_TITLE_THRESHOLD,
             fuzzy_threshold: float = FUZZY_TITLE_THRESHOLD,
             year_tolerance: int = ACCEPTABLE_YEAR_DIFF,
             ) -> tuple[str, list[str]]:
    """Classify a (query, candidate) pair.

    Returns (status, mismatches). status is one of: "exact", "fuzzy", "weak".
    "weak" means below fuzzy threshold — caller should treat as no match.
    """
    sim = title_similarity(query.title, candidate.title)
    mismatches: list[str] = []

    if sim < fuzzy_threshold:
        return "weak", [f"title similarity {sim:.2f} < {fuzzy_threshold}"]

    # Below exact title threshold -> always fuzzy
    if sim < exact_threshold:
        mismatches.append(f"title similarity {sim:.2f}")

    # Year check. When title is an exact match and year is off by <= 1,
    # treat as the arXiv-preprint-vs-conf/journal pattern (same paper,
    # different publication stage) and don't downgrade. Otherwise an
    # arXiv hit with a year matching the user's input would beat the
    # canonical conf/journal record published the year after.
    if query.year and candidate.year:
        yd = year_diff(query.year, candidate.year)
        if yd is None:
            mismatches.append(f"year unparseable: query={query.year} hit={candidate.year}")
        elif yd > 0:
            tolerable = sim >= exact_threshold and yd <= 1
            if not tolerable:
                tag = " (close)" if yd <= year_tolerance else " (far)"
                mismatches.append(f"year: {query.year} -> {candidate.year}{tag}")

    # Author check
    if query.author and candidate.authors:
        if not author_match(query.author, candidate.authors):
            mismatches.append(
                f"author mismatch: query={query.author!r} "
                f"hit={candidate.authors[:2]}"
            )

    if mismatches:
        return "fuzzy", mismatches
    return "exact", []


def _maybe_promote_via_title_evidence(
    query: PaperQuery,
    best: MatchCandidate,
    all_candidates: list[MatchCandidate],
) -> bool:
    """Decide whether `best` (currently fuzzy) deserves promotion to exact.

    Rule:
      - Collect every candidate whose title sim with the query is
        >= TITLE_EVIDENCE_THRESHOLD.
      - Group those candidates by (normalized_title, year). One group
        means all hits are records of the same paper (different sources,
        arXiv/conf duplicates, etc.) — title is uniquely decisive, accept.
      - Multiple groups means distinct papers share this title: require
        year_diff <= TITLE_EVIDENCE_DISAMBIG_YEAR_DIFF AND first-author
        surname match between query and `best`. Both query.year and
        query.author must be non-empty for this branch to fire.
    """
    title_matches = [
        c for c in all_candidates
        if title_similarity(query.title, c.title) >= TITLE_EVIDENCE_THRESHOLD
    ]
    if not title_matches:
        return False

    groups = {(normalize_for_comparison(c.title), str(c.year))
              for c in title_matches}
    if len(groups) <= 1:
        return True

    if not query.year or not query.author:
        return False
    yd = year_diff(query.year, best.year)
    if yd is None or yd > TITLE_EVIDENCE_DISAMBIG_YEAR_DIFF:
        return False
    return author_match(query.author, best.authors)


def _search_one(query: PaperQuery, source_names: list[str],
                api_delay: float, max_hits: int, verbose: bool,
                ) -> MatchResult:
    """Cascade through sources for one query, classify, return MatchResult."""
    all_candidates: list[MatchCandidate] = []
    best: MatchCandidate | None = None
    best_status = "weak"
    best_mismatches: list[str] = []

    for src_name in source_names:
        backend = SOURCES.get(src_name)
        if backend is None:
            if verbose:
                print(f"  unknown source: {src_name}")
            continue

        cands = backend.search(query, max_hits=max_hits, verbose=verbose)
        all_candidates.extend(cands)

        for cand in cands:
            status, mismatches = classify(query, cand)
            if status == "weak":
                continue
            # Take the first non-weak candidate; thereafter, prefer
            # exact > fuzzy and break ties by score within the same status.
            if best is None:
                better = True
            else:
                better = (
                    (status == "exact" and best_status != "exact") or
                    (status == best_status and cand.score > best.score)
                )
            if better:
                best = cand
                best_status = status
                best_mismatches = mismatches

        # Stop cascading on a hard exact, or on a fuzzy that the
        # title-evidence override would promote (e.g. correct title +
        # placeholder author). Otherwise keep cascading to gather more
        # candidates for disambiguation.
        if best_status == "exact":
            break
        if best is not None and best_status == "fuzzy" and \
                _maybe_promote_via_title_evidence(query, best, all_candidates):
            best_status = "exact"
            best_mismatches = []
            break

        if api_delay > 0:
            time.sleep(api_delay)

    # De-dup all_candidates by (source, canonical_key) and sort
    seen = set()
    unique_cands = []
    for c in sorted(all_candidates, key=lambda c: c.score, reverse=True):
        sig = (c.source, c.canonical_key)
        if sig in seen:
            continue
        seen.add(sig)
        unique_cands.append(c)

    if best is None:
        return MatchResult(query=query, status="not_found", candidates=unique_cands)

    # Final post-cascade promotion check (e.g. when no source surfaced
    # an exact but the cumulative candidate set still resolves uniquely).
    if best_status == "fuzzy" and \
            _maybe_promote_via_title_evidence(query, best, all_candidates):
        best_status = "exact"
        best_mismatches = []

    if best_status == "exact":
        return MatchResult(query=query, status="exact", chosen=best,
                           candidates=unique_cands, mismatches=best_mismatches)

    # fuzzy
    return MatchResult(query=query, status="fuzzy_pending", chosen=best,
                       candidates=unique_cands, mismatches=best_mismatches)


def search_papers(
    queries: list[PaperQuery],
    *,
    sources: list[str] | None = None,
    interactive: bool = True,
    out_json: str | Path | None = None,
    out_bib: str | Path | None = None,
    api_delay: float = 3.0,
    max_hits: int = 5,
    verbose: bool = False,
    fetch_bibtex_for_fuzzy: bool = True,
) -> SearchReport:
    """Search each query across `sources` (cascading order) and classify.

    Args:
        queries: list of PaperQuery objects.
        sources: ordered list of source names; defaults to DEFAULT_ORDER.
        interactive: if True, prompt user to confirm each fuzzy match.
        out_json: optional path to write per-entry results JSON.
        out_bib: optional path to write a .bib file.
        api_delay: seconds between source queries (politeness; per-query).
        max_hits: candidates per source per query.
        verbose: print extra progress info.
        fetch_bibtex_for_fuzzy: fetch BibTeX for fuzzy entries even if user
            doesn't confirm them (so the .bib file has placeholders).
    """
    source_names = sources or DEFAULT_ORDER
    results: list[MatchResult] = []

    print(f"Searching {len(queries)} queries across {source_names}...")
    for i, q in enumerate(queries, 1):
        label = q.id or f"#{i}"
        print(f"[{i}/{len(queries)}] {label}: {q.title[:70]}")
        result = _search_one(q, source_names, api_delay, max_hits, verbose)
        if result.status == "exact":
            print(f"   -> EXACT  ({result.source}: {result.canonical_key})")
        elif result.status == "fuzzy_pending":
            print(f"   -> FUZZY  ({result.source}: {result.canonical_key})  "
                  f"{'; '.join(result.mismatches)}")
        else:
            print(f"   -> NOT FOUND")
        results.append(result)
        if api_delay > 0:
            time.sleep(api_delay)

    # Phase 2: interactive review of fuzzy entries
    if interactive:
        fuzzy = [r for r in results if r.status == "fuzzy_pending"]
        if fuzzy:
            interactive_review(fuzzy)

    # Fetch BibTeX for accepted entries
    accepted = [r for r in results
                if r.status in ("exact", "fuzzy_confirmed") and r.chosen]
    if fetch_bibtex_for_fuzzy:
        accepted += [r for r in results
                     if r.status == "fuzzy_pending" and r.chosen]

    print(f"\nFetching BibTeX for {len(accepted)} accepted entries...")
    n_fetch_failed = 0
    for r in accepted:
        if r.chosen and r.chosen.bibtex is None:
            backend = SOURCES.get(r.chosen.source)
            if backend is None:
                continue
            try:
                r.chosen.bibtex = backend.fetch_bibtex(r.chosen, verbose=verbose)
            except Exception as e:
                # Print unconditionally so silent fetch failures don't
                # quietly downgrade matched entries to manual stubs.
                n_fetch_failed += 1
                print(f"  WARN bibtex fetch failed for {r.canonical_key}: "
                      f"{type(e).__name__}: {e}")
            # Only sleep if the backend actually issued an HTTP call.
            # Synthesis-only backends (all four current ones) finish in
            # microseconds — a 3s sleep there is pure waiting.
            if api_delay > 0 and getattr(backend, "fetch_makes_http", False):
                time.sleep(api_delay)
    if n_fetch_failed:
        print(f"\n!! {n_fetch_failed}/{len(accepted)} BibTeX fetches failed. "
              f"Affected entries appear as FETCH_FAILED stubs in the .bib output "
              f"with the canonical key + manual-fetch URL preserved.")

    report = SearchReport(results=results)

    if out_json:
        report.write_json(out_json)
        print(f"Per-entry results -> {out_json}")
    if out_bib:
        write_bib_file(results, out_bib)
        print(f"BibTeX -> {out_bib}")

    print()
    report.summary()
    return report
