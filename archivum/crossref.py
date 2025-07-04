"""
Code for interacting with the crossref api.

With GPT.
"""

from typing import Union

import requests

from . import __version__ as version

HEADERS = {
    "User-Agent": f"archivum/{version} (mailto:stephenjmildenhall@gmail.com)"
}
BASE_URL = 'https://api.crossref.org/works'


def lookup_doi(doi: str) -> dict | None:
    """Lookup an individual doi string."""
    url = f"{BASE_URL}/{doi}"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json()["message"]
    return None


def search_by_title(title: str, rows=1) -> dict | None:
    """Reverse lookup via title search."""
    params = {"query.title": title, "rows": rows}
    resp = requests.get(BASE_URL, params=params, headers=HEADERS)
    if resp.status_code == 200:
        items = resp.json()["message"]["items"]
        return items[0] if items else None
    return None


def search(query=None, title=None, author=None, rows=5):
    """Generic search: keywords, title, and/or author."""
    params = {"rows": rows}
    if query:
        params["query"] = query
    if author:
        params["query.author"] = author
    if title:
        params["query.title"] = title
    resp = requests.get(BASE_URL, params=params, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json()["message"]["items"]
    return []

