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

        # Flags to track which sources contributed to the final record.
        self._used_filename = False
        self._used_metadata = False
        self._used_cover = False
        self._used_identifier = False
        self._used_external = False

        # Overall quality status for the record.
        self.status: str = "partial"
        self.status_diagnostics: List[str] = []

        # Diagnostics just for the filename heuristic.
        self.filename_diagnostics: List[str] = []

    def _guess_from_filename(
        self,
    ) -> Tuple[Optional[str], Optional[str], Optional[str], float, List[str]]:
        """
        Best-effort guess of (title, author, year) from the filename.
        Returns a tuple (title, author, year, confidence, diagnostics).
        All fields may be None if no reasonable guess is possible.
        """
        diagnostics: List[str] = []
        stem = self.doc_path.stem

        # Normalize underscores and whitespace.
        name = stem.replace("_", " ")
        name = re.sub(r"\s+", " ", name).strip()

        # Strip trailing source tags like (z-lib.org), (Z-Library), (libgen.li).
        name2 = re.sub(
            r"\s*\((z-lib\.org|Z-Library|libgen\.li)\)\s*$",
            "",
            name,
            flags=re.IGNORECASE,
        )
        if name2 != name:
            diagnostics.append("removed trailing source tag")
            name = name2

        # Strip trailing copy indices like (1), (2).
        name2 = re.sub(r"\s*\(\d+\)\s*$", "", name)
        if name2 != name:
            diagnostics.append("removed trailing copy index")
            name = name2

        # Strip leading series information in parentheses.
        name2 = re.sub(r"^\([^)]*\)\s*", "", name)
        if name2 != name:
            diagnostics.append("removed leading series information")
            name = name2

        # If nothing useful remains, bail out.
        if not name or len(name) < 5:
            diagnostics.append("filename too short or empty after cleaning")
            return None, None, None, 0.0, diagnostics

        # Find a plausible year (prefer the last one if multiple).
        year: Optional[str] = None
        year_matches = re.findall(r"\b(19\d{2}|20\d{2})\b", name)
        if year_matches:
            year = year_matches[-1]
            diagnostics.append(f"found year {year} in filename")
            name = re.sub(rf"\b{year}\b", "", name)
            name = re.sub(r"\s+", " ", name).strip(" -_,")

        title: Optional[str] = None
        author: Optional[str] = None
        confidence = 0.0

        # Pattern 1: "Title by Author"
        by_match = re.search(r"\sby\s", name, flags=re.IGNORECASE)
        if by_match:
            raw_title = name[: by_match.start()].strip(" -_,")
            raw_author = name[by_match.end() :].strip(" -_,")
            # Drop trailing parenthetical noise from author.
            raw_author = re.sub(r"\s*\([^)]*\)\s*$", "", raw_author).strip(" -_,")
            if raw_title and raw_author:
                title = raw_title
                author = raw_author
                confidence = 0.9
                diagnostics.append("matched pattern 'title by author'")

        # Pattern 2: "Title (Author)" at the end.
        if title is None:
            paren_match = re.search(r"\(([^()]*)\)\s*$", name)
            if paren_match:
                possible_author = paren_match.group(1).strip()
                # Heuristic: looks like a name if it has at least one space and no digits.
                if " " in possible_author and not re.search(r"\d", possible_author):
                    raw_title = name[: paren_match.start()].strip(" -_,")
                    if raw_title:
                        title = raw_title
                        author = possible_author
                        confidence = max(confidence, 0.75)
                        diagnostics.append("matched pattern 'title (author)'")

        # Pattern 3: "Author - Title - ..."
        if title is None:
            parts = re.split(r"\s-\s", name)
            if len(parts) >= 2:
                candidate_author = re.sub(r"\s*\([^)]*\)\s*$", "", parts[0]).strip(
                    " -_,"
                )
                remainder = " - ".join(parts[1:]).strip(" -_,")
                # Basic name-like check: letters present, very few digits.
                if re.search(r"[A-Za-z]", candidate_author) and not re.search(
                    r"\d", candidate_author
                ):
                    author = candidate_author
                    title = remainder
                    confidence = max(confidence, 0.8)
                    diagnostics.append("matched pattern 'author - title'")

        # Pattern 4: "YYYY Title" (only if we still do not have a title).
        if title is None:
            m = re.match(r"^(19\d{2}|20\d{2})[\s_]+(.+)$", stem)
            if m:
                if year is None:
                    year = m.group(1)
                    diagnostics.append(f"year {year} from prefix")
                title = m.group(2).replace("_", " ")
                title = re.sub(r"\s+", " ", title).strip(" -_,")
                confidence = max(confidence, 0.5)
                diagnostics.append("matched pattern 'YYYY title'")

        # Final fallback: treat remaining cleaned name as title only.
        if title is None:
            title = name.strip(" -_,")
            confidence = max(confidence, 0.3)
            diagnostics.append("fallback: treated filename as title only")

        # Normalize whitespace for title and author.
        if title:
            title = re.sub(r"\s+", " ", title).strip(" -_,")
        if author:
            author = re.sub(r"\s+", " ", author).strip(" -_,")

        # Basic informativeness check for title.
        def _informative(s: str) -> bool:
            letters = len(re.findall(r"[A-Za-z]", s))
            return bool(letters) and letters >= len(s) / 3

        if not title or len(title) < 5 or not _informative(title):
            diagnostics.append(f"title '{title}' deemed uninformative")
            title = None

        # Adjust confidence based on what we actually have.
        if title is None and author is None and year is None:
            return None, None, None, 0.0, diagnostics

        if title is not None:
            confidence += 0.1
        if author is not None:
            confidence += 0.1
        if year is not None:
            confidence += 0.1

        confidence = max(0.0, min(1.0, confidence))

        return title, author, year, confidence, diagnostics

    def _step_filename(self) -> None:
        """
        Use the filename as a low-trust heuristic for title/author/year.
        Only fills fields that are currently empty.
        """
        title, author, year, confidence, diagnostics = self._guess_from_filename()
        self.filename_diagnostics = diagnostics

        if title or author or year:
            self._used_filename = True

        if title and not self.bib["title"]:
            self.bib["title"] = title
        if author and not self.bib["author"]:
            self.bib["author"] = author
        if year and not self.bib["year"]:
            self.bib["year"] = year

    def _finalize_status(self) -> None:
        """
        Derive an overall status ('ok', 'partial', 'failed') from the fields
        and which sources were used.
        """
        # No title => hard failure.
        if not self.bib["title"]:
            self.status = "failed"
            self.status_diagnostics.append("missing title after pipeline")
            return

        core_ok = bool(self.bib["title"] and self.bib["author"] and self.bib["year"])

        # Highest confidence: we used external identifiers (doi/arxiv/crossref).
        if self._used_external or self.bib["doi"] or self.bib["arxiv_id"]:
            if core_ok:
                self.status = "ok"
            else:
                self.status = "partial"
                self.status_diagnostics.append(
                    "external data used but some core fields missing"
                )
            return

        # Next: we have core fields from internal sources (metadata/cover/filename).
        if core_ok and (self._used_metadata or self._used_cover or self._used_filename):
            self.status = "ok"
            return

        if core_ok:
            self.status = "partial"
            self.status_diagnostics.append(
                "core fields present but only weakly supported"
            )
        else:
            self.status = "failed"
            self.status_diagnostics.append(
                "missing at least one of title, author, year"
            )

    def process(self):
        """
        Run the full discovery pipeline to populate self.bib.

        Order of operations increases in 'trust':
        0. Filename heuristic
        1. File Metadata
        2. Visual Inspection (Cover Page)
        3. Identifier Scrape (DOI/Arxiv found in text)
        4. External API Enhancement (Crossref/Arxiv)
        """
        # Reset flags and status for this run.
        self._used_filename = False
        self._used_metadata = False
        self._used_cover = False
        self._used_identifier = False
        self._used_external = False
        self.status = "partial"
        self.status_diagnostics = []
        self.filename_diagnostics = []

        # 0. Filename heuristic (low trust).
        self._step_filename()

        # 1. Base Metadata.
        self._step_metadata()
        # 2. Visual/Text Inspection of Page 1.
        self._step_cover_page()
        # 3. Enhance with External APIs.
        self._step_external_enhancement()

        # Final cleanup and status.
        if not self.bib["title"]:
            self.bib["title"] = self.doc_path.stem.replace("_", " ")
            self.bib["entry_type"] = "misc"

        self._finalize_status()

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

        header_lines: List[str] = []

        # If the record is not fully trusted, prepend a comment.
        if getattr(self, "status", "ok") != "ok":
            diag = "; ".join(getattr(self, "status_diagnostics", []))
            if diag:
                header_lines.append(f"% status={self.status}: {diag}")
            else:
                header_lines.append(f"% status={self.status}")

        header_lines.append(f"@{entry_type}{{{cite_key},")

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

        body_lines: List[str] = []

        for key, val in display_bib.items():
            if val and key not in exclude:
                safe_val = str(val).replace("{", "\\{").replace("}", "\\}")
                if key == "title":
                    # double braces for title
                    body_lines.append(f"  {key} = {{{{{safe_val}}}}},")
                else:
                    body_lines.append(f"  {key} = {{{safe_val}}},")

        if self.bib["arxiv_id"]:
            body_lines.append(f"  eprint = {{{self.bib['arxiv_id']}}},")
            body_lines.append("  archivePrefix = {arXiv},")

        # new name if it exists
        p = self._new_doc_path or self.doc_path
        mendeley_file_str = (
            f":{p.drive[0]}\\:{str(p.absolute().as_posix())[2:]}:{p.suffix[1:]}"
        )
        body_lines.append(f"  file = {{{mendeley_file_str}}},")

        body_lines.append("}")

        return "\n".join(header_lines + body_lines)

    def renamer(self):
        """Figure dir name and file name."""
        # make filename safe!
        sp = self.bib["author"].split(" and ") if self.bib["author"] else []
        dir_name = ", ".join([i.split(",")[0] for i in sp][:3])
        if len(sp) > 3:
            dir_name = f"{dir_name} et al" if dir_name else "et al"

        file_name = f"{self.bib['year']}_{self.bib['title']}".strip("_")

        dir_name = sanitize_windows_component(dir_name or "Unknown")
        file_name = sanitize_windows_component(file_name or self.doc_path.stem)

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
            logger.warning("new path exists! Unlinking...")
            # new_path.unlink()
        # make new link
        logger.info("%s --> %s", new_path, self.doc_path)
        print(f"{new_path} ==> {self.doc_path}")
        # save it
        self._new_doc_path = new_path
        # new_path.hardlink_to(self.doc_path)

    def _step_metadata(self):
        """Extract embedded PDF metadata."""
        try:
            with pymupdf.open(self.doc_path) as doc:
                meta = doc.metadata
        except Exception:
            return

        self._used_metadata = True

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
            self._used_identifier = True

        # DOI
        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", raw_text, re.IGNORECASE)
        if doi_match:
            self.bib["doi"] = doi_match.group(0)
            self._used_identifier = True

        # B. Visual Title Extraction (Medium Trust)
        # Only override metadata title if metadata looked garbage (short) or empty
        # and this visual title looks good.
        visual_title = self._find_largest_text(text_dict)

        current_title = self.bib["title"]
        if not current_title or len(current_title) < 5 or "Microsoft" in current_title:
            if visual_title and len(visual_title) > 5:
                self.bib["title"] = visual_title
                self._used_cover = True
        elif visual_title:
            # If we have both, trust visual if it's significantly longer
            # (Metadata often truncates) or very similar
            sim = ratio(current_title.lower(), visual_title.lower())
            if sim < 90 and len(visual_title) > len(current_title):
                self.bib["title"] = visual_title
                self._used_cover = True

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
                self._used_external = True
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
                # Only accept if titles match reasonably well (>80%)
                if ratio(q_title.lower(), top_title.lower()) > 80:
                    # print("\n\nupdating from cross ref\n\n")
                    self._update_from_crossref(top)

    def _update_from_crossref(self, data: Dict):
        """Map Crossref API response to internal bib dict."""
        self._used_external = True

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

        # Container: journal or booktitle
        container = data.get("container-title", [""])
        if isinstance(container, list):
            container = container[0] if container else ""
        else:
            container = str(container)

        # Map Crossref type to BibTeX type
        cr_type = data.get("type", "")
        self.bib["entry_type"] = CROSSREF_TO_BIBTEX.get(cr_type, "article")

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
