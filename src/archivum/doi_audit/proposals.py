"""
What the Audit page shows for a subset: the summary and the rows for review.

Computed from the references and the journal, with no network call.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from . import journal
from .changesets import cell_value
from .doi import (
    ATTENTION, MALFORMED, MISMATCH, NOT_CHECKED, NOT_COMPARED, SHARED, UNRESOLVED, VERIFIED, WEAK_MATCH,
    YEAR_DIFFERS, classify_stored_doi, doi_key, match, normalize_doi,
)

# class -> label, in the order the page offers them
LOCAL_CLASSES = {MALFORMED: "Malformed", ATTENTION: "Needs attention"}
CHECK_CLASSES = {
    UNRESOLVED: "Unresolved", MISMATCH: "Mismatch", WEAK_MATCH: "Weak match", YEAR_DIFFERS: "Year differs",
    SHARED: "Shared", NOT_COMPARED: "Exists, not compared", VERIFIED: "Verified", NOT_CHECKED: "Not checked",
}
# check classes whose rows can be ticked: the DOI is replaced by hand, or cleared
CHECK_COMMIT_CLASSES = (UNRESOLVED, MISMATCH, WEAK_MATCH)
CHECK_NOTES = {
    UNRESOLVED: "doi.org has no such DOI; replace it or clear it",
    MISMATCH: "the DOI is another work's, by title or first author",
    WEAK_MATCH: "the title is only close; compare the two",
    YEAR_DIFFERS: "the DOI is right; the years are more than one apart; correct the year in Edit if it is wrong",
    NOT_COMPARED: "the DOI exists, but neither OpenAlex nor Crossref has a record to compare",
    VERIFIED: "",
    NOT_CHECKED: "run Check DOIs",
}


@dataclass(frozen=True)
class Proposal:
    """
    One row of the review table.

    Attributes
    ----------
    tag, field : str
        The cell.
    cls : str
        The class, such as ``malformed``.
    current : str or None
        The value in the library, ``None`` when missing.
    proposed : str or None
        The value offered; ``None`` for a row shown for information only, and
        empty for a checked DOI, where the replacement is typed by hand and
        empty clears the DOI.
    source : str
        Where the proposal comes from.
    note : str
        A line of explanation for the reader.
    score : float or None
        The title similarity with the source record, for a checked DOI.
    library, record : dict
        The library's and the source's title, first author and year, shown
        side by side for a checked DOI.
    shared_with : tuple of str
        The other tags with the same DOI.
    """

    tag: str
    field: str
    cls: str
    current: str | None
    proposed: str | None
    source: str = "local"
    note: str = ""
    score: float | None = None
    library: dict = field(default_factory=dict)
    record: dict = field(default_factory=dict)
    shared_with: tuple = ()

    @property
    def key(self) -> str:
        """The row's identity in a form: ``tag|field``."""
        return f"{self.tag}|{self.field}"


def subset_rows(ref_df: pd.DataFrame, tags: list[str] | None) -> pd.DataFrame:
    """Return the references in ``tags``, in library order; all of them for ``None``."""
    if tags is None:
        return ref_df
    return ref_df[ref_df["tag"].isin(set(tags))]


def _dois(rows: pd.DataFrame) -> list[tuple[str, str]]:
    """``(tag, doi)`` for each reference with a DOI that is not blank."""
    if "doi" not in rows.columns:
        return []
    pairs = ((tag, cell_value(doi)) for tag, doi in zip(rows["tag"], rows["doi"]))
    return [(tag, doi) for tag, doi in pairs if doi and doi.strip()]


def summarize(rows: pd.DataFrame) -> dict:
    """
    Count what the summary strip shows for a subset.

    Returns
    -------
    dict
        ``references``, ``with_doi``, ``without_doi``, ``malformed``,
        ``attention``, and ``shared``: the references whose DOI, compared
        without case, is on another reference of the subset.
    """
    dois = _dois(rows)
    classes = Counter(classify_stored_doi(doi) for _, doi in dois)
    keys = Counter(doi_key(doi) for _, doi in dois)
    return {
        "references": len(rows),
        "with_doi": len(dois),
        "without_doi": len(rows) - len(dois),
        "malformed": classes[MALFORMED],
        "attention": classes[ATTENTION],
        "shared": sum(n for n in keys.values() if n > 1),
    }


