"""Class to manage a single reference for archivum."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Union


from . hasher import qhash

@dataclass
class Reference:
    tag: Optional[Path]                 # authorYYYYa identifying tag, must be provided by library to stay unique
    type: Optional[str]                 # bibtex type: journal, book etc.
    title: str
    year: int
    author: Optional[str] = None
    journal: Optional[str] = None
    volume: Optional[Path] = None
    number: Optional[str] = None
    month: Optional[str] = None
    pages: Optional[str] = None
    edition: Optional[str] = None
    booktitle: Optional[str] = None
    isbn: Optional[str] = None
    editor: Optional[str] = None
    publisher: Optional[str] = None
    institution: Optional[str] = None
    address: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[Path] = None
    source: Optional[Path] = None
    mendeley_tags: Optional[str] = None    # from mendeley library...these will transition
    arc_citations: Optional[Path] = None
    arc_source: Optional[Path] = None      # mendeley, imported etc.

    def to_dict(self, fillna=None):
        """Convert to dictionary format."""
        if fillna is None:
            return {k: v for k, v in self.__dict__.items() if v is not None and v != ''}
        else:
            return {k: fillna if v is None else v for k, v in self.__dict__.items()}

    def to_bibtex(self):
        """Return dict with fillna='' suitable for loading into ref_df."""
        return self.to_dict('')

    @staticmethod
    def from_crossref(data: dict, lib=None) -> dict:
        """
        Create from a crossref return value.

        Pass lib for name completion and other edits.
        """
        crossref_type = data.get("type", "").lower()
        bibtex_type = {
            "journal-article": "article",
            "book": "book",
            "book-chapter": "inbook",
            "proceedings-article": "inproceedings",
            "posted-content": "unpublished",
            "report": "techreport",
            "reference-entry": "misc",
            "dataset": "misc",
            "book-section": "incollection",
            "dissertation": "phdthesis",
            "manual": "manual",
        }.get(crossref_type, "misc")

        def format_names(people):
            names = [f"{p['family']}, {p.get('given', '')}".strip() for p in people]
            if lib:
                names = [lib.to_name_ex(name, strict=False) for name in names]
            return " and ".join(names)

        authors = data.get("author", [])
        editors = data.get("editor", [])

        issued = data.get("issued", {}).get("date-parts", [[None]])
        year = issued[0][0] if issued and issued[0] else None

        entry = {
            "type": bibtex_type,
            "title": data.get("title", [""])[0],
            "year": year,
            "doi": data.get("DOI", ""),
            "url": data.get("URL", ""),
        }

        if authors:
            entry["author"] = format_names(authors)

        if editors:
            entry["editor"] = format_names(editors)

        # Add shared optional fields
        if "edition" in data:
            entry["edition"] = data["edition"]
        if "ISBN" in data:
            entry["isbn"] = data["ISBN"][0]

        # Add type-specific fields
        if bibtex_type == "article":
            entry.update({
                "journal": data.get("container-title", [""])[0],
                "volume": data.get("volume", ""),
                "number": data.get("issue", ""),
                "pages": data.get("page", ""),
            })
        elif bibtex_type == "book":
            entry.update({
                "publisher": data.get("publisher", ""),
            })
        elif bibtex_type == "inbook":
            entry.update({
                "booktitle": data.get("container-title", [""])[0],
                "publisher": data.get("publisher", ""),
                "chapter": data.get("chapter", ""),
                "pages": data.get("page", ""),
            })
        elif bibtex_type == "incollection":
            entry.update({
                "booktitle": data.get("container-title", [""])[0],
                "publisher": data.get("publisher", ""),
                "pages": data.get("page", ""),
            })
        elif bibtex_type == "inproceedings":
            entry.update({
                "booktitle": data.get("container-title", [""])[0],
                "publisher": data.get("publisher", ""),
                "pages": data.get("page", ""),
            })
        elif bibtex_type == "manual":
            entry.update({
                "organization": data.get("publisher", ""),
            })
        elif bibtex_type == "phdthesis":
            entry.update({
                "school": data.get("publisher", ""),
            })
        elif bibtex_type == "techreport":
            entry.update({
                "institution": data.get("publisher", ""),
                "number": data.get("report-number", data.get("number", "")),
            })

        # finally th etag
        if lib:
            if authors:
                a = authors[0]['family']
            elif editors:
                a = editors[0]['family']
            else:
                a = qhash(str(data))
            y = f'{year:04d}'
            tag = lib.next_tag(a, y)
        else:
            # XXXX Year! TODO
            tag = lib.next_tag('NoName', '2025')
        entry['tag'] = tag
        entry['source'] = 'crossref'
        entry['arc_source'] = 'import'

        return Reference(**entry)
