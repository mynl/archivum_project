"""
The public sources, each asked through ``egress`` and answered as plain records.

A record is a dict with the fields of an observation line: ``source``,
``source_id``, ``doi``, ``title``, ``year``, ``first_author``, ``container``,
``volume``, ``issue``, ``pages``, ``type``, ``cited_by_count``.
"""

from __future__ import annotations

from .. import egress
from .doi import doi_key

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
