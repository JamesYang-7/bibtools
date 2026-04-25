"""Shared HTTP helper with retry, exponential backoff, and 429 handling.

Consolidates the two ad-hoc implementations from fetch_bibtex.py and
verify_dblp.py into one well-behaved client.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

USER_AGENT = "bibtools/1.0 (academic reference lookup; +https://github.com/)"
DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 3
DEFAULT_API_DELAY = 3.0  # polite spacing between successive API calls


def http_get(
    url: str,
    headers: dict | None = None,
    retries: int = DEFAULT_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> str:
    """GET a URL with retry, returning the decoded body.

    Raises RuntimeError if all retries fail. Honors HTTP 429 with longer backoff.
    """
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            last_err = e
            # 429 = rate limited: back off harder
            wait = (10 if e.code == 429 else 3) * (2 ** attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            wait = 3 * (2 ** attempt)

        if attempt < retries - 1:
            if verbose:
                print(f"  [http retry in {wait}s after {type(last_err).__name__}]")
            time.sleep(wait)

    raise RuntimeError(f"HTTP request failed after {retries} attempts: {last_err}")
