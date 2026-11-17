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
from ..bibtex import dict_to_bibtex
from .doi import (
    AMBIGUOUS, ATTENTION, CROSSREF_TYPES, MALFORMED, MISMATCH, NOT_ASKED, NOT_CHECKED, NOT_COMPARED, NOT_FOUND,
    NOT_PUBLISHED, NOT_SEARCHED, PROPOSED_EXACT, PROPOSED_STRONG, PUBLISHED_OTHER_YEAR, PUBLISHED_POSSIBLE,
    PUBLISHED_SAME_YEAR, SHARED, STRONG_TITLE, UNRESOLVED, VERIFIED, WEAK_MATCH, WORK_NO_DOI, YEAR_DIFFERS,
    classify_candidates, classify_stored_doi, doi_key, fold, is_preprint, match, normalize_doi,
)

# class -> label, in the order the page offers them
LOCAL_CLASSES = {MALFORMED: "Malformed", ATTENTION: "Needs attention"}
CHECK_CLASSES = {
    UNRESOLVED: "Unresolved", MISMATCH: "Mismatch", WEAK_MATCH: "Weak match", YEAR_DIFFERS: "Year differs",
    SHARED: "Shared", NOT_COMPARED: "Exists, not compared", VERIFIED: "Verified", NOT_CHECKED: "Not checked",
}
# check classes whose rows can be ticked: the DOI is replaced by hand, or cleared
CHECK_COMMIT_CLASSES = (UNRESOLVED, MISMATCH, WEAK_MATCH)
SEARCH_CLASSES = {
    PROPOSED_EXACT: "Exact match", PROPOSED_STRONG: "Strong match", AMBIGUOUS: "Ambiguous",
    WORK_NO_DOI: "Work without a DOI", NOT_FOUND: "Not found", NOT_SEARCHED: "Not searched",
}
# search classes whose rows can be ticked to commit a DOI
SEARCH_COMMIT_CLASSES = (PROPOSED_EXACT, PROPOSED_STRONG, AMBIGUOUS)
SEARCH_NOTES = {
    PROPOSED_EXACT: "same title and first author, years agree",
    PROPOSED_STRONG: "title and first author match",
    AMBIGUOUS: "close, or more than one candidate; pick a candidate's DOI or reject",
    WORK_NO_DOI: "OpenAlex has the work but no DOI for it; accept it to attach its citation count",
    NOT_FOUND: "nothing close at Crossref or OpenAlex",
    NOT_SEARCHED: "run Find missing DOIs",
}
PUBLISHED_CLASSES = {
    PUBLISHED_SAME_YEAR: "Published, same year", PUBLISHED_OTHER_YEAR: "Published, different year",
    PUBLISHED_POSSIBLE: "Possibly published", NOT_PUBLISHED: "No published version", NOT_ASKED: "Not asked",
}
# the fields a published version fills in on the preprint's own reference, as one unit
BACKFILL_FIELDS = ("doi", "type", "journal", "volume", "number", "pages")
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
    candidates : tuple of dict
        For a searched reference, the candidates at or above the weak
        threshold, best first: ``doi``, ``title``, ``author``, ``year``,
        ``container``, ``source``, ``score``, ``preprint``.
    work_id : str
        For a ``work without a DOI`` row, the OpenAlex work id.
    work_accepted : bool
        Whether that work has been accepted for the reference.
    unit : tuple of tuple
        For a published version in the preprint's year, the cells to fill in
        as ``(field, old, new)``, accepted or rejected together.
    bibtex : str
        For a published version in a different year, the entry proposed as a
        new reference.
    published_doi : str
        The DOI of the published version.
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
    candidates: tuple = ()
    work_id: str = ""
    work_accepted: bool = False
    unit: tuple = ()
    bibtex: str = ""
    published_doi: str = ""

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


def _candidate_doi(record: dict) -> str | None:
    """A candidate's DOI as its source wrote it: Crossref keeps the registrant's capitals."""
    if record.get("source") == "crossref" and record.get("source_id"):
        return record["source_id"]
    return record.get("doi")


