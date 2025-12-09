"""
Document class

Combines best of Gemini and my original document class.

renamer moved into utilities.

v 1.0   2025-12-06
"""
from datetime import datetime
import logging
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

from nameparser import HumanName
import pymupdf  # type: ignore[import-untyped]  # fitz
from rapidfuzz import fuzz

from .arxiv import lookup_arxiv
from .crossref import lookup_doi, search as lookup_xref_search
from .utilities import sanitize_windows_component
from .bibtex import dict_to_bibtex_crossref

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
    Manages a physical PDF document.
    Uses a Gather -> Rank -> Verify strategy to reconcile Metadata, Filenames, and OCR.
    """

    def __init__(self, doc_path: Path, book_mode: bool = False):
        self.doc_path = Path(doc_path)
        self._new_doc_path: Optional[Path] = None
        self._text: str = ""
        self.book_mode = book_mode

        # Operational Status
        self.status = "NEW"  # NEW, SUCCESS, REVIEW_NEEDED, FAILED
        self.confidence_score = 0  # 0 to 100
        self.log_messages = []

        # The final chosen metadata
        self.bib: Dict[str, str] = {
            "type": "book" if book_mode else "article",
            "title": "",
            "author": "",
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

        # Candidate data from different sources
        self.candidates = {
            "filename": {},
            "metadata": {},
            "visual": {},
            "api": {},
        }

    def __repr__(self):
        return f"Document({self.doc_path.name}) [{self.status}]"

    def process(self):
        """
        Orchestrates the discovery pipeline:
        1. Gather: Collect raw info from Filename, PDF Metadata, and Visual OCR.
        2. Rank: Choose the best local 'Anchor' to use for search.
        3. Enhance: Query external APIs using the Anchor.
        4. Verify: Validate API results against the Anchor.
        """
        # 1. Gather
        self._parse_filename()
        self._step_metadata()
        self._step_visual()

        # 2. Rank / Anchor Selection
        anchor_source, anchor_data = self._determine_anchor()
        self.log_messages.append(f"Selected anchor source: {anchor_source}")

        # 3. Enhance (API Search)
        # If we found an ID (DOI/Arxiv) visually, that's an automatic win.
        if self.candidates["visual"].get("doi") or self.candidates["visual"].get(
            "arxiv_id"
        ):
            self._step_id_lookup(self.candidates["visual"])
        else:
            self._step_search_api(anchor_data)

        # 4. Verify & Merge
        self._validate_and_merge(anchor_data)

        # Final cleanup
        if not self.bib["title"]:
            self.bib["title"] = self.doc_path.stem.replace("_", " ")
            self.status = "FAILED"

    # ----------------------------------------------------------------------
    # 1. GATHERING STEPS
    # ----------------------------------------------------------------------

    def _parse_filename(self):
        """
        Heuristic parsing of filenames based on common ebook patterns.
        """
        name = self.doc_path.stem
        candidate = {"source": "filename"}

        # --- A. Clean Noise ---
        # Remove common "pirate" tags and file extensions in stem if any
        noise_patterns = [
            r"\(z-lib\.org\)",
            r"\(Z-Library\)",
            r"libgen\.li",
            r"\(auth\.\)",
            r"\(eds\.\)",
            r"\(ed\.\)",
            r"_crc",
            r"\(.*Springer.*\)",
            r"\(.*Cambridge.*\)",
            r"\(.*Wiley.*\)",
        ]
        clean_name = name
        for pat in noise_patterns:
            clean_name = re.sub(pat, "", clean_name, flags=re.IGNORECASE)

        clean_name = clean_name.replace(r"_", " ").replace('-', ' ').strip()

        # --- B. Extract Year ---
        # Look for (YYYY) or start of string YYYY
        year_match = re.search(r"\((\d{4})\)|^\s*(\d{4})\s", clean_name)
        if year_match:
            candidate["year"] = year_match.group(1) or year_match.group(2)
            # Remove year from name to clean up title parsing
            clean_name = re.sub(r"\((\d{4})\)|^\s*(\d{4})\s", "", clean_name)

        # --- C. Structure Detection ---

        # Strategy 1: "Title by Author"
        if " by " in clean_name:
            parts = clean_name.split(" by ", 1)
            candidate["title"] = parts[0].strip()
            candidate["author"] = parts[1].strip()

        # Strategy 2: "Author - Title" (Hyphen separated)
        # Note: Many files have "Series - Author - Title".
        # We split by " - " (space hyphen space) to avoid hyphenated words.
        elif " - " in clean_name:
            segments = clean_name.split(" - ")
            # Heuristic: Title is usually the longest segment
            longest = max(segments, key=len)
            candidate["title"] = longest.strip()

            # If 2 parts: Author - Title OR Title - Author?
            # Usually Author - Title.
            if len(segments) >= 2:
                # If the longest is the second part, assume first is author
                if longest == segments[1]:
                    candidate["author"] = segments[0].strip()
                # If longest is first part, assume second is author (less common but possible)
                elif longest == segments[0]:
                    candidate["author"] = segments[1].strip()

        # Strategy 3: "Title (Author)"
        elif "(" in clean_name and clean_name.endswith(")"):
            # Last parentheses often contain Author
            match = re.search(r"(.*)\((.*)\)$", clean_name)
            if match:
                candidate["title"] = match.group(1).strip()
                candidate["author"] = match.group(2).strip()

        # Fallback: Treat whole cleaned string as Title
        else:
            candidate["title"] = clean_name.strip()

        # Clean up authors (remove " et al", etc)
        if candidate.get("author"):
            candidate["author"] = re.sub(
                r" et al\.?", "", candidate["author"], flags=re.IGNORECASE
            )

        self.candidates["filename"] = candidate

    def _step_metadata(self):
        """Extract embedded PDF metadata."""
        c = {"source": "metadata"}
        try:
            with pymupdf.open(self.doc_path) as doc:
                meta = doc.metadata
        except Exception:
            c["error"] = "extracting md"
            self.candidates["metadata"] = c
            return

        title = meta.get("title", "").strip()
        author = meta.get("author", "").strip()
        subject = meta.get("subject", "").strip()

        # Filter Garbage Metadata
        bad_titles = ["Microsoft Word", "Untitled", "Presentation", "Document"]
        if title and len(title) > 3 and not any(b in title for b in bad_titles):
            c["title"] = title

        if author and len(author) > 2 and "@" not in author:
            c["author"] = author

        # Attempt to find year in subject or creation date
        year_match = re.search(r"\b(19|20)\d{2}\b", subject)
        if year_match:
            c["year"] = year_match.group(0)
        else:
            # Fallback to file creation date from metadata
            cdate = meta.get("creationDate", "")
            if cdate.startswith("D:"):
                c["year"] = cdate[2:6]

        self.candidates["metadata"] = c

    def _step_visual(self):
        """Visual scraping for Largest Text (Title) and IDs."""
        c = {"source": "visual"}
        try:
            with pymupdf.open(self.doc_path) as doc:
                page = doc[0]
                text_dict = page.get_text("dict")
                raw_text = page.get_text("text")
        except Exception:
            self.candidates["visual"] = c
            return

        # ID Scraping
        arxiv_match = re.search(r"arXiv:(\d{4}\.\d{4,5})", raw_text, re.IGNORECASE)
        if arxiv_match:
            c["arxiv_id"] = arxiv_match.group(1)

        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", raw_text, re.IGNORECASE)
        if doi_match:
            c["doi"] = doi_match.group(0)

        # Title Scraping (Largest Font)
        visual_title = self._find_largest_text(text_dict)
        if visual_title:
            # the all caps trick
            all_caps = [w for w in visual_title.split(' ') if w.isupper() and len(w) > 3]
            if 5 <= len(all_caps) <= 15:
                visual_title = (' '.join(all_caps)).title()
            # if too long probably includes the abstract too...
            # but who knows where to truncate!
        c["title"] = visual_title
        self.candidates["visual"] = c

    # ----------------------------------------------------------------------
    # 2. RANKING / ANCHOR
    # ----------------------------------------------------------------------

    def _determine_anchor(self) -> Tuple[str, Dict]:
        """
        Compare candidates and pick the 'Anchor'—the most trustworthy local source.
        Priorities:
        1. Visual (If Title exists and looks 'clean')
        2. Filename (If parsed successfully)
        3. Metadata (Lowest trust)
        """
        vis = self.candidates["visual"]
        fn = self.candidates["filename"]
        meta = self.candidates["metadata"]

        # Helper to score a candidate
        def score(c):
            s = 0
            if not c.get("title"):
                return -100
            else:
                s += 50
            t_len = len(c["title"].split(" "))
            if t_len == 1:
                s -= 50
            elif t_len <= 5:
                s -= 25
            # penalize junk non-alpha chars
            s -= sum([1 for c in c["title"] if c != ' ' and not c.isalpha()])
            if "Microsoft" in c["title"]:
                s -= 50
            if c.get("author"):
                s += 25
            return s

        s_vis = score(vis)
        s_fn = score(fn)
        s_meta = score(meta)

        # print('determining anchor based on:')
        # print(vis, s_vis)
        # print(fn, s_fn)
        # print(meta, s_meta)

        # Logic
        best = "filename"
        max_s = s_fn

        if s_vis > max_s:
            best = "visual"
            max_s = s_vis

        if s_meta > max_s:
            best = "metadata"
            max_s = s_meta

        self.log_messages.append(f'Anchor determined from {best}, score {max_s}')

        return best, self.candidates[best]

    # ----------------------------------------------------------------------
    # 3. EXTERNAL ENHANCEMENT
    # ----------------------------------------------------------------------

    def _step_id_lookup(self, source_data):
        """Lookup by DOI or Arxiv ID."""
        if source_data.get("arxiv_id"):
            # print("arxiv lookup", source_data["arxiv_id"])
            res = lookup_arxiv(source_data["arxiv_id"])
            if res:
                self.candidates["api"] = self._normalize_arxiv(res)
                return

        if source_data.get("doi"):
            print("crossref doi lookup", source_data["doi"])
            res = lookup_doi(source_data["doi"])
            if res:
                self.candidates["api"] = self._normalize_crossref(res)
                self.candidates['crossref-bib'] = dict_to_bibtex_crossref(res)
                return

    def _step_search_api(self, anchor):
        """Search Crossref using Title/Author from anchor."""
        if not anchor.get("title"):
            return

        query = anchor["title"]
        if anchor.get("author"):
            query += f" {anchor['author']}"

        # logger.info(f"Searching Crossref: {query}")
        print("crossref query lookup", query)
        results = lookup_xref_search(query, book_mode=self.book_mode)
        if results:
            # We take the top result tentatively
            self.candidates["api"] = self._normalize_crossref(results[0])
            self.candidates['crossref-bib'] = dict_to_bibtex_crossref(results[0])

    # ----------------------------------------------------------------------
    # 4. VERIFY & MERGE
    # ----------------------------------------------------------------------

    def _validate_and_merge(self, anchor):
        """
        Decide whether to trust the API result or fallback to the Anchor.
        """
        api = self.candidates.get("api", {})

        if not api:
            # No API result found. Use Anchor.
            print('NO API RESULT')
            self.bib.update(anchor)
            self.status = "REVIEW_NEEDED" if not anchor.get("author") else "SUCCESS"
            self.log_messages.append("No API results. Using local anchor.")
            return

        # Validation: Compare API Title vs Anchor Title
        # We use Token Sort Ratio to handle word reordering
        similarity = fuzz.token_sort_ratio(
            str(anchor.get("title", "")).lower(), str(api.get("title", "")).lower()
        )

        self.confidence_score = similarity

        if similarity > 80:
            # High Confidence: Accept API
            self.bib.update(api)
            self.status = "SUCCESS"
        elif similarity > 50:
            # Medium Confidence: Accept API but flag
            self.bib.update(api)
            self.status = "REVIEW_NEEDED"
            self.log_messages.append(f"Medium match ({similarity}%). check title.")
        else:
            # Low Confidence: Reject API, use Anchor
            self.bib.update(anchor)
            self.status = "REVIEW_NEEDED"
            self.log_messages.append(
                f"Rejected API match ({similarity}%). API found: '{api.get('title')}'"
            )

    # ----------------------------------------------------------------------
    # UTILITIES & NORMALIZERS
    # ----------------------------------------------------------------------

    def _normalize_crossref(self, data):
        """Map Crossref JSON to internal dict."""
        out = {}
        t = data.get("title", "")
        out["title"] = t[0] if isinstance(t, list) and t else str(t)

        # Authors
        authors = data.get("author", [])
        if isinstance(authors, list):
            auth_strs = []
            for a in authors:
                if "family" in a:
                    auth_strs.append(
                        f"{a.get('family')}, {a.get('given', '')}".strip(", ")
                    )
            out["author"] = " and ".join(auth_strs)

        # Date
        pub = (
            data.get("published-print")
            or data.get("published-online")
            or data.get("created")
        )
        if pub and "date-parts" in pub:
            out["year"] = str(pub["date-parts"][0][0])

        out["doi"] = data.get("DOI", "")
        out["publisher"] = data.get("publisher", "")

        # Container
        j = data.get("container-title", [])
        container = j[0] if j else ""
        out["journal"] = container  # Simplified mapping

        return out

    def _normalize_arxiv(self, data):
        """Map Arxiv JSON to internal dict."""
        # Handle list return from lookup_arxiv
        if isinstance(data, list) and data:
            data = data[0]

        ans = {
            "title": data.get("title", ""),
            "author": data.get("author", ""),  # Assuming already stringified
            "year": str(data.get("year", "")),
            "arxiv_id": data.get("arxiv", ""),  # Adjust based on your arxiv module
        }
        if not ans['arxiv_id'] and 'eprint' in data:
            ans['arxiv_id'] = data['eprint']
        if 'doi' in data:
            ans['doi'] = data['doi']
        if 'journal' in data:
            ans['journal'] = data['journal']
        return ans

    def _find_largest_text(self, text_dict: Dict) -> str:
        """Return text with largest font size from pymupdf dict."""
        blocks = text_dict.get("blocks", [])
        candidates = []
        for b in blocks:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if len(text) > 1:
                        candidates.append((span["size"], text))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)

        # Join spans of same largest size (multiline titles)
        largest_size = candidates[0][0]
        title_parts = []
        for size, text in candidates:
            if abs(size - largest_size) < 0.5:
                title_parts.append(text)
            else:
                break
        return " ".join(title_parts)

    # ----------------------------------------------------------------------
    # OUTPUTS
    # ----------------------------------------------------------------------

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

    def report(self, print_fn=print):
        """Quick summary."""
        print_fn(self.bibtex())

    def bibtex(self) -> str:
        if not self.bib["title"]:
            return ""

        # Key - will be over-ridden
        cite_key = "Author2099"

        lines = [f"@{self.bib['type']}{{{cite_key},"]

        for k, v in self.bib.items():
            if v and k not in ["type", "arxiv_id"]:
                val = str(v).replace("{", "\\{").replace("}", "\\}")
                if k == 'title':
                    lines.append(f"  {k} = {{{{{val}}}}},")
                elif k == "author":
                    val = Document._sort_authors(val)
                    lines.append(f"  {k} = {{{val}}},")
                else:
                    lines.append(f"  {k} = {{{val}}},")

        if self.bib.get("arxiv_id"):
            lines.append(f"  eprint = {{{self.bib['arxiv_id']}}},")
            lines.append("  archivePrefix = {arXiv},")

        p = self._new_doc_path or self.doc_path
        p = p.absolute()
        # Windows Mendeley style path
        mendeley_file = (
            f":{p.drive[0]}\\:{str(p.as_posix())[2:]}:{p.suffix[1:]}"
        )
        lines.append(f"  file = {{{mendeley_file}}},")

        lines.append("}")
        return "\n".join(lines)

    def show_log(self, print_fn=print):
        """Show the process log information."""
        print_fn(f'{self.doc_path.name}\n'
            + f'{"-" * len(self.doc_path.name)}\n'
            + '\n'.join(self.log_messages))

    @staticmethod
    def _sort_authors(authors):
        """Make last, first and ... """
        a_list = authors.split(' and ')
        out = []
        for a in a_list:
            hn = HumanName(a)
            name_out = f'{hn.last}, {hn.first}' + (
                f' {hn.middle}' if hn.middle else '')
            out.append(name_out)
        return ' and '.join(out)
