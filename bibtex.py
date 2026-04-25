"""BibTeX file writer with manual stubs for not_found entries."""

from __future__ import annotations

from pathlib import Path

from .models import MatchCandidate, MatchResult, PaperQuery


def make_manual_stub(query: PaperQuery, marker: str = "TODO") -> str:
    """Generate a placeholder BibTeX entry for a query we couldn't resolve."""
    venue = (query.venue or "").lower()
    if any(kw in venue for kw in ("trans. graph", "journal", "ieee", "siam", "comput")):
        bib_type = "article"
    elif any(kw in venue for kw in ("siggraph", "sca", "mig", "eurographics",
                                    "conference", "proceedings", "symposium")):
        bib_type = "inproceedings"
    elif any(kw in venue for kw in ("dover", "springer", "press", "publisher")):
        bib_type = "book"
    else:
        bib_type = "misc"

    key = query.id or "unresolved"
    lines = [
        f"% {marker}: Manually verify this entry — not found in any source",
        f"@{bib_type}{{{key},",
        f"  title = {{{query.title}}},",
    ]
    if query.author:
        lines.append(f"  author = {{{query.author}}},")
    if query.year:
        lines.append(f"  year = {{{query.year}}},")
    if query.venue:
        venue_field = "journal" if bib_type == "article" else "booktitle"
        lines.append(f"  {venue_field} = {{{query.venue}}},")
    lines.append("}")
    return "\n".join(lines)


def _manual_fetch_url(candidate: MatchCandidate) -> str:
    """Return a per-source URL the user can curl to retrieve the BibTeX
    by hand when the in-process fetch failed (e.g. transient rate limit)."""
    canon = candidate.canonical_key or ""
    src = candidate.source
    if src == "dblp" and canon.startswith("DBLP:"):
        return f"https://dblp.org/rec/{canon.removeprefix('DBLP:')}.bib"
    if src == "crossref" and canon:
        return (f"https://api.crossref.org/works/{canon}"
                f"/transform/application/x-bibtex")
    if src == "openalex" and canon.startswith("W"):
        return f"https://api.openalex.org/works/{canon}"
    if src == "arxiv" and canon:
        return f"https://arxiv.org/abs/{canon}"
    return ""


def make_fetch_failed_stub(query: PaperQuery, candidate: MatchCandidate,
                            marker: str = "FETCH_FAILED") -> str:
    """Stub for entries where search matched but BibTeX retrieval failed.

    Synthesizes a placeholder from the matched candidate's metadata and
    preserves the canonical_key + a manual-fetch URL hint in a comment,
    so the user can either re-run bibtools or curl the bib by hand.
    """
    canon = candidate.canonical_key or ""
    key = query.id or canon.replace(":", "_").replace("/", "_").replace(".", "_") \
                          or "unresolved"
    fetch_url = _manual_fetch_url(candidate)

    lines = [
        f"% {marker}: search matched in {candidate.source} "
        f"(canonical_key: {canon}) but BibTeX retrieval failed.",
        f"% Re-run bibtools or fetch by hand:",
    ]
    if fetch_url:
        lines.append(f"%   curl -sL '{fetch_url}'")
    else:
        lines.append("%   (no stable bib endpoint for this source — verify manually)")
    lines.append(f"@misc{{{key},")
    lines.append(f"  title = {{{candidate.title or query.title}}},")
    if candidate.authors:
        lines.append(f"  author = {{{' and '.join(candidate.authors)}}},")
    elif query.author:
        lines.append(f"  author = {{{query.author}}},")
    if candidate.year:
        lines.append(f"  year = {{{candidate.year}}},")
    elif query.year:
        lines.append(f"  year = {{{query.year}}},")
    if query.venue:
        lines.append(f"  booktitle = {{{query.venue}}},")
    lines.append("}")
    return "\n".join(lines)


def _comment_out(bib_text: str) -> str:
    """Prefix every non-comment, non-blank line of `bib_text` with '% '.

    Lines already starting with '%' (e.g., the explanatory header inside
    a stub) and blank lines are passed through unchanged so we don't
    double-comment or fill blank rows.
    """
    return "\n".join(
        line if (not line.strip() or line.lstrip().startswith("%")) else f"% {line}"
        for line in bib_text.splitlines()
    )


def write_bib_file(results: list[MatchResult], path: str | Path) -> None:
    """Write a .bib file. Only `status == "exact"` entries are emitted live;
    every other status (fuzzy_*, fetch_failed, not_found) is written with
    every line prefixed by '% ' so LaTeX cannot accidentally cite an
    unverified record.

    Three buckets:
      - accepted: search matched AND BibTeX successfully retrieved.
        Within this bucket, only `status == "exact"` is live; fuzzy_*
        accepted entries are commented out.
      - fetch_failed: search matched but BibTeX retrieval failed (raises in
        backend.fetch_bibtex). Stub preserves canonical_key + manual-fetch
        URL; commented out.
      - unresolved: search did not match (not_found / fuzzy_rejected /
        fuzzy_skipped). Generic manual stub from the query alone; commented
        out.

    Sort order: accepted by canonical_key; fetch_failed by canonical_key;
    unresolved by query id.
    """
    accepted: list[MatchResult] = []
    fetch_failed: list[MatchResult] = []
    unresolved: list[MatchResult] = []
    for r in results:
        if r.status in ("exact", "fuzzy_confirmed", "fuzzy_pending") and r.chosen:
            if r.chosen.bibtex:
                accepted.append(r)
            else:
                fetch_failed.append(r)
        else:
            unresolved.append(r)

    accepted.sort(key=lambda r: (r.canonical_key or "").lower())
    fetch_failed.sort(key=lambda r: (r.canonical_key or r.query.id or "").lower())
    unresolved.sort(key=lambda r: (r.query.id or r.query.title).lower())

    out = Path(path)
    with out.open("w", encoding="utf-8") as f:
        f.write("% Auto-generated by bibtools.\n")
        f.write("% Only entries with status=exact are emitted live; everything else\n")
        f.write("% (fuzzy matches, FETCH_FAILED, TODO/REJECTED/SKIPPED stubs) is\n")
        f.write("% commented out line-by-line. Verify manually before un-commenting.\n\n")

        for r in accepted:
            assert r.chosen and r.chosen.bibtex  # for mypy
            bib = r.chosen.bibtex.strip()
            if r.status == "exact":
                f.write(bib)
            else:
                # fuzzy_confirmed / fuzzy_pending: live-format bib but inert.
                if r.status == "fuzzy_pending":
                    f.write("% TODO: fuzzy match, not user-confirmed (commented out)\n")
                elif r.status == "fuzzy_confirmed":
                    head = "% NOTE: fuzzy match (user confirmed)"
                    ms = "; ".join(r.mismatches)
                    if ms:
                        head += f": {ms}"
                    f.write(head + " (commented out)\n")
                f.write(_comment_out(bib))
            f.write("\n\n")

        for r in fetch_failed:
            assert r.chosen
            f.write(_comment_out(make_fetch_failed_stub(r.query, r.chosen)))
            f.write("\n\n")

        for r in unresolved:
            if r.status == "fuzzy_rejected":
                marker = "REJECTED"
            elif r.status == "fuzzy_skipped":
                marker = "SKIPPED"
            else:
                marker = "TODO"
            f.write(_comment_out(make_manual_stub(r.query, marker=marker)))
            f.write("\n\n")
