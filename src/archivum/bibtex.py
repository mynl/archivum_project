"""
Functions for creating bibtex entries from various iterables.

v2  Hack off gemini, which actually was very poor for task at hand.
v1  Gemini.
"""
import logging
import re
from pathlib import Path
from typing import Any, List
import pandas as pd
from rich.text import Text

logger = logging.getLogger(__name__)


def sanitize_for_latex(val: Any) -> str:
    """Sanitize string for LaTeX compatibility."""
    if pd.isna(val):
        return ""
    
    # Handle numbers: convert 2017.0 to 2017
    if isinstance(val, (float, int)):
        if isinstance(val, float) and val.is_integer():
            return str(int(val))
        return str(val)

    s = str(val)
    
    # 1. Nasty unicode dashes -> LaTeX dashes
    s = s.replace('–', '--').replace('—', '---')
    
    # 2. LaTeX Special Characters (only if not already escaped)
    # We use a negative lookbehind to avoid double escaping
    # Handling &, %, _, #, { }
    s = re.sub(r'(?<!\\)&', r'\&', s)
    s = re.sub(r'(?<!\\)%', r'\%', s)
    s = re.sub(r'(?<!\\)_', r'\_', s)
    s = re.sub(r'(?<!\\)#', r'\#', s)
    
    return s


def dict_to_bibtex(data: Any, allowed_fields: List[str] = None) -> str:
    """
    Converts a dict-like object to a sanitized BibTeX string.
    """
    if data is None:
        return ""

    # Handle pandas objects
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    
    # Handle NamedTuple (often returned by itertuples)
    if hasattr(data, "_asdict"):
        data = data._asdict()

    if not isinstance(data, dict):
        return ""

    # Standard header fields
    bib_type = str(data.get('type', 'article')).lower()
    cite_key = str(data.get('tag', 'unknown'))

    # Determine which fields to process
    if allowed_fields:
        # Use whitelist, excluding type/tag which are in the header
        keys = [k for k in allowed_fields if k not in {'type', 'tag'}]
    else:
        # Fallback: process all fields except blacklisted ones
        keys = [k for k in data.keys() if k not in {'type', 'tag'} 
                and not k.startswith(('arc-', 'mendeley-'))
                and k != 'merge_count']

    # Filter out empty/NaN and sanitize
    processed_data = {}
    for k in keys:
        v = data.get(k)
        if pd.isna(v) or str(v).strip() in ("", "nan"):
            continue
        
        sanitized_v = sanitize_for_latex(v)
        if sanitized_v:
            # Title preservation: wrap in double braces if it's a title/journal
            # but ONLY if not already braced.
            # We check for a single '{' at start to avoid triple bracing {{ { ... } }}
            if k in ('title', 'journal', 'booktitle') and not str(sanitized_v).startswith('{'):
                processed_data[k] = f"{{{sanitized_v}}}"
            else:
                processed_data[k] = sanitized_v

    if not processed_data:
        return ""

    max_len = max(len(k) for k in processed_data)

    lines = [f"@{bib_type}{{{cite_key},"]
    for k, v in processed_data.items():
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


def format_biblio(df: pd.DataFrame) -> str:
    """Print out a df, one entry per line, MLA/AP style."""
    df_sorted = df.sort_values("tag")

    lines = []
    for _, row in df_sorted.iterrows():
        # Clean fields
        fields = {
            col: str(row.get(col, "")).strip("{}") if pd.notna(row.get(col)) else ""
            for col in ["author", "title", "journal", "publisher", "type"]
        }

        source = fields["journal"] or fields["publisher"]
        # only three words of journal title
        source = ' '.join([i for i in source.split( )[:4]])

        # Use [i] for italics in Rich
        source_str = f"[i]{source}[/i]" if source else ""
        # Build the clickable link using Rich markup
        # Syntax: [link=file:///path/to/file]hash[:6][/link]
        short_hash = str(row["hash"])[:6]

        if pd.notna(row.get("path")) and row["path"]:
            path_uri = Path(row["path"]).resolve().as_uri()
            clickable_hash = f"[link={path_uri}][blue]{short_hash}[/blue][/link]"
        else:
            clickable_hash = f'HH{short_hash}'

        if fields['type'] == 'book':
            type = '[red]book[/red]'
        else:
            type = None

        bits = []
        bits.append(clickable_hash)
        if type:
            bits.append(type)
        bits.append(f"\"{fields['title']}\"")
        if (fa := fields['author']) != '':
            bits.append(f'[yellow]{fa}[/yellow]')
        if source_str:
            bits.append(source_str)
        spcer = '' if row['tag'][-1] in list('abcdefgh') else ' '
        line = f"[{row['tag']}{spcer}] " + ', '.join(bits)
        lines.append(line)

    return "\n".join(lines)

