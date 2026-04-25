"""Dataclasses used throughout bibtools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Status = Literal[
    "exact",
    "fuzzy_pending",
    "fuzzy_confirmed",
    "fuzzy_rejected",
    "fuzzy_skipped",
    "not_found",
]


@dataclass
class PaperQuery:
    """A user-supplied paper to look up.

    Title and author are treated as authoritative for matching.
    Year and venue are advisory (used for tie-breaking and mismatch reporting).
    """
    title: str
    author: str = ""               # free-form; first surname is extracted
    year: str = ""
    venue: str = ""
    id: str | None = None          # local citation key; auto-generated if None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PaperQuery":
        known = {"title", "author", "year", "venue", "id"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            title=str(d.get("title", "")).strip(),
            author=str(d.get("author", "")).strip(),
            year=str(d.get("year", "")).strip(),
            venue=str(d.get("venue", "")).strip(),
            id=d.get("id"),
            extra=extra,
        )

    @classmethod
    def load_json(cls, path: str | Path) -> list["PaperQuery"]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array of paper objects, got {type(data).__name__}")
        return [cls.from_dict(d) for d in data]


@dataclass
class MatchCandidate:
    """A single hit from a search backend, scored against the query."""
    source: str                    # "dblp" | "crossref" | "openalex" | "arxiv"
    score: float                   # 0.0 - 1.0+ (with tie-break bonuses)
    title: str
    authors: list[str]
    year: str
    canonical_key: str             # e.g. "DBLP:journals/tog/Foo24" or DOI
    raw: dict[str, Any] = field(default_factory=dict)  # source-specific payload
    bibtex: str | None = None      # filled lazily on accept

    def title_match(self, query_title: str, threshold: float = 0.95) -> bool:
        from .normalize import title_similarity
        return title_similarity(self.title, query_title) >= threshold

    def to_brief(self) -> dict:
        return {
            "source": self.source,
            "score": round(self.score, 3),
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "canonical_key": self.canonical_key,
        }


@dataclass
class MatchResult:
    """The outcome for a single PaperQuery."""
    query: PaperQuery
    status: Status
    chosen: MatchCandidate | None = None       # the accepted candidate, if any
    candidates: list[MatchCandidate] = field(default_factory=list)  # all top hits, all sources
    mismatches: list[str] = field(default_factory=list)  # human-readable diff vs. query
    user_decision: str | None = None           # only set when interactive

    @property
    def source(self) -> str | None:
        return self.chosen.source if self.chosen else None

    @property
    def canonical_key(self) -> str | None:
        return self.chosen.canonical_key if self.chosen else None

    def to_dict(self) -> dict:
        d = {
            "id": self.query.id,
            "status": self.status,
            "source": self.source,
            "canonical_key": self.canonical_key,
            "score": round(self.chosen.score, 3) if self.chosen else None,
            "mismatches": self.mismatches,
            "user_decision": self.user_decision,
            "query": {
                "title": self.query.title,
                "author": self.query.author,
                "year": self.query.year,
            },
            "matched": self.chosen.to_brief() if self.chosen else None,
            "alternates": [c.to_brief() for c in self.candidates if c is not self.chosen][:4],
        }
        return d


@dataclass
class SearchReport:
    """Aggregate result of search_papers()."""
    results: list[MatchResult]

    @property
    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    @property
    def by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            if r.source:
                out[r.source] = out.get(r.source, 0) + 1
        return out

    def summary(self, fh=None) -> dict:
        """Print summary to stdout (or fh) and return as dict."""
        import sys
        fh = fh or sys.stdout
        bs = self.by_status
        bsrc = self.by_source

        exact = bs.get("exact", 0)
        confirmed = bs.get("fuzzy_confirmed", 0)
        rejected = bs.get("fuzzy_rejected", 0)
        skipped = bs.get("fuzzy_skipped", 0)
        pending = bs.get("fuzzy_pending", 0)
        not_found = bs.get("not_found", 0)
        total = len(self.results)

        fuzzy_total = confirmed + rejected + skipped + pending
        fuzzy_breakdown = []
        if confirmed: fuzzy_breakdown.append(f"{confirmed} confirmed")
        if rejected:  fuzzy_breakdown.append(f"{rejected} rejected")
        if skipped:   fuzzy_breakdown.append(f"{skipped} skipped")
        if pending:   fuzzy_breakdown.append(f"{pending} pending review")
        fuzzy_str = f"  ({', '.join(fuzzy_breakdown)})" if fuzzy_breakdown else ""

        print("=" * 60, file=fh)
        print("BibTools Search Report", file=fh)
        print("=" * 60, file=fh)
        print(f"Total queries:     {total}", file=fh)
        print(f"Exact matches:     {exact:>4}  (auto-accepted)", file=fh)
        print(f"Fuzzy matches:     {fuzzy_total:>4}{fuzzy_str}", file=fh)
        print(f"Not found:         {not_found:>4}", file=fh)
        if bsrc:
            srcs = ", ".join(f"{k}={v}" for k, v in sorted(bsrc.items()))
            print(f"By source: {srcs}", file=fh)

        self.print_unresolved(fh)

        return {
            "total": total,
            "exact": exact,
            "fuzzy": fuzzy_total,
            "not_found": not_found,
            "by_status": bs,
            "by_source": bsrc,
        }

    def print_unresolved(self, fh=None) -> None:
        """List every non-exact entry (fuzzy + not_found) with title and author."""
        import sys
        fh = fh or sys.stdout
        non_exact = [r for r in self.results if r.status != "exact"]
        if not non_exact:
            return

        # Group by status for readability
        groups: dict[str, list[MatchResult]] = {}
        for r in non_exact:
            groups.setdefault(r.status, []).append(r)

        # Stable order: pending review first, then confirmed, rejected, skipped, not_found
        status_order = ["fuzzy_pending", "fuzzy_confirmed", "fuzzy_rejected",
                        "fuzzy_skipped", "not_found"]
        labels = {
            "fuzzy_pending":   "Fuzzy (pending review)",
            "fuzzy_confirmed": "Fuzzy (user confirmed)",
            "fuzzy_rejected":  "Fuzzy (user rejected)",
            "fuzzy_skipped":   "Fuzzy (user skipped)",
            "not_found":       "Not found",
        }

        print("", file=fh)
        print("-" * 60, file=fh)
        print("Non-exact entries (title / author):", file=fh)
        print("-" * 60, file=fh)
        for status in status_order:
            items = groups.get(status, [])
            if not items:
                continue
            print(f"\n[{labels[status]}]  ({len(items)})", file=fh)
            for r in items:
                q = r.query
                ident = q.id or "?"
                author = q.author or "(no author)"
                year = f" ({q.year})" if q.year else ""
                print(f"  - {ident}: {q.title!r}", file=fh)
                print(f"      by {author}{year}", file=fh)
                if r.mismatches:
                    print(f"      mismatch: {'; '.join(r.mismatches)}", file=fh)

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps([r.to_dict() for r in self.results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
