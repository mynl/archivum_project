"""
DOI rules: normalization, the classes a DOI can fall into, and the match score.

Pure functions of text, with no I/O.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

from rapidfuzz import fuzz

# classes from the stored text alone
MALFORMED = "malformed"
ATTENTION = "attention"
# classes from a check against the sources
VERIFIED = "verified"
YEAR_DIFFERS = "year differs"
WEAK_MATCH = "weak match"
MISMATCH = "mismatch"
UNRESOLVED = "unresolved"
NOT_COMPARED = "not compared"
SHARED = "shared"
NOT_CHECKED = "not checked"
# classes from a title search, for a reference with no DOI
PROPOSED_EXACT = "proposed (exact)"
PROPOSED_STRONG = "proposed (strong)"
AMBIGUOUS = "ambiguous"
WORK_NO_DOI = "work without a DOI"
NOT_FOUND = "not found"
NOT_SEARCHED = "not searched"

# Provisional: to be set from the score distribution of the first full run.
STRONG_TITLE = 0.9
WEAK_TITLE = 0.6
YEAR_TOLERANCE = 1

_URL_PREFIX = re.compile(r"^https?://(?:dx\.|www\.)?doi\.org/", re.IGNORECASE)
_DOI_PREFIX = re.compile(r"^doi:\s*", re.IGNORECASE)
_ENCODED = re.compile(r"%[0-9A-Fa-f]{2}")


def normalize_doi(value: str) -> str:
    """
    Reduce a stored DOI to the bare ``10.prefix/suffix`` form.

    Parameters
    ----------
    value : str
        The DOI as stored.

    Returns
    -------
    str
        The value without surrounding whitespace, a leading
        ``http(s)://doi.org/``, ``dx.doi.org`` or ``www.doi.org`` address, a
        leading ``doi:``, URL-encoding, or trailing ``.``, ``,`` or ``;``.

    Notes
    -----
    Case is kept. DOIs compare without regard to case, and many are stored in
    the publisher's own capitals (``S0167-6687...``); use :func:`doi_key` to
    compare. The steps repeat until the value stops changing, so
    ``doi: https://doi.org/10.1/x.`` is reduced in one call.
    """
    text = str(value)
    while True:
        before = text
        text = text.strip()
        text = _URL_PREFIX.sub("", text)
        text = _DOI_PREFIX.sub("", text)
        if _ENCODED.search(text):
            text = unquote(text)
        text = text.rstrip(".,;")
        if text == before:
            return text


def doi_key(value: str) -> str:
    """Return the form two DOIs are compared in: normalized, lower case."""
    return normalize_doi(value).lower()


def classify_stored_doi(value: str) -> str | None:
    """
    Class a stored DOI from its text alone.

    Parameters
    ----------
    value : str
        The DOI as stored; not empty.

    Returns
    -------
    str or None
        :data:`MALFORMED` when normalization changes the value into a DOI,
        which is a proposal to commit; :data:`ATTENTION` when the value is not
        a DOI even after normalization (it does not start ``10.``, has no
        suffix, or has whitespace inside), which has no proposal; ``None``
        when the value is already in the normal form.
    """
    normal = normalize_doi(value)
    if not re.match(r"^10\.[^/\s]+/\S+$", normal):
        return ATTENTION
    return MALFORMED if normal != value else None


def fold(text) -> str:
    """
    Reduce text to what a comparison should see.

    LaTeX commands and braces, HTML tags and entities, accents, case and
    punctuation are removed, and whitespace is collapsed. ``{\\"O}ber die
    <i>Sache</i>`` and ``Uber die Sache`` fold to the same text.
    """
    if text is None:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text)))
    text = re.sub(r"\\[a-zA-Z]+\s*|\\.", "", text)
    # braces protect capitals inside a word ({R}isk), so they go without leaving a space
    text = text.replace("{", "").replace("}", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def plain_title(text) -> str:
    """
    A title as a search should receive it: LaTeX commands and braces and HTML tags removed.

    Case and accents written as characters are kept. ``Coherent Measures of
    {R}isk`` becomes ``Coherent Measures of Risk``; sent with its braces, a
    search engine reads ``R`` and ``isk`` as two words and finds nothing.
    """
    if text is None:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text)))
    text = re.sub(r"\\[a-zA-Z]+\s*|\\.", "", text)
    return " ".join(text.replace("{", "").replace("}", "").replace("$", "").split())


def title_similarity(a, b) -> float:
    r"""
    Similarity of two titles, in :math:`[0, 1]`.

    Notes
    -----
    Both titles are folded with :func:`fold`, then compared with the
    normalized indel similarity :math:`t = 1 - d / (|a| + |b|)`, where
    :math:`d` is the least number of single-character insertions and
    deletions turning one into the other. Equal titles give 1; an empty title
    on either side gives 0, since nothing was compared.
    """
    a, b = fold(a), fold(b)
    if not a or not b:
        return 0.0
    return fuzz.ratio(a, b) / 100.0


def first_surname(author) -> str:
    """
    The folded surname of the first author of a BibTeX ``author`` field.

    ``Wang, Ruodu and Zitikis, R.`` gives ``wang``; ``Ruodu Wang`` gives
    ``wang``; a corporate author in braces is kept whole.
    """
    if author is None:
        return ""
    text = str(author).strip()
    first = re.split(r"\s+and\s+", text, maxsplit=1)[0].strip()
    if first.startswith("{") and first.endswith("}"):
        return fold(first)
    if "," in first:
        return fold(first.split(",", 1)[0])
    words = fold(first).split()
    return words[-1] if words else ""


def surnames_agree(library_author, source_first_author) -> bool | None:
    """
    Whether the library's first author is the source's first author.

    Returns
    -------
    bool or None
        True when every word of the library surname is a word of the source's
        name, which allows ``van der Vaart`` against ``A. W. van der Vaart``
        and initials on either side; ``None`` when either side has no author,
        so nothing could be compared.
    """
    surname = first_surname(library_author).split()
    source = fold(source_first_author).split()
    if not surname or not source:
        return None
    return all(word in source for word in surname)


def year_gap(library_year, source_year) -> int | None:
    """The absolute difference of two years; ``None`` when either is not a year."""
    try:
        return abs(int(str(library_year).strip()[:4]) - int(str(source_year).strip()[:4]))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Match:
    """
    How well a source record matches a reference.

    Attributes
    ----------
    title : float
        :func:`title_similarity`.
    surname : bool or None
        :func:`surnames_agree`.
    years : int or None
        :func:`year_gap`.
    """

    title: float
    surname: bool | None
    years: int | None

    @property
    def cls(self) -> str:
        """
        The class of a DOI whose record matches this well.

        ``mismatch`` when the title similarity is under :data:`WEAK_TITLE` or
        the surnames differ; ``verified`` when it is at least
        :data:`STRONG_TITLE` and the surnames do not differ, becoming ``year
        differs`` if the years are more than :data:`YEAR_TOLERANCE` apart;
        ``weak match`` between the two thresholds. A missing author on either
        side does not count as a difference.
        """
        if self.title < WEAK_TITLE or self.surname is False:
            return MISMATCH
        if self.title < STRONG_TITLE:
            return WEAK_MATCH
        if self.years is not None and self.years > YEAR_TOLERANCE:
            return YEAR_DIFFERS
        return VERIFIED


def match(reference: dict, record: dict) -> Match:
    """
    Score a source record against a reference.

    Parameters
    ----------
    reference : dict
        The library's ``title``, ``author`` and ``year``.
    record : dict
        A source record with ``title``, ``first_author`` and ``year``.

    Returns
    -------
    Match
    """
    return Match(
        title=title_similarity(reference.get("title"), record.get("title")),
        surname=surnames_agree(reference.get("author"), record.get("first_author")),
        years=year_gap(reference.get("year"), record.get("year")),
    )


def is_preprint(record: dict) -> bool:
    """Whether a source record is a preprint: an arXiv DOI, a posted-content type, or a stated published version."""
    doi = record.get("doi") or ""
    return (doi.startswith("10.48550/") or record.get("type") in ("posted-content", "preprint")
            or bool(record.get("is_preprint_of")))


def classify_candidates(reference: dict, candidates: list[dict]) -> tuple[str, dict | None, list[tuple[dict, Match]]]:
    """
    Class the candidates a title search returned for a reference with no DOI.

    Parameters
    ----------
    reference : dict
        The library's ``title``, ``author`` and ``year``.
    candidates : list of dict
        Source records, from any of the sources.

    Returns
    -------
    tuple of (str, dict or None, list)
        The class, the record proposed (``None`` unless the class proposes
        one), and every candidate at or above :data:`WEAK_TITLE` with its
        :class:`Match`, best first.

    Notes
    -----
    A candidate qualifies when its title similarity is at least
    :data:`STRONG_TITLE` and the first author agrees. With one qualifying DOI
    the class is ``proposed (exact)`` if the folded titles are equal and the
    years agree, else ``proposed (strong)``. A journal DOI wins over a
    preprint's, so preprints are dropped when a published version qualifies.
    Of several qualifying DOIs, one whose year agrees with the reference's is
    preferred when it is the only such, which separates an article from its
    later reprint. Several qualifying DOIs still, or a best candidate between the
    thresholds, or one whose author could not be compared, is ``ambiguous``.
    A qualifying work with no DOI is ``work without a DOI``: nothing to
    commit, but its citation count can be attached. Nothing at
    :data:`WEAK_TITLE` or above is ``not found``.
    """
    scored = [(c, match(reference, c)) for c in candidates]
    scored = sorted((s for s in scored if s[1].title >= WEAK_TITLE), key=lambda s: -s[1].title)
    if not scored:
        return NOT_FOUND, None, []

    qualifying = [(c, m) for c, m in scored if m.title >= STRONG_TITLE and m.surname is True]
    with_doi: dict[str, tuple[dict, Match]] = {}
    for c, m in qualifying:
        if c.get("doi"):
            with_doi.setdefault(c["doi"], (c, m))
    published = {d: cm for d, cm in with_doi.items() if not is_preprint(cm[0])}
    chosen = published or with_doi
    if len(chosen) > 1:
        # a reprint or a later edition has the same title and author: the year tells them apart
        same_year = {d: cm for d, cm in chosen.items() if cm[1].years is not None and cm[1].years <= YEAR_TOLERANCE}
        if len(same_year) == 1:
            chosen = same_year
    if len(chosen) == 1:
        record, m = next(iter(chosen.values()))
        exact = m.title >= 1.0 and m.years is not None and m.years <= YEAR_TOLERANCE
        return (PROPOSED_EXACT if exact else PROPOSED_STRONG), record, scored
    if len(chosen) > 1:
        return AMBIGUOUS, None, scored
    if qualifying:
        return WORK_NO_DOI, qualifying[0][0], scored
    return AMBIGUOUS, None, scored
