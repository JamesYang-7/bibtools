"""Phase-2 interactive review of fuzzy match candidates."""

from __future__ import annotations

from .models import MatchResult


def _print_candidate(idx: int, total: int, cand, mismatches: list[str]) -> None:
    print(f"  Candidate {idx + 1}/{total}  ({cand.source}, score={cand.score:.3f})")
    print(f"    Title:   {cand.title}")
    if cand.authors:
        a = ", ".join(cand.authors[:3]) + (" ..." if len(cand.authors) > 3 else "")
        print(f"    Authors: {a}")
    print(f"    Year:    {cand.year}")
    print(f"    Key:     {cand.canonical_key}")
    if mismatches:
        print(f"    Mismatch: {'; '.join(mismatches)}")


def _prompt(prompt_str: str) -> str:
    try:
        return input(prompt_str).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "s"


def interactive_review(fuzzy_results: list[MatchResult]) -> None:
    """Walk through each fuzzy result and ask the user to confirm/reject.

    Mutates each MatchResult in place: sets `status` to fuzzy_confirmed /
    fuzzy_rejected / fuzzy_skipped and may swap `chosen` for an alternate.
    """
    print(f"\n{'=' * 60}")
    print(f"Interactive review: {len(fuzzy_results)} fuzzy match{'es' if len(fuzzy_results) != 1 else ''}")
    print(f"{'=' * 60}")
    print("Commands per entry: [y]es / [n]o / [s]kip / [a]lt (next candidate) / [q]uit\n")

    for i, result in enumerate(fuzzy_results, 1):
        q = result.query
        print(f"[{i}/{len(fuzzy_results)}] {q.id or '?'}")
        print(f"  Query:   {q.title!r}")
        if q.author or q.year:
            print(f"           by {q.author or '?'}  ({q.year or '?'})")
        if q.venue:
            print(f"           venue: {q.venue}")

        # Build the candidate carousel: chosen first, then alternates
        carousel = [result.chosen] if result.chosen else []
        for c in result.candidates:
            if c not in carousel:
                carousel.append(c)
        if not carousel:
            print("  (no candidates to show)")
            result.status = "fuzzy_skipped"
            continue

        idx = 0
        while True:
            cand = carousel[idx]
            # Recompute mismatches for this candidate
            from .search import classify
            _, ms = classify(q, cand)
            _print_candidate(idx, len(carousel), cand, ms)
            choice = _prompt("  Accept? [y/n/s/a/q]: ")

            if choice in ("y", "yes"):
                result.chosen = cand
                result.mismatches = ms
                result.status = "fuzzy_confirmed"
                result.user_decision = "confirmed"
                break
            elif choice in ("n", "no"):
                result.chosen = None
                result.status = "fuzzy_rejected"
                result.user_decision = "rejected"
                break
            elif choice in ("s", "skip", ""):
                result.status = "fuzzy_skipped"
                result.user_decision = "skipped"
                break
            elif choice in ("a", "alt", "next"):
                idx = (idx + 1) % len(carousel)
                if idx == 0:
                    print("  (wrapped around to first candidate)")
                continue
            elif choice in ("q", "quit"):
                print("  Aborting interactive review; remaining entries left as fuzzy_pending")
                return
            else:
                print(f"  unknown choice {choice!r}; try y/n/s/a/q")
        print()
