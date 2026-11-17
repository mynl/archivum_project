"""
DOI rules: normalization and the classes a stored DOI can fall into.

Pure functions of text, with no I/O.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

MALFORMED = "malformed"
ATTENTION = "attention"

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
