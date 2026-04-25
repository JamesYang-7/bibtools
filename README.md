# bibtools

A small, self-contained Python package for BibTeX lookup and verification.
Searches DBLP, CrossRef, OpenAlex, and arXiv from a JSON list of papers and
reports how many were exact matches, fuzzy matches, or not found.

**No third-party dependencies** — uses only the Python standard library.
Copy this folder into any project and it works.

## Quick start

### Input JSON schema

A JSON array of paper objects. Title and author are treated as authoritative
for matching; year and venue are advisory.

```json
[
  {
    "id": "macklin2016xpbd",
    "title": "XPBD: Position-Based Simulation of Compliant Constrained Dynamics",
    "author": "Macklin, Müller, Chentanez",
    "year": "2016",
    "venue": "MIG"
  },
  {
    "id": "chen2024vbd",
    "title": "Vertex Block Descent",
    "author": "Chen et al.",
    "year": "2024"
  }
]
```

| Field    | Required | Notes |
|----------|----------|-------|
| `title`  | yes      | Used for similarity matching. |
| `author` | no       | Free-form. First surname is auto-extracted. |
| `year`   | no       | String or int. Year mismatch downgrades to fuzzy. |
| `venue`  | no       | Used to guess BibTeX type for not-found stubs. |
| `id`     | no       | Local citation key; auto-generated if absent. |

### CLI

```bash
python -m bibtools papers.json --out-bib refs.bib --out-json results.json
# Or skip the interactive prompt:
python -m bibtools papers.json --out-bib refs.bib --no-interactive
# Limit sources:
python -m bibtools papers.json --sources dblp crossref
```

### Programmatic API

```python
from bibtools import search_papers, PaperQuery

queries = PaperQuery.load_json("papers.json")
report = search_papers(
    queries,
    sources=["dblp", "crossref", "openalex", "arxiv"],
    interactive=True,
    out_bib="refs.bib",
    out_json="results.json",
)

# report.summary() prints + returns a dict:
# {"total": 50, "exact": 32, "fuzzy": 6, "not_found": 12, ...}
counts = report.summary()
for r in report.results:
    print(r.query.id, r.status, r.canonical_key)
```

## How matching works

For each query, sources are queried in cascade order. Each candidate is
scored on title similarity (Ratcliff-Obershelp on normalized text) and
classified as:

- **exact** — title similarity ≥ 0.95 *and* year matches *and* first-author
  surname appears in the candidate's author list. Auto-accepted, no prompt.
- **fuzzy** — title similarity ≥ 0.75 but at least one of {title 0.75–0.95,
  year off, author missing}. Held for phase-2 interactive review.
- **not_found** — no candidate above 0.75 from any source.

The first **exact** match short-circuits the cascade. Fuzzy matches are
collected from all sources before classification, so the user sees the best
candidate available.

## Output report

```
============================================================
BibTools Search Report
============================================================
Total queries:     50
Exact matches:       32  (auto-accepted)
Fuzzy matches:        6  (4 confirmed, 1 rejected, 1 skipped)
Not found:           12
By source: dblp=28, crossref=8, openalex=2, arxiv=0
```

The `--out-json` file gives per-entry detail:

```json
{
  "id": "zhu2010",
  "status": "fuzzy_confirmed",
  "source": "dblp",
  "canonical_key": "DBLP:journals/tog/ZhuB05",
  "score": 0.91,
  "mismatches": ["year: 2010 -> 2005"],
  "user_decision": "confirmed",
  "matched": {"title": "...", "authors": [...], "year": "2005"},
  "alternates": [...]
}
```

## Layout

```
bibtools/
├── __init__.py          public API
├── __main__.py          enables `python -m bibtools`
├── cli.py               argparse CLI
├── models.py            PaperQuery, MatchCandidate, MatchResult, SearchReport
├── normalize.py         title/author normalization + similarity
├── http.py              retry-aware HTTP GET
├── search.py            cascade orchestrator + classifier
├── interactive.py       phase-2 fuzzy review prompt
├── bibtex.py            .bib writer + manual stubs
└── sources/
    ├── base.py          SourceBackend protocol
    ├── dblp.py
    ├── crossref.py
    ├── openalex.py
    └── arxiv.py
```

## Reusing in another project

The folder has zero project-internal imports. To use it elsewhere:

```bash
cp -r paper_writing/bibtools /path/to/other_project/
cd /path/to/other_project
python -m bibtools papers.json --out-bib refs.bib
```

## Adding a new source

1. Create `bibtools/sources/myservice.py` with a class implementing the
   `SourceBackend` protocol from `bibtools/sources/base.py`:

   ```python
   class MyServiceSource:
       name = "myservice"
       def search(self, query, *, max_hits=5, verbose=False) -> list[MatchCandidate]: ...
       def fetch_bibtex(self, candidate, *, verbose=False) -> str: ...
   ```

2. Register it in `bibtools/sources/__init__.py`:

   ```python
   from .myservice import MyServiceSource
   SOURCES["myservice"] = MyServiceSource()
   DEFAULT_ORDER.append("myservice")
   ```

## Tunables

Module-level constants in `search.py`:

- `EXACT_TITLE_THRESHOLD = 0.95`
- `FUZZY_TITLE_THRESHOLD = 0.75`
- `ACCEPTABLE_YEAR_DIFF = 2`

Module-level constants in `http.py`:

- `DEFAULT_TIMEOUT = 15`
- `DEFAULT_RETRIES = 3`
- `DEFAULT_API_DELAY = 3.0` (used as the default `--api-delay` / `api_delay=` value in `cli.py` and `search.py`)

DBLP, CrossRef, OpenAlex, and arXiv all synthesize BibTeX locally from
the search-time response payload in `MatchCandidate.raw`, so
`fetch_bibtex` never makes a second HTTP round-trip per accepted entry.
