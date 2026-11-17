"""
Lookups against the Crossref REST API.

Each function makes one attempt through :mod:`archivum.egress` and returns
nothing on any failure, since the callers (ingest, the ``crossref`` command)
are interactive and carry on without the answer.
"""
from functools import lru_cache
import logging
from typing import Union, List, Dict, Optional, Any

from . import egress

logger = logging.getLogger(__name__)

BASE_URL = "https://api.crossref.org/works"
TIMEOUT_SECONDS = 10


def _get_json(url: str, params: Optional[dict], kind: str, what: str) -> Optional[dict]:
    """
    Fetch one Crossref answer.

    Parameters
    ----------
    url : str
        The Crossref address.
    params : dict or None
        Query parameters.
    kind : str
        ``egress.SINGLE`` or ``egress.SEARCH``, which sets the pace.
    what : str
        The lookup, for the log line on failure.

    Returns
    -------
    dict or None
        The parsed JSON, or ``None`` when the request failed or the status
        was not 200.
    """
    try:
        resp = egress.default().get(url, params=params, kind=kind, retry=False, timeout=TIMEOUT_SECONDS)
    except (egress.EgressFailed, egress.HostStopped) as e:
        logger.error("Error %s: %s", what, e)
        return None
    if resp.status_code != 200:
        logger.error("Error %s: HTTP %s", what, resp.status_code)
        return None
    return resp.json()


@lru_cache(maxsize=None)
def lookup_doi(doi: str) -> Union[Dict[str, Any], None]:
    """Lookup an individual doi string."""
    logger.info("crossref doi %s", doi)
    data = _get_json(f"{BASE_URL}/{doi}", None, egress.SINGLE, f"looking up DOI {doi}")
    return data["message"] if data else None


@lru_cache(maxsize=None)
def search_by_title(title: str, rows: int = 1) -> Union[Dict[str, Any], None]:
    """Reverse lookup via title search."""
    logger.info("crossref title %s", title)
    data = _get_json(BASE_URL, {"query.title": title, "rows": rows}, egress.SEARCH, f"searching title {title}")
    items = (data or {}).get("message", {}).get("items", [])
    return items[0] if items else None


@lru_cache(maxsize=None)
def search(
    query: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
    rows: int = 5,
    book_mode: bool = False,
) -> List[Dict[str, Any]]:
    """Generic search: keywords, title, and/or author."""
    logger.info("crossref search title=%s author=%s query=%s", title, author, query)
    params = {"rows": rows}
    if query:
        params["query"] = query
    if author:
        params["query.author"] = author
    if title:
        params["query.title"] = title

    if book_mode:
        params["filter"] = ("type:book",)  # Filters specifically for books

    data = _get_json(BASE_URL, params, egress.SEARCH, "in generic search")
    return (data or {}).get("message", {}).get("items", [])
