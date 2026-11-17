"""
What the Audit page shows for a subset: the summary and the rows for review.

Computed from the references alone, with no network call.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd

from .changesets import cell_value
from .doi import ATTENTION, MALFORMED, classify_stored_doi, doi_key, normalize_doi

# class -> label, in the order the page offers them
LOCAL_CLASSES = {MALFORMED: "Malformed", ATTENTION: "Needs attention"}


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
        The value offered; ``None`` for a row shown for information only.
    source : str
        Where the proposal comes from.
    note : str
        A line of explanation for the reader.
    """

    tag: str
    field: str
    cls: str
    current: str | None
    proposed: str | None
    source: str = "local"
    note: str = ""

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