def searched_references(rows: pd.DataFrame, latest: dict, rejected: set[tuple] = frozenset(),
                        accepted_works: dict[str, str] | None = None) -> list[Proposal]:
    """
    Class every reference of a subset that has no DOI, from the searches in the journal.

    Parameters
    ----------
    rows : pandas.DataFrame
        The references of the subset.
    latest : dict
        From :func:`journal.latest_observations`.
    rejected : set of tuple, optional
        ``(tag, field, old, proposed)`` of rows already rejected, which are
        left out. For a work with no DOI the field is ``work`` and the
        proposal its OpenAlex id.
    accepted_works : dict, optional
        ``tag -> OpenAlex work id`` already accepted, from
        :func:`journal.accepted_works`.

    Returns
    -------
    list of Proposal
        One per reference with a title and no DOI, in library order, classed
        by :func:`doi.classify_candidates` on the Crossref and OpenAlex
        candidates together; ``not searched`` when neither has been asked.
        ``proposed`` is the DOI for the two ``proposed`` classes, empty for
        ``ambiguous`` (one is picked by hand), and ``None`` otherwise.
    """
    accepted_works = accepted_works or {}
    found = []
    for row in rows.to_dict("records"):
        doi = cell_value(row.get("doi"))
        title = cell_value(row.get("title"))
        if (doi and doi.strip()) or not title or not fold(title):
            continue
        tag, key = row["tag"], fold(title)
        library = {"title": title, "author": cell_value(row.get("author")), "year": cell_value(row.get("year"))}
        searches = [latest.get((tag, source, "title", key)) for source in ("crossref", "openalex")]
        if not any(searches):
            found.append(Proposal(tag, "doi", NOT_SEARCHED, doi, None, source="", note=SEARCH_NOTES[NOT_SEARCHED],
                                  library=library))
            continue
        candidates = [c for s in searches if s for c in (s.get("candidates") or [])]
        cls, record, scored = classify_candidates(library, candidates)
        shown = tuple(
            {"doi": _candidate_doi(c), "title": c.get("title"), "author": c.get("first_author"), "year": c.get("year"),
             "container": c.get("container"), "source": c.get("source"), "score": m.title, "preprint": is_preprint(c)}
            for c, m in scored
        )
        if cls == WORK_NO_DOI:
            work_id = record["source_id"]
            if (tag, "work", None, work_id) in rejected:
                continue
            accepted = accepted_works.get(tag) == work_id
            note = "accepted; its citation count is collected with the others" if accepted else SEARCH_NOTES[cls]
            found.append(Proposal(tag, "work", cls, None, None, source=record["source"], note=note,
                                  library=library, candidates=shown, work_id=work_id, work_accepted=accepted))
            continue
        if cls in (PROPOSED_EXACT, PROPOSED_STRONG):
            proposed = _candidate_doi(record)
        else:
            proposed = "" if cls == AMBIGUOUS else None
        if proposed is not None and (tag, "doi", doi, proposed) in rejected:
            continue
        found.append(Proposal(tag, "doi", cls, doi, proposed, source=record["source"] if record else "",
                              note=SEARCH_NOTES[cls], score=scored[0][1].title if scored else None,
                              library=library, candidates=shown))
    return found


def search_counts(searched: list[Proposal]) -> dict[str, int]:
    """Count the rows of each search class."""
    counts = Counter(p.cls for p in searched)
    return {cls: counts[cls] for cls in SEARCH_CLASSES}


def _published_values(record: dict, current_type: str | None) -> dict:
    """
    The values a Crossref record of a published version gives for the backfill fields.

    The entry type follows the record (:data:`doi.CROSSREF_TYPES`), except
    that a ``book`` is never given another type.
    """
    values = {
        "doi": record.get("source_id") or record.get("doi"),
        "journal": record.get("container"),
        "volume": record.get("volume"),
        "number": record.get("issue"),
        "pages": record.get("pages"),
    }
    mapped = CROSSREF_TYPES.get(record.get("type") or "")
    if mapped and (current_type or "").lower() != "book":
        values["type"] = mapped
    return {field: str(value) for field, value in values.items() if value not in (None, "")}


