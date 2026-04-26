"""Title / author normalization and similarity.

Ported from paper_writing/fetch_bibtex.py and paper_writing/verify_dblp.py
to a single shared implementation.
"""

from __future__ import annotations

import difflib
import re
import unicodedata


# LaTeX-friendly accent stripper: \"{u} -> u, \v{c} -> c, \"u -> u, etc.
_ACCENT_BRACED = re.compile(r'\\["\'^`~vucdbHtrk]\{([^}])\}')
_ACCENT_BARE = re.compile(r'\\["\'^`~vucdbHtrk]([a-zA-Z])')
_FORMAT_CMDS = re.compile(r'\\(?:em|it|bf|textit|textbf|emph)\b\s*')
_CITE_CMDS = re.compile(r'\\(?:short)?cite\{[^}]*\}')


def clean_latex(text: str) -> str:
    """Strip LaTeX markup so the text is search-friendly."""
    text = text.replace(r'\newblock', '').strip()
    text = _FORMAT_CMDS.sub('', text)
    text = _ACCENT_BRACED.sub(r'\1', text)
    text = _ACCENT_BARE.sub(r'\1', text)
    text = text.replace('{', '').replace('}', '')
    text = text.replace('~', ' ')
    text = _CITE_CMDS.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text.rstrip('.')


def normalize_for_comparison(s: str) -> str:
    """Lowercase, strip diacritics + punctuation, collapse whitespace."""
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r'[^\w\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def title_similarity(a: str, b: str) -> float:
    """Ratcliff-Obershelp similarity on normalized titles."""
    return difflib.SequenceMatcher(None, normalize_for_comparison(a),
                                   normalize_for_comparison(b)).ratio()


# Search-side title sanitizer. Used to build alternative search queries for
# titles whose original form contains characters that bibliographic search
# APIs (esp. DBLP) do not index as tokens. The two important rules:
#   1. CUT the offending characters; do not replace with space. DBLP indexes
#      'HMD2' as one token, so the query 'HMD2' matches; 'HMD 2' splits and
#      DBLP returns no hit. The user verified this empirically.
#   2. Translate Unicode super/subscripts to plain digits (so ² -> 2 directly,
#      no space).
#   3. Unicode dashes (U+2010..U+2015) are different — those are best treated
#      as ASCII '-', not cut, because they typically appear in compounds
#      ('head-mounted') and dropping them would also break tokenization.
_SEARCH_CUT = re.compile(r'[\^_$\\{}~|<>]')
_SEARCH_SUPSUB = str.maketrans({
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
    '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
    '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
})
_SEARCH_DASH = re.compile(r'[‐-―]')


def normalize_for_search(title: str) -> str:
    """Strip characters that break bibliographic search APIs.

    Returns the title in the form most likely to match a search index's
    tokenizer. See _SEARCH_CUT/_SEARCH_SUPSUB/_SEARCH_DASH for the full
    set of transformations and the rationale behind cut-vs-space.

    Idempotent: normalize_for_search(normalize_for_search(t)) == normalize_for_search(t).
    """
    t = title.translate(_SEARCH_SUPSUB)
    t = _SEARCH_DASH.sub('-', t)
    t = _SEARCH_CUT.sub('', t)            # cut, no space
    return re.sub(r'\s+', ' ', t).strip()


def title_search_variants(title: str) -> list[str]:
    """Ordered list of search-query variants for a single paper title.

    The caller tries each variant in order; the first that returns hits wins.
    Order, with rationale:
      1. Original title — preserves behavior on clean inputs (no extra HTTP).
      2. Normalized title — rare chars cut. Catches the 'HMD^2 -> HMD2' case
         where the original returns 0 hits because the index can't tokenize
         the special character.
      3. Pre-colon / pre-period base — strips subtitles. Helps when the
         indexed title differs from the user-supplied one only in punctuation
         after the main title (e.g., "X: Sub-title" vs "X: A New Sub-title"),
         and main-title has >= 3 words so it remains specific.

    Returns at most 3 distinct variants; collapses duplicates.
    """
    out: list[str] = []
    if title:
        out.append(title)
    norm = normalize_for_search(title)
    if norm and norm not in out:
        out.append(norm)
    # Pre-colon base form. Apply on the normalized variant when it exists,
    # else on the original — either way we want a clean leading clause.
    src_for_base = norm or title
    base = re.split(r'[:.\-]', src_for_base, maxsplit=1)[0].strip()
    if base and base not in out and len(base.split()) >= 3:
        out.append(base)
    return out


