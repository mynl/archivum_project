"""
The public sources, each asked through ``egress`` and answered as plain records.

A record is a dict with the fields of an observation line: ``source``,
``source_id``, ``doi``, ``title``, ``year``, ``first_author``, ``container``,
``volume``, ``issue``, ``pages``, ``type``, ``cited_by_count``.
"""

from __future__ import annotations

from urllib.parse import quote

from .. import egress
from .doi import doi_key

CROSSREF_WORKS = "https://api.crossref.org/works"
DOI_ORG = "https://doi.org"
OPENALEX_WORKS = "https://api.openalex.org/works"
# OpenAlex allows 100 values in one filter
OPENALEX_BATCH = 100
_OPENALEX_FIELDS = "id,doi,title,publication_year,cited_by_count,type,authorships,primary_location,biblio"


class SourceError(RuntimeError):
    """A source answered with a status that is neither a result nor a not-found."""


def can_batch(doi: str) -> bool:
    """Whether a DOI can go in an OpenAlex filter list, where ``|`` and ``,`` are separators."""
    return "|" not in doi and "," not in doi


def _openalex_record(work: dict) -> dict:
    """Reduce an OpenAlex work to a record."""
    authors = work.get("authorships") or []
    first = (authors[0].get("author") or {}).get("display_name") if authors else None
    location = work.get("primary_location") or {}
    biblio = work.get("biblio") or {}
    first_page, last_page = biblio.get("first_page"), biblio.get("last_page")
    pages = first_page if not last_page or last_page == first_page else f"{first_page}--{last_page}"
    return {
        "source": "openalex",
        "source_id": (work.get("id") or "").rsplit("/", 1)[-1],
        "doi": doi_key(work["doi"]) if work.get("doi") else None,
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "first_author": first,
        "container": (location.get("source") or {}).get("display_name"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "pages": pages,
        "type": work.get("type"),
        "cited_by_count": work.get("cited_by_count"),
    }


def openalex_works_by_doi(client: egress.Egress, dois: list[str]) -> dict[str, dict]:
    """
    Ask OpenAlex for the works with the given DOIs, in one request.

    Parameters
    ----------
    client : egress.Egress
        The paced client of the run.
    dois : list of str
        At most :data:`OPENALEX_BATCH` DOIs in compared form (:func:`doi_key`),
        each passing :func:`can_batch`.

    Returns
    -------
    dict
        ``doi_key -> record`` for the DOIs OpenAlex has. A DOI that is absent
        from the result is one OpenAlex does not have. Where OpenAlex has
        several works for one DOI the most cited is kept.

    Raises
    ------
    SourceError
        If the status is not 200.
    """
    if len(dois) > OPENALEX_BATCH:
        raise ValueError(f"At most {OPENALEX_BATCH} DOIs in one request")
    response = client.get(
        OPENALEX_WORKS,
        params={"filter": "doi:" + "|".join(dois), "per_page": OPENALEX_BATCH, "select": _OPENALEX_FIELDS},
        kind=egress.SEARCH,
    )
    if response.status_code != 200:
        raise SourceError(f"OpenAlex answered HTTP {response.status_code}")
    found: dict[str, dict] = {}
    for work in response.json().get("results", []):
        record = _openalex_record(work)
        key = record["doi"]
        if key and (key not in found or (record["cited_by_count"] or 0) > (found[key]["cited_by_count"] or 0)):
            found[key] = record
    return found


def openalex_work(client: egress.Egress, identifier: str) -> dict | None:
    """
    Ask OpenAlex for one work.

    Parameters
    ----------
    client : egress.Egress
        The paced client of the run.
    identifier : str
        An OpenAlex work id (``W2741809807``) or ``doi:<doi>``.

    Returns
    -------
    dict or None
        The record, or ``None`` when OpenAlex has no such work.

    Raises
    ------
    SourceError
        If the status is neither 200 nor 404.
    """
    response = client.get(f"{OPENALEX_WORKS}/{identifier}", params={"select": _OPENALEX_FIELDS}, kind=egress.SINGLE)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SourceError(f"OpenAlex answered HTTP {response.status_code}")
    return _openalex_record(response.json())


def _doi_path(doi: str) -> str:
    """A DOI as a URL path segment: ``/`` and the common DOI punctuation kept, the rest encoded."""
    return quote(doi, safe="/:;()")


def _crossref_record(work: dict) -> dict:
    """Reduce a Crossref work to a record."""
    def first(key):
        values = work.get(key) or []
        return values[0] if values else None

    authors = work.get("author") or []
    author = authors[0] if authors else {}
    name = " ".join(part for part in (author.get("given"), author.get("family")) if part) or author.get("name")
    parts = (((work.get("issued") or {}).get("date-parts") or [[None]])[0]) or [None]
    relation = work.get("relation") or {}
    return {
        "source": "crossref",
        "source_id": work.get("DOI"),
        "doi": doi_key(work["DOI"]) if work.get("DOI") else None,
        "title": first("title"),
        "year": parts[0],
        "first_author": name,
        "container": first("container-title"),
        "volume": work.get("volume"),
        "issue": work.get("issue"),
        "pages": (work.get("page") or "").replace("-", "--") or None,
        "type": work.get("type"),
        "cited_by_count": work.get("is-referenced-by-count"),
        "is_preprint_of": [doi_key(r["id"]) for r in relation.get("is-preprint-of", []) if r.get("id-type") == "doi"],
    }


def crossref_work(client: egress.Egress, doi: str) -> dict | None:
    """
    Ask Crossref for the work with a DOI.

    Parameters
    ----------
    client : egress.Egress
        The paced client of the run.
    doi : str
        The DOI in compared form.

    Returns
    -------
    dict or None
        The record, or ``None`` when Crossref has no such DOI. That is not
        proof the DOI does not exist: Crossref has only the DOIs registered
        with it, and not, for instance, DataCite's.

    Raises
    ------
    SourceError
        If the status is neither 200 nor 404.
    """
    response = client.get(f"{CROSSREF_WORKS}/{_doi_path(doi)}", kind=egress.SINGLE)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SourceError(f"Crossref answered HTTP {response.status_code}")
    return _crossref_record(response.json()["message"])


def doi_exists(client: egress.Egress, doi: str) -> bool:
    """
    Ask ``doi.org`` whether a DOI is registered anywhere.

    Parameters
    ----------
    client : egress.Egress
        The paced client of the run.
    doi : str
        The DOI in compared form.

    Returns
    -------
    bool
        True for a redirect, which is how ``doi.org`` answers a DOI it knows
        (the redirect is not followed); False for a 404.

    Raises
    ------
    SourceError
        For any other status.
    """
    response = client.request("HEAD", f"{DOI_ORG}/{_doi_path(doi)}", kind=egress.SINGLE)
    if response.status_code in (301, 302, 303, 307, 308):
        return True
    if response.status_code == 404:
        return False
    raise SourceError(f"doi.org answered HTTP {response.status_code}")
