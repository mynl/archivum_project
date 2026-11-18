"""
The DOI check made when a reference is added on the Ingest page.

The entry about to be committed is the one checked, never the suggestion it
began as: an entry pasted over the suggestion may have a different DOI, or a
DOI that belongs to another work. One to three requests, each tried once with
a short timeout, sending only the DOI. The OpenAlex answer that confirms the
DOI also has the citation count, which is stored once the reference has a tag.

No Flask and no ``Library`` here: the Ingest routes call :func:`check_entry_doi`
before the import and :func:`record_ingest_check` after it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .. import egress
from ..bibtex import bibtex_to_dict
from . import journal, sources
from .doi import (
    ATTENTION, MISMATCH, NOT_COMPARED, UNRESOLVED, VERIFIED, WEAK_MATCH, YEAR_DIFFERS,
    classify_stored_doi, doi_key, match, normalize_doi,
)

logger = logging.getLogger(__name__)

NO_DOI = "no doi"
NOT_A_DOI = "not a doi"
NOT_CHECKED = "not checked"
# only a full match is added without a question
CONFIRM_STATUSES = (YEAR_DIFFERS, WEAK_MATCH, MISMATCH, UNRESOLVED, NOT_COMPARED, NOT_A_DOI)
# seconds for each request; the person is waiting on the Import button
TIMEOUT_SECONDS = 10
RUN_ID = "ingest"
SOURCE_NAMES = {"openalex": "OpenAlex", "crossref": "Crossref", "doi.org": "doi.org"}
WARNINGS = {
    YEAR_DIFFERS: "The title and first author match, but the years are more than one apart.",
    WEAK_MATCH: "The title is only close to the one recorded for this DOI.",
    MISMATCH: "This DOI is recorded for another work, by title or first author.",
    UNRESOLVED: "doi.org has no such DOI.",
    NOT_COMPARED: "The DOI exists, but neither OpenAlex nor Crossref has a record to compare it with.",
    NOT_A_DOI: "The doi field is not a DOI.",
}


@dataclass(frozen=True)
class IngestDoiCheck:
    """
    What the sources say of the DOI in an entry about to be added.

    Attributes
    ----------
    doi : str
        The entry's DOI in normal form; the text as typed for ``not a doi``;
        empty when the entry has none.
    status : str
        ``verified``, ``year differs``, ``weak match``, ``mismatch``,
        ``unresolved``, ``not compared``, ``not a doi``, ``no doi`` or
        ``not checked``.
    source : str
        ``openalex``, ``crossref`` or ``doi.org``: where the status comes from.
    entry, record : dict
        The entry's and the source's ``title``, ``author`` and ``year``
        (``container`` too for the source), shown side by side in a warning.
    score : float or None
        The title similarity, 0 to 1, when there is a record.
    count : int or None
        OpenAlex's citation count for the DOI.
    openalex_id : str
        The OpenAlex work id that goes with the count.
    error : str
        Why the sources could not be asked, for ``not checked``.
    observations : tuple of dict
        The journal lines of the answers received, without a tag.
    """

    doi: str = ""
    status: str = NO_DOI
    source: str = ""
    entry: dict = field(default_factory=dict)
    record: dict = field(default_factory=dict)
    score: float | None = None
    count: int | None = None
    openalex_id: str = ""
    error: str = ""
    observations: tuple = ()

    @property
    def needs_confirmation(self) -> bool:
        """Whether the reference is held until the user confirms the DOI."""
        return self.status in CONFIRM_STATUSES

    @property
    def warning(self) -> str:
        """One sentence saying what is wrong, for a status that needs confirmation."""
        return WARNINGS.get(self.status, "")

    @property
    def source_name(self) -> str:
        """The source as written in a message."""
        return SOURCE_NAMES.get(self.source, self.source)


class _OneAttempt:
    """
    An egress client that tries each request once, with a short timeout.

    The ``sources`` functions use the client's defaults, which in a DOI audit
    run mean retries with backoff for minutes. Here a person is waiting.
    """

    def __init__(self, client: egress.Egress):
        self._client = client

    def get(self, url, **kwargs):
        return self._client.get(url, **{**kwargs, "retry": False, "timeout": TIMEOUT_SECONDS})

    def request(self, method, url, **kwargs):
        return self._client.request(method, url, **{**kwargs, "retry": False, "timeout": TIMEOUT_SECONDS})


def entry_fields(bibtex_text: str) -> dict:
    """Return the fields of the first entry of ``bibtex_text``; empty when it does not parse."""
    try:
        entries = bibtex_to_dict(bibtex_text or "")
    except Exception:
        return {}
    return next(iter(entries.values()), {}) if entries else {}


def check_entry_doi(bibtex_text: str, client: egress.Egress) -> IngestDoiCheck:
    """
    Check the DOI of the entry about to be added against the sources.

    Parameters
    ----------
    bibtex_text : str
        The BibTeX being committed.
    client : egress.Egress
        The client for interactive lookups, :func:`egress.default`.

    Returns
    -------
    IngestDoiCheck

    Notes
    -----
    OpenAlex is asked first, since its answer has the citation count; Crossref
    where OpenAlex has no such work; ``doi.org`` where neither has, to tell a
    DOI registered elsewhere (``not compared``) from one that does not exist
    (``unresolved``). An entry with no DOI, or with a ``doi`` field that is
    not a DOI, sends nothing. A request that fails ends the check as
    ``not checked``: the import is not held for a source that is down.
    """
    fields = entry_fields(bibtex_text)
    raw = str(fields.get("doi") or "").strip()
    entry = {"title": fields.get("title"), "author": fields.get("author"), "year": fields.get("year")}
    if not raw:
        return IngestDoiCheck(entry=entry)
    if classify_stored_doi(raw) == ATTENTION:
        return IngestDoiCheck(doi=raw, status=NOT_A_DOI, entry=entry)

    doi = normalize_doi(raw)
    key = doi_key(doi)
    once = _OneAttempt(client)
    observations: list[dict] = []

    def observed(source: str, record: dict | None) -> None:
        base = {"run_id": RUN_ID, "source": source, "query_kind": "doi", "query": key}
        observations.append({**base, "status": journal.OK, **record} if record is not None
                            else {**base, "status": journal.NOT_FOUND})

    def result(status: str, source: str, record: dict | None = None, **extra) -> IngestDoiCheck:
        shown = {} if record is None else {
            "title": record.get("title"), "author": record.get("first_author"), "year": record.get("year"),
            "container": record.get("container")}
        return IngestDoiCheck(doi=doi, status=status, source=source, entry=entry, record=shown,
                              observations=tuple(observations), **extra)

    try:
        record = sources.openalex_work(once, f"doi:{key}")
        observed("openalex", record)
        if record is not None:
            scored = match(entry, record)
            return result(scored.cls, "openalex", record, score=scored.title,
                          count=record.get("cited_by_count"), openalex_id=record.get("source_id") or "")
        record = sources.crossref_work(once, key)
        observed("crossref", record)
        if record is not None:
            scored = match(entry, record)
            return result(scored.cls, "crossref", record, score=scored.title)
        exists = sources.doi_exists(once, key)
        observed("doi.org", {} if exists else None)
        return result(NOT_COMPARED if exists else UNRESOLVED, "doi.org")
    except (egress.EgressFailed, egress.HostStopped, egress.Stopped, egress.EgressRefused, sources.SourceError) as e:
        logger.warning("Ingest DOI check of %s not completed: %s", doi, e)
        return result(NOT_CHECKED, "", error=str(e))


def record_ingest_check(library_path: Path, tag: str, check: IngestDoiCheck) -> None:
    """
    Store what the check learned, under the tag the import allocated.

    Parameters
    ----------
    library_path : Path
        The library folder.
    tag : str
        The new reference's tag.
    check : IngestDoiCheck
        From :func:`check_entry_doi`, before the import.

    Notes
    -----
    The answers go to ``observations.jsonl`` in the form a Check DOIs run
    writes, so the Audit page shows the reference as checked and the next run
    skips it while the answer is fresh. A count goes to ``counts.jsonl`` and
    ``cites.feather`` is rebuilt. With no answers nothing is written.
    """
    if not check.observations:
        return
    journal.append_lines(library_path, journal.OBSERVATIONS, [{**o, "tag": tag} for o in check.observations])
    if check.count is not None:
        journal.append_lines(library_path, journal.COUNTS, [
            {"tag": tag, "source": "openalex", "source_id": check.openalex_id, "doi": doi_key(check.doi),
             "count": int(check.count)}])
        journal.derive_cites(library_path)


def result_sentence(check: IngestDoiCheck) -> str:
    """
    The DOI part of the Ingest result banner, as plain text.

    Returns
    -------
    str
        Empty for an entry with no DOI: no DOI, no check, no count.
    """
    if check.status == NO_DOI:
        return ""
    if check.status == NOT_CHECKED:
        return f"DOI not checked: {check.error}. Check DOIs on the Audit page will pick it up."
    if check.status == VERIFIED:
        text = f"DOI verified at {check.source_name}."
    else:
        text = f"DOI kept at your word ({check.status})."
    if check.count is not None:
        times = "once" if check.count == 1 else f"{check.count:,d} times"
        return f"{text} Cited {times}{'' if check.status == VERIFIED else ', for that DOI'}."
    if check.status == VERIFIED:
        return f"{text} OpenAlex has no record, so no citation count."
    return f"{text} No citation count."
