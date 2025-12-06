import logging
import re
import subprocess
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import pymupdf  # fitz
from rapidfuzz.fuzz import ratio

from .arxiv import lookup_arxiv
from .crossref import lookup_doi, search as xref_search
from .utilities import sanitize_windows_component

logger = logging.getLogger(__name__)

# Mapping Crossref types to BibTeX types
CROSSREF_TO_BIBTEX = {
    "journal-article": "article",
    "book-chapter": "incollection",
    "proceedings-article": "inproceedings",
    "monograph": "book",
    "book": "book",
    "report": "techreport",
    "dissertation": "phdthesis",
    "preprint": "article",  # Often best fit for arXiv
}


class Document:
    """
    Manages a physical PDF document (an afile).
    Handles text extraction, metadata discovery, and BibTeX generation.
    """

    def __init__(self, doc_path: Path):
        self.doc_path = Path(doc_path)
        self._new_doc_path = None
        self._text: str = ""

        # The central source of truth for the best-guess info
        self.bib: Dict[str, str] = {
            "entry_type": "article",  # default
            "title": "",
            "author": "",  # Normalized string "Last, First and Last, First"
            "year": "",
            "month": "",
            "day": "",
            "doi": "",
            "arxiv_id": "",
            "journal": "",  # For articles
            "booktitle": "",  # For chapters/proceedings
            "publisher": "",
            "volume": "",
            "number": "",
            "pages": "",
        }

    def __repr__(self):
        return f"Document({self.doc_path.name})"

    @property
    def has_text(self) -> bool:
        return bool(self._text)

    def extract_text(self) -> str:
        """
        Extracts text using pdftotext.
        Stores result in self._text and returns it.
        """
        if self._text:
            return self._text

        try:
            logger.info("extract text: %s", self.doc_path)
            # -raw: content stream order, -nopgbrk: no page breaks
            result = subprocess.run(
                ["pdftotext", "-raw", "-nopgbrk", str(self.doc_path), "-"],
                capture_output=True,
                check=True,
            )
            text = result.stdout.decode("utf-8", errors="replace").replace("\r", "")

            # Fix hyphenation (word-\nword -> wordword)
            text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
            # Normalize unicode
            text = unicodedata.normalize("NFC", text)

            self._text = text
            return text
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Error extracting text for {self.doc_path.name}: {e}")
            return ""

    def report(self):
        """Quick summary."""
        print(self.bibtex())

    def process(self):
        """
        Run the full discovery pipeline to populate self.bib.
        Order of operations increases in 'trust':
        1. File Metadata / Filename
        2. Visual Inspection (Cover Page)
        3. Identifier Scrape (DOI/Arxiv found in text)
        4. External API Enhancement (Crossref/Arxiv)
        """
        # 1. Base Metadata
        self._step_metadata()
        # self.report()
        # 2. Visual/Text Inspection of Page 1
        self._step_cover_page()
        # self.report()

        # 3. Enhance with External APIs
        self._step_external_enhancement()
        # self.report()

        # Final cleanup
        if not self.bib["title"]:
            self.bib["title"] = self.doc_path.stem.replace("_", " ")
            self.bib["entry_type"] = "misc"

    def bibtex(self) -> str:
        """Generates a BibTeX entry blob."""
        if not self.bib["title"]:
            return ""

        # Generate Citation Key: LastNameYYYY
        last_name = "Unknown"
        if self.bib["author"]:
            # Assume "Last, First" or "First Last".
            # Simple heuristic: take the first word of the string if no comma,
            # or pre-comma if comma exists.
            first_auth = self.bib["author"].split(" and ")[0].strip()
            if "," in first_auth:
                last_name = first_auth.split(",")[0].strip()
            else:
                last_name = first_auth.split(" ")[-1].strip()

        # Remove non-alphanumeric from key
        last_name = re.sub(r"\W+", "", last_name)
        year = self.bib["year"] or "XXXX"
        cite_key = f"{last_name}{year}"

        entry_type = self.bib["entry_type"]
        lines = [f"@{entry_type}{{{cite_key},"]

        # Fields to exclude from output
        exclude = {"entry_type", "arxiv_id"}

        # Logic to swap journal/booktitle based on type
        display_bib = self.bib.copy()
        if (
            entry_type == "article"
            and not display_bib["journal"]
            and display_bib["booktitle"]
        ):
            display_bib["journal"] = display_bib.pop("booktitle")
        elif (
            entry_type in ["inproceedings", "incollection"]
            and not display_bib["booktitle"]
            and display_bib["journal"]
        ):
            display_bib["booktitle"] = display_bib.pop("journal")

        for key, val in display_bib.items():
            if val and key not in exclude:
                safe_val = str(val).replace("{", "\\{").replace("}", "\\}")
                if key == "title":
                    # double braces for title
                    lines.append(f"  {key} = {{{{{safe_val}}}}},")
                else:
                    lines.append(f"  {key} = {{{safe_val}}},")

        if self.bib["arxiv_id"]:
            lines.append(f"  eprint = {{{self.bib['arxiv_id']}}},")
            lines.append("  archivePrefix = {arXiv},")

        # new name if it exists
        p = self._new_doc_path or self.doc_path
        mendeley_file_str = (
            f":{p.drive[0]}\\:{str(p.absolute().as_posix())[2:]}:{p.suffix[1:]}"
        )
        lines.append(f"  file = {{{mendeley_file_str}}},")

        lines.append("}")
        return "\n".join(lines)

    def renamer(self):
        """Figure dir name and file name."""
        # make filename safe!
        sp = self.bib['author'].split(' and ')
        dir_name = ', '.join([i.split(',')[0] for i in sp][:3]) + (' et al' if len(sp) > 3 else "")
        file_name = f'{self.bib['year']}_{self.bib['title']}'
        dir_name = sanitize_windows_component(dir_name)
        file_name = sanitize_windows_component(file_name)
        return dir_name, file_name

    def rename(self, pdf_dir):
        """Hard link original file into pdf_dir/dir_name/file_name."""
        try:
            dir_name, file_name = self.renamer()
        except:
            raise
        pdf_dir = Path(pdf_dir)
        new_name = Path(file_name).with_suffix(self.doc_path.suffix)
        parent_dir = pdf_dir / dir_name
        parent_dir.mkdir(parents=True, exist_ok=True)
        new_path = parent_dir / new_name
        if new_path.exists():
            logger.warning('new path exists! Unlinking...')
            # new_path.unlink()
        # make new link
        logger.info("%s --> %s", new_path, self.doc_path)
        print(f"{new_path} ==> {self.doc_path}")
        # save it
        self._new_doc_path = new_path
        # new_path.hardlink_to(self.doc_path)

    # ----------------------------------------------------------------------
    # Pipeline Steps
    # ----------------------------------------------------------------------

    def _step_metadata(self):
        """Extract embedded PDF metadata."""
        try:
            with pymupdf.open(self.doc_path) as doc:
                meta = doc.metadata
        except Exception:
            return

        title = meta.get("title", "").strip()
        author = meta.get("author", "").strip()
        subject = meta.get("subject", "").strip()

        # Clean garbage titles
        if title:
            clean_title = re.sub(
                r"Microsoft (PowerPoint|Word)( - )?|Presentation title",
                "",
                title,
                flags=re.IGNORECASE,
            )
            self.bib["title"] = clean_title.strip()

        if author:
            self.bib["author"] = author

        # Attempt to find year in subject or creation date
        if not self.bib["year"]:
            year_match = re.search(r"\b(19|20)\d{2}\b", subject)
            if year_match:
                self.bib["year"] = year_match.group(0)
            else:
                # Fallback to file creation date from metadata
                cdate = meta.get("creationDate", "")
                if cdate.startswith("D:"):
                    self.bib["year"] = cdate[2:6]

    def _step_cover_page(self):
        """Scrape Page 1 for identifiers and 'Largest Font' title."""
        try:
            with pymupdf.open(self.doc_path) as doc:
                page = doc[0]
                text_dict = page.get_text("dict")
                raw_text = page.get_text("text")
        except Exception:
            return

        # A. Identifier Scraping (High Trust)
        # Arxiv ID
        arxiv_match = re.search(r"arXiv:(\d{4}\.\d{4,5})", raw_text, re.IGNORECASE)
        if arxiv_match:
            self.bib["arxiv_id"] = arxiv_match.group(1)

        # DOI
        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", raw_text, re.IGNORECASE)
        if doi_match:
            self.bib["doi"] = doi_match.group(0)

        # B. Visual Title Extraction (Medium Trust)
        # Only override metadata title if metadata looked garbage (short) or empty
        # and this visual title looks good.
        visual_title = self._find_largest_text(text_dict)

        current_title = self.bib["title"]
        if not current_title or len(current_title) < 5 or "Microsoft" in current_title:
            if visual_title and len(visual_title) > 5:
                self.bib["title"] = visual_title
        elif visual_title:
            # If we have both, trust visual if it's significantly longer
            # (Metadata often truncates) or very similar
            sim = ratio(current_title.lower(), visual_title.lower())
            if sim < 90 and len(visual_title) > len(current_title):
                self.bib["title"] = visual_title

    def _step_external_enhancement(self):
        """Use found IDs or Title/Author query to fetch clean data."""

        # 1. Arxiv Lookup
        if self.bib["arxiv_id"]:
            logger.info("arxiv lookup %s", self.bib["arxiv_id"])
            data = lookup_arxiv(self.bib["arxiv_id"])
            # Assuming data returns a dict or list of dicts.
            # Adjust based on your actual arxiv module return signature.
            if data:
                # If lookup_arxiv returns a dict keyed by ID, or a single result
                res = data if isinstance(data, dict) else data[0]
                self.bib["title"] = res.get("title", self.bib["title"])
                self.bib["author"] = res.get(
                    "author", self.bib["author"]
                )  # format list to string if needed
                self.bib["year"] = str(res.get("year", self.bib["year"]))
                self.bib["journal"] = "arXiv preprint"
                return  # Stop if we found it via Arxiv

        # 2. DOI Lookup
        if self.bib["doi"]:
            logger.info("doi lookup %s", self.bib["doi"])
            data = lookup_doi(self.bib["doi"])
            if data:
                self._update_from_crossref(data)
                return

        # 3. Crossref Search (Last Resort)
        # Construct query
        q_title = self.bib["title"]
        q_author = self.bib["author"]
        if q_title and len(q_title) > 10:
            logger.info("xref lookup %s, %s", self.bib["author"], self.bib["title"])
            results = xref_search(
                query=f"{q_title} {q_author}"
            )  # title=q_title, author=q_author)
            # print("\n\nX cross ref queryX\n\n")
            if results:
                # Validate the top result
                top = results[0]  # assuming list of results

                top_title = (
                    top.get("title", [""])[0]
                    if isinstance(top.get("title"), list)
                    else top.get("title", "")
                )
                # print(top_title, q_title, sep="\n\t")
                # print(ratio(q_title.lower(), top_title.lower()))
                # Only accept if titles match reasonably well (>85%)
                if ratio(q_title.lower(), top_title.lower()) > 80:
                    # print("\n\nupdating from cross ref\n\n")
                    # print("\n\nupdating from cross ref\n\n")
                    self._update_from_crossref(top)

    def _update_from_crossref(self, data: Dict):
        """Map Crossref API response to internal bib dict."""
        # Titles in crossref are often lists
        t = data.get("title", "")
        self.bib["title"] = t[0] if isinstance(t, list) and t else t

        # Authors: Crossref usually returns list of dicts [{'given':, 'family':}]
        authors = data.get("author", [])
        if isinstance(authors, list):
            auth_strs = []
            for a in authors:
                if "family" in a:
                    auth_strs.append(
                        f"{a.get('family')}, {a.get('given', '')}".strip(", ")
                    )
            self.bib["author"] = " and ".join(auth_strs)

        # Date
        # Crossref 'published-print' or 'published-online' -> 'date-parts' [[2020, 1, 1]]
        pub = (
            data.get("published-print")
            or data.get("published-online")
            or data.get("created")
        )
        if pub and "date-parts" in pub:
            self.bib["year"] = str(pub["date-parts"][0][0])

        self.bib["doi"] = data.get("DOI", self.bib["doi"])

        # Container (Journal/Proceedings)
        j = data.get("container-title", [])
        container = j[0] if j else ""

        if self.bib["entry_type"] in ["inproceedings", "incollection"]:
            self.bib["booktitle"] = container
        else:
            self.bib["journal"] = container

        # Extended Fields
        self.bib["publisher"] = data.get("publisher", "")
        self.bib["volume"] = data.get("volume", "")
        self.bib["number"] = data.get("issue", "") or data.get("journal-issue", {}).get(
            "issue", ""
        )
        self.bib["pages"] = data.get("page", "")

    def _find_largest_text(self, text_dict: Dict) -> str:
        """
        Heuristic: The title is usually the text span with the largest font size
        on the first page.
        """
        blocks = text_dict.get("blocks", [])
        candidates = []

        for b in blocks:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if len(text) > 1:  # Ignore artifacts
                        candidates.append((span["size"], text))

        if not candidates:
            return ""

        # Sort by size descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Take the largest. If the next few are same size (multiline title), join them.
        largest_size = candidates[0][0]
        title_parts = []

        for size, text in candidates:
            # Allow small float tolerance for "same size"
            if abs(size - largest_size) < 0.5:
                title_parts.append(text)
            else:
                break

        return " ".join(title_parts)
