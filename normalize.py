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


_INITIAL_PREFIX = re.compile(r'^([A-Z]\.?\s*~?\s*)+')


def first_author_surname(author_field: str) -> str:
    """Extract the first author's surname from a free-form author string.

    Handles: "Smith, John and Doe, Jane", "John Smith and Jane Doe",
    "J.~Smith, J. Doe", "Müller". Returns "" if blank.
    """
    if not author_field:
        return ""
    s = clean_latex(author_field)
    # Take everything before "and" or the first comma+space (semantic separator)
    first = re.split(r'\s+and\s+', s, maxsplit=1)[0].strip()
    if ',' in first:
        # "Last, First" form — surname is before the comma
        surname = first.split(',', 1)[0].strip()
    else:
        # "First Last" form — strip leading initials, take last word
        no_initials = _INITIAL_PREFIX.sub('', first).strip()
        surname = no_initials.split()[-1] if no_initials.split() else first
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