# Match leading initials like "A. ", "A.~", "A. B. ". The period is REQUIRED
# (not optional) — without it, the pattern over-matched lone capitals: "A. B.
# Smith" stripped to "mith" (the third initial 'S' got eaten without a period
# anchor), and "Müller" stripped to "üller" (the 'M' was treated as a bare
# initial). Requiring the period restricts the match to bona fide initials.
_INITIAL_PREFIX = re.compile(r'^([A-Z]\.\s*~?\s*)+')


def first_author_surname(author_field: str) -> str:
    """Extract the first author's surname from a free-form author string.

    Disambiguates four input shapes:
      A. "Smith, John"                   — single "Last, First" name
      B. "John Smith"                    — single "First Last" name
      C. "John Smith and Jane Doe"       — BibTeX-style "and"-separated list
      D. "John Smith, Jane Doe, Bob Lee" — comma-separated list of full names
         (this is how the JSON input pool stores authors; mishandled prior
         to this fix because the first comma was read as a Case-A "Last,
         First" delimiter, returning a two-word "surname" like
         "alexander winkler" that then silently failed substring matching
         against DBLP records carrying a middle initial.)

    The Case-A vs Case-D ambiguity (single comma) is broken by looking at
    the part before the comma: a single token = surname (Case A), multiple
    tokens = first author of a list (Case D). Returns "" if blank.
    """
    if not author_field:
        return ""
    s = clean_latex(author_field)
    # Split on BibTeX "and" first (Case C → reduces to A/B/D on first group)
    first_group = re.split(r'\s+and\s+', s, maxsplit=1)[0].strip()

    if ',' in first_group:
        n_commas = first_group.count(',')
        if n_commas >= 2:
            # Case D: definitely a comma-separated list of full names.
            first_name = first_group.split(',', 1)[0].strip()
            return _surname_from_first_last(first_name)
        # Single comma: Case A vs Case D ambiguous.
        before, _ = [p.strip() for p in first_group.split(',', 1)]
        if len(before.split()) == 1:
            # Case A: "Last, First" — surname is the part before the comma.
            return normalize_for_comparison(before)
        # Case D one-author edge: "John Smith, Jane Doe" - first is "John Smith".
        return _surname_from_first_last(before)

    # Case B: "First [Middle] Last".
    return _surname_from_first_last(first_group)


def _surname_from_first_last(name: str) -> str:
    """Last word of a 'First [Middle] Last' name, after stripping leading
    initials like 'J.~' or 'A. B. ' so 'A. B. Smith' yields 'smith'."""
    no_initials = _INITIAL_PREFIX.sub('', name).strip()
    words = no_initials.split()
    surname = words[-1] if words else name
    return normalize_for_comparison(surname)


def author_match(query_author: str, candidate_authors: list[str]) -> bool:
    """True if query's first-author surname appears in any candidate author."""
    qs = first_author_surname(query_author)
    if not qs:
        return True  # no query author to check
    for a in candidate_authors:
        if qs in normalize_for_comparison(a).split():
            return True
        # Also check normalized whole-string contains (handles "Müller-Bender" etc.)
        if qs in normalize_for_comparison(a):
            return True
    return False


def year_diff(query_year: str, candidate_year: str) -> int | None:
    """Absolute year difference, or None if either side is unparseable."""
    try:
        return abs(int(query_year) - int(candidate_year))
    except (ValueError, TypeError):
        return None
