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
