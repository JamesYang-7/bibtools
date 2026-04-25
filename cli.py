"""Command-line entry point: `python -m bibtools input.json [...]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import PaperQuery
from .search import search_papers
from .sources import DEFAULT_ORDER


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="bibtools",
        description="Search DBLP/CrossRef/OpenAlex/arXiv for paper references.",
    )
    p.add_argument("input", type=Path,
                   help="JSON file with array of {title, author, year, ...}")
    p.add_argument("--out-bib", type=Path, default=None,
                   help="Write BibTeX to this path")
    p.add_argument("--out-json", type=Path, default=None,
                   help="Write per-entry JSON results to this path")
    p.add_argument("--sources", nargs="+", default=DEFAULT_ORDER,
                   choices=DEFAULT_ORDER,
                   help=f"Sources to query, in cascade order (default: {' '.join(DEFAULT_ORDER)})")
    p.add_argument("--no-interactive", action="store_true",
                   help="Skip phase-2 review of fuzzy matches")
    p.add_argument("--api-delay", type=float, default=3.0,
                   help="Seconds between API calls (default: 3.0)")
    p.add_argument("--max-hits", type=int, default=5,
                   help="Candidates per source (default: 5)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    queries = PaperQuery.load_json(args.input)
    if not queries:
        print(f"ERROR: input JSON contained zero entries", file=sys.stderr)
        return 2

    search_papers(
        queries,
        sources=args.sources,
        interactive=not args.no_interactive,
        out_json=args.out_json,
        out_bib=args.out_bib,
        api_delay=args.api_delay,
        max_hits=args.max_hits,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