def published_versions(rows: pd.DataFrame, latest: dict, settled: set[tuple] = frozenset()) -> list[Proposal]:
    """
    Class every preprint of a subset by what is known of its published version.

    A preprint is a reference with an arXiv identifier and no journal.

    Parameters
    ----------
    rows : pandas.DataFrame
        The references of the subset.
    latest : dict
        From :func:`journal.latest_observations`.
    settled : set of tuple, optional
        ``(tag, doi_key)`` of published versions already imported or
        rejected, from :func:`journal.settled_published`; left out.

    Returns
    -------
    list of Proposal
        In library order. The published version is certain when a DOI is
        known for it (the one the authors gave arXiv, or the reference's own)
        and Crossref's record of that DOI matches the reference by title and
        first author. Then, if the record's year is the reference's, the class
        is ``published, same year`` and ``unit`` has the cells to fill in on
        the reference itself; otherwise ``published, different year`` and
        ``bibtex`` is a new reference for the published version, built from
        the library's author and title and the record's year, venue and DOI.
        Anything weaker (a journal reference with no DOI, a DOI Crossref does
        not have, a record that does not match) is ``possibly published`` and
        proposes nothing.

    Notes
    -----
    Neither case writes the tag, author or year of the preprint's reference:
    the first because they already agree, the second because the reference is
    left as it is and a new one is added.
    """
    from .runner import preprint_reference, published_doi

    found = []
    for row in rows.to_dict("records"):
        reference = preprint_reference(row)
        if reference is None:
            continue
        tag = reference["tag"]
        library = {"title": reference["title"], "author": reference["author"], "year": reference["year"]}
        base = {"tag": tag, "field": "published", "current": None, "proposed": None, "library": library}
        arxiv = latest.get((tag, "arxiv", "arxiv_id", reference["arxiv"]))
        if arxiv is None:
            found.append(Proposal(cls=NOT_ASKED, note="run Find published versions", **base))
            continue
        doi = published_doi(reference, latest)
        journal_ref = arxiv.get("journal_ref") if arxiv.get("status") == journal.OK else None
        if not doi:
            if journal_ref:
                found.append(Proposal(cls=PUBLISHED_POSSIBLE, source="arxiv", **base,
                                      note=f"arXiv gives a journal reference and no DOI: {journal_ref}. Find missing DOIs may find it."))
            else:
                found.append(Proposal(cls=NOT_PUBLISHED, source="arxiv", note="arXiv knows of no published version", **base))
            continue
        if (tag, doi) in settled:
            continue
        crossref = latest.get((tag, "crossref", "doi", doi))
        if crossref is None:
            found.append(Proposal(cls=NOT_ASKED, note="run Find published versions", published_doi=doi, **base))
            continue
        if crossref.get("status") != journal.OK:
            found.append(Proposal(cls=PUBLISHED_POSSIBLE, source="arxiv", published_doi=doi, **base,
                                  note=f"the DOI {doi} is given, but Crossref has no record of it to take the journal from"))
            continue
        scored = match(library, crossref)
        record = _shown(crossref)
        if scored.title < STRONG_TITLE or scored.surname is False:
            found.append(Proposal(cls=PUBLISHED_POSSIBLE, source="crossref", score=scored.title, record=record,
                                  published_doi=doi, note="the record of the DOI does not match the reference", **base))
            continue

        values = _published_values(crossref, reference["type"])
        if str(crossref.get("year")) == str(reference["year"] or "").strip():
            unit = tuple(
                (name, cell_value(row.get(name)), values[name]) for name in BACKFILL_FIELDS
                if name in values and name in row
                and (doi_key(cell_value(row.get(name)) or "") != doi_key(values[name]) if name == "doi"
                     else cell_value(row.get(name)) != values[name])
            )
            if unit:
                found.append(Proposal(cls=PUBLISHED_SAME_YEAR, source="crossref", score=scored.title, record=record,
                                      unit=unit, published_doi=doi, note="fills in the reference; tag, author and year stay",
                                      **{**base, "proposed": values.get("doi", doi)}))
            continue
        entry = {"type": values.get("type", "article"), "tag": "PublishedVersion", "author": reference["author"],
                 "title": reference["title"], "year": str(crossref.get("year")),
                 **{name: values[name] for name in ("journal", "volume", "number", "pages", "doi") if name in values}}
        found.append(Proposal(
            cls=PUBLISHED_OTHER_YEAR, source="crossref", score=scored.title, record=record, published_doi=doi,
            bibtex=dict_to_bibtex(entry, allowed_fields=["author", "title", "year", "journal", "volume", "number", "pages", "doi"]),
            note=f"published in {crossref.get('year')}; the preprint's reference stays, and a new one is added for the published version",
            **{**base, "proposed": values.get("doi", doi)},
        ))
    return found


def published_counts(published: list[Proposal]) -> dict[str, int]:
    """Count the rows of each published-version class."""
    counts = Counter(p.cls for p in published)
    return {cls: counts[cls] for cls in PUBLISHED_CLASSES}
