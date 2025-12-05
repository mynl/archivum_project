"""
Code for interacting with the crossref api.

With GPT.

v2 Gemini updates
v1 GPT
"""
from functools import lru_cache
import logging
from typing import Union, List, Dict, Optional, Any

import requests
from requests.exceptions import RequestException

from . import __version__ as version

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": f"archivum/{version} (mailto:stephenjmildenhall@gmail.com)"}
BASE_URL = "https://api.crossref.org/works"


@lru_cache(maxsize=None)
def lookup_doi(doi: str) -> Union[Dict[str, Any], None]:
    """Lookup an individual doi string."""
    logger.info("crossref doi %s", doi)
    url = f"{BASE_URL}/{doi}"
    try:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()["message"]
    except RequestException as e:
        logger.error("Error looking up DOI %s: %s", doi, e)
        return None


@lru_cache(maxsize=None)
def search_by_title(title: str, rows: int = 1) -> Union[Dict[str, Any], None]:
    """Reverse lookup via title search."""
    logger.info("crossref title %s", title)
    params = {"query.title": title, "rows": rows}
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        return items[0] if items else None
    except RequestException as e:
        logger.error("Error searching title %s: %s", title, e)
        return None


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

    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("items", [])
    except RequestException as e:
        logger.error("Error in generic search: %s", e)
        return []


# # v1
# from functools import lru_cache
# import logging
# from typing import Union

# import requests

# from . import __version__ as version

# logger = logging.getLogger(__name__)

# HEADERS = {
#     "User-Agent": f"archivum/{version} (mailto:stephenjmildenhall@gmail.com)"
# }
# BASE_URL = 'https://api.crossref.org/works'

# @lru_cache
# def lookup_doi(doi: str) -> dict | None:
#     """Lookup an individual doi string."""
#     logger.info('crossref doi %s', doi)
#     url = f"{BASE_URL}/{doi}"
#     resp = requests.get(url, headers=HEADERS)
#     if resp.status_code == 200:
#         return resp.json()["message"]
#     return None


# @lru_cache
# def search_by_title(title: str, rows=1) -> dict | None:
#     """Reverse lookup via title search."""
#     logger.info('crossref title %s', title)
#     params = {"query.title": title, "rows": rows}
#     resp = requests.get(BASE_URL, params=params, headers=HEADERS)
#     if resp.status_code == 200:
#         items = resp.json()["message"]["items"]
#         return items[0] if items else None
#     return None


# @lru_cache
# def search(query=None, title=None, author=None, rows=5):
#     """Generic search: keywords, title, and/or author."""
#     logger.info('crossref search %s %s', title, author)
#     params = {"rows": rows}
#     if query:
#         params["query"] = query
#     if author:
#         params["query.author"] = author
#     if title:
#         params["query.title"] = title
#     resp = requests.get(BASE_URL, params=params, headers=HEADERS)
#     if resp.status_code == 200:
#         return resp.json()["message"]["items"]
#     return []
