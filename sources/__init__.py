"""Source backend registry.

Each backend implements the SourceBackend protocol from .base.
Add a new backend by importing it here and adding it to SOURCES.
"""

from .base import SourceBackend, Hit
from .dblp import DBLPSource
from .crossref import CrossRefSource
from .openalex import OpenAlexSource
from .arxiv import ArXivSource

SOURCES: dict[str, SourceBackend] = {
    "dblp": DBLPSource(),
    "crossref": CrossRefSource(),
    "openalex": OpenAlexSource(),
    "arxiv": ArXivSource(),
}

DEFAULT_ORDER = ["dblp", "crossref", "openalex", "arxiv"]

__all__ = ["SOURCES", "DEFAULT_ORDER", "SourceBackend", "Hit"]