def local_proposals(rows: pd.DataFrame, cls: str, rejected: set[tuple] = frozenset()) -> list[Proposal]:
    """
    The review rows of one local class.

    Parameters
    ----------
    rows : pandas.DataFrame
        The references of the subset.
    cls : str
        ``malformed`` or ``attention``.
    rejected : set of tuple, optional
        ``(tag, field, old, proposed)`` of the proposals already rejected,
        which are left out.

    Returns
    -------
    list of Proposal
        In library order. A ``malformed`` row proposes the normalized DOI; an
        ``attention`` row has no proposal.
    """
    found = []
    for tag, doi in _dois(rows):
        if classify_stored_doi(doi) != cls:
            continue
        if cls == MALFORMED:
            proposal = Proposal(tag, "doi", cls, doi, normalize_doi(doi), note="prefix or punctuation removed")
        else:
            proposal = Proposal(tag, "doi", cls, doi, None, note="not a DOI; correct it in Edit")
        if (proposal.tag, proposal.field, proposal.current, proposal.proposed) not in rejected:
            found.append(proposal)
    return found


def _shown(record: dict) -> dict:
    """The fields of a source record shown beside the library's."""
    return {"title": record.get("title"), "author": record.get("first_author"), "year": record.get("year"),
            "container": record.get("container")}


def checked_dois(rows: pd.DataFrame, all_rows: pd.DataFrame, latest: dict,
                 rejected: set[tuple] = frozenset()) -> list[Proposal]:
    """
    Class every usable DOI of a subset from the answers in the journal.

    Parameters
    ----------
    rows : pandas.DataFrame
        The references of the subset.
    all_rows : pandas.DataFrame
        Every reference, to find the tags sharing a DOI.
    latest : dict
        From :func:`journal.latest_observations`.
    rejected : set of tuple, optional
        ``(tag, field, old, proposed)`` of rows already rejected. A rejected
        ``unresolved``, ``mismatch`` or ``weak match`` row is one the user has
        looked at and is leaving as it is; it is left out.

    Returns
    -------
    list of Proposal
        One per reference with a usable DOI, in library order, with its class:
        ``verified``, ``year differs``, ``weak match`` or ``mismatch`` from the
        better of the OpenAlex and Crossref records; ``not compared`` when
        neither has the DOI but ``doi.org`` does; ``unresolved`` when
        ``doi.org`` does not; ``not checked`` when the sources have not been
        asked. ``shared_with`` lists the other tags with the DOI; sharing is
        shown beside the class, not instead of it.
    """
    sharing: dict[str, list[str]] = {}
    for tag, doi in _dois(all_rows):
        if classify_stored_doi(doi) != ATTENTION:
            sharing.setdefault(doi_key(doi), []).append(tag)

    found = []
    for row in rows.to_dict("records"):
        doi = cell_value(row.get("doi"))
        if not doi or not doi.strip() or classify_stored_doi(doi) == ATTENTION:
            continue
        tag, key = row["tag"], doi_key(doi)
        library = {"title": cell_value(row.get("title")), "author": cell_value(row.get("author")),
                   "year": cell_value(row.get("year"))}
        openalex = latest.get((tag, "openalex", "doi", key))
        crossref = latest.get((tag, "crossref", "doi", key))
        doi_org = latest.get((tag, "doi.org", "doi", key))
        records = [r for r in (openalex, crossref) if r and r.get("status") == journal.OK]

        score, record, source = None, {}, ""
        if records:
            best = max(records, key=lambda r: match(library, r).title)
            scored = match(library, best)
            cls, score, record, source = scored.cls, scored.title, _shown(best), best["source"]
        elif doi_org and doi_org.get("status") == journal.OK:
            cls, source = NOT_COMPARED, "doi.org"
        elif doi_org and doi_org.get("status") == journal.NOT_FOUND:
            cls, source = UNRESOLVED, "doi.org"
        else:
            cls = NOT_CHECKED

        proposed = "" if cls in CHECK_COMMIT_CLASSES else None
        if proposed is not None and (tag, "doi", doi, proposed) in rejected:
            continue
        found.append(Proposal(
            tag, "doi", cls, doi, proposed, source=source, note=CHECK_NOTES[cls], score=score,
            library=library, record=record, shared_with=tuple(t for t in sharing.get(key, []) if t != tag),
        ))
    return found


def check_counts(checked: list[Proposal]) -> dict[str, int]:
    """Count the rows of each check class; ``shared`` counts the rows whose DOI is on another tag."""
    counts = Counter(p.cls for p in checked)
    counts[SHARED] = sum(1 for p in checked if p.shared_with)
    return {cls: counts[cls] for cls in CHECK_CLASSES}


def check_rows(checked: list[Proposal], cls: str) -> list[Proposal]:
    """The rows of one check class; for ``shared``, the rows whose DOI is on another tag."""
    if cls == SHARED:
        return [p for p in checked if p.shared_with]
    return [p for p in checked if p.cls == cls]
