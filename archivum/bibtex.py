"""
Functions for creating bibtex entries from various iterables.

v2  Hack off gemini, which actually was very poor for task at hand.
v1  Gemini.
"""
import logging
from typing import Any


logger = logging.getLogger(__name__)


def dict_to_bibtex(data: Any) -> str:
    """
    Converts a dict-like object (dict, pd.Series, DataFrame row) to a BibTeX string.

    Assumes data is "sensible" - mostly used by Library.to_bibtex.
    """
    if data is None:
        return ""

    # Handle pandas objects (Series or DataFrame row)
    if hasattr(data, "to_dict"):
        data = data.to_dict()

    # Handle NamedTuple (often returned by itertuples)
    if hasattr(data, "_asdict"):
        data = data._asdict()

    if not isinstance(data, dict):
        return ""

    # non empty elements
    data = {k: v for k, v in data.items() if v != ""}

    max_len = max(len(k) for k in data)

    bib_type = data.get('type')
    cite_key = data.get('tag')

    if not bib_type:
        logger.error('row missing type, %s', data.get('title'))

    if not cite_key:
        logger.error('row missing tag (citation key), %s', data.get('title'))

    lines = [f"@{bib_type}{{{cite_key},"]
    for k, v in data.items():
        if k in {'type', 'tag'}:
            continue
        padding = " " * (max_len - len(k))
        lines.append(f"  {k}{padding} = {{{v}}},")
    lines.append("}")

    return "\n".join(lines)


def dict_to_bibtex_crossref(data: Any) -> str:
    """
    Converts a dict-like object to a BibTeX string.

    Suitable for the return value form cross ref.

    Gemini code.
    """
    if data is None:
        return ""

    if hasattr(data, "to_dict"):
        data = data.to_dict()
    if hasattr(data, "_asdict"):
        data = data._asdict()

    if not isinstance(data, dict):
        return ""

    def get_list_safe(key: str) -> str:
        val = data.get(key)
        if isinstance(val, list) and val:
            return str(val[0])
        return str(val) if val else ""

    ctype = data.get('type', 'misc')
    type_map = {
        'article': 'article',
        'book': 'book',
        'techreport': 'techreport',
        'misc': 'misc',
        'incollection': 'incollection',
        'inproceedings': 'inproceedings',
        'phdthesis': 'phdthesis',
        'journal-article': 'article',
        'book-chapter': 'incollection',
        'proceedings-article': 'inproceedings',
        'monograph': 'book',
        'report': 'techreport',
        'dissertation': 'phdthesis'
    }

    bib_type = type_map.get(ctype, 'misc')

    authors = data.get('author', [])
    formatted_authors = []
    first_author_family = "Unknown"

    if authors and isinstance(authors, list):
        first_author_family = authors[0].get('family', 'Unknown')
        for auth in authors:
            family = auth.get('family')
            given = auth.get('given')
            if family and given:
                formatted_authors.append(f"{family}, {given}")
            elif family:
                formatted_authors.append(family)
            elif 'name' in auth:
                formatted_authors.append(auth['name'])

    author_str = " and ".join(formatted_authors)

    date_parts = (
        data.get('published-print', {}).get('date-parts') or
        data.get('published-online', {}).get('date-parts') or
        data.get('created', {}).get('date-parts')
    )
    year = str(date_parts[0][0]) if date_parts and date_parts[0] else "nd"

    safe_family = "".join(filter(str.isalnum, first_author_family))
    cite_key = f"{safe_family}{year}"

    if (isbn := data.get("ISBN")):
        isbn = isbn[0]
    else:
        isbn = None

    fields = {
        'author': author_str,
        'title': get_list_safe('title'),
        'journal': get_list_safe('container-title'),
        'year': year,
        'volume': data.get('volume'),
        'number': data.get('issue'),
        'pages': data.get('page'),
        'doi': data.get('DOI'),
        'publisher': data.get('publisher'),
        'url': data.get('URL'),
        'isbn': isbn,
    }

    active_fields = {k: v for k, v in fields.items() if v}
    if not active_fields:
        return ""

    max_len = max(len(k) for k in active_fields)

    lines = [f"@{bib_type}{{{cite_key},"]
    for k, v in active_fields.items():
        clean_val = str(v).replace('&', '\\&').replace('%', '\\%').replace('_', '\\_')
        padding = " " * (max_len - len(k))
        lines.append(f"  {k}{padding} = {{{clean_val}}},")
    lines.append("}")

    return "\n".join(lines)
