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
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Dict, Optional, Tuple, Any, List

from nameparser import HumanName
import pymupdf  # type: ignore[import-untyped]  # fitz
from rapidfuzz import fuzz
from tqdm import tqdm

from .arxiv import lookup_arxiv
from .crossref import lookup_doi, search as lookup_xref_search
from .utilities import sanitize_windows_component
from .bibtex import dict_to_bibtex_crossref, format_mendeley_file
from .hasher import hash_many3

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
        self.hash: str = ""

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

    def key(self):
        """A reasonable default key to make reviewing easy. Filename based."""
        n = self.doc_path.stem
        n = "".join(c for c in n if c.isalnum())
        return "f" + n[:20]

    def process(self):
        """
        Orchestrates the discovery pipeline by prioritizing evidence:

        1. Gather: Collect raw info from Filename, PDF Metadata, and Visual OCR.
        2. Prioritized Enhance: Attempt lookup using a found DOI or ArXiv ID. If successful,
           accept the result as definitive.
        3. Fallback Enhance: If no ID was found, determine the best local 'Anchor',
           search external APIs, and validate the results.
        """
        self.log_messages.append(f"Starting processing for {self.doc_path.name}")
        # 1. Gather
        self._parse_filename()
        self._step_metadata()
        self._step_visual()

        # 2. Prioritized Enhancement via found ID
        visual_ids = self.candidates["visual"].get("doi") or self.candidates["visual"].get(
            "arxiv_id"
        )
        if visual_ids:
            self.log_messages.append(
                "Found potential DOI/ArXiv ID from visual scan. Attempting direct lookup."
            )
            self._step_id_lookup(self.candidates["visual"])
            if self.candidates.get("api"):
                self.log_messages.append(
                    f"Direct lookup successful. API result: {self.candidates['api']}"
                )
                # High confidence - this is the best source of truth.
                self.bib.update(self.candidates["api"])
                self.status = "SUCCESS"
                self.confidence_score = 100  # 100% confidence in a direct ID lookup
                self.log_messages.append(
                    "Setting status to SUCCESS with 100% confidence based on ID lookup."
                )
                return  # End of processing

        # 3. Fallback to Anchor-based Search
        self.log_messages.append(
            "No definitive ID found or lookup failed. Falling back to anchor-based search."
        )
        anchor_source, anchor_data = self._determine_anchor()
        self.log_messages.append(f"Selected anchor source: '{anchor_source}' with data: {anchor_data}")

        if not anchor_data.get("title"):
            self.log_messages.append("Anchor has no title. Cannot proceed with search.")
            self.bib["title"] = self.doc_path.stem.replace("_", " ")
            self.status = "FAILED"
            return

        self._step_search_api(anchor_data)

        # 4. Verify & Merge for anchor-based search
        self._validate_and_merge(anchor_data)

        # 5. Final cleanup
        if not self.bib.get("title"):
            self.log_messages.append("Processing failed to find a title.")
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
        self.log_messages.append(f"Parsed Filename: {candidate}")

    def _step_metadata(self):
        """Extract embedded PDF metadata."""
        c = {"source": "metadata"}
        try:
            with pymupdf.open(self.doc_path) as doc:
                meta = doc.metadata
        except Exception as e:
            c["error"] = f"extracting md: {e}"
            self.candidates["metadata"] = c
            self.log_messages.append(f"PDF Metadata Error: {c['error']}")
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
        self.log_messages.append(f"Extracted Metadata: {c}")

    def _step_visual(self):
        """Visual scraping for Largest Text (Title) and IDs."""
        c = {"source": "visual"}
        try:
            with pymupdf.open(self.doc_path) as doc:
                page = doc[0]
                text_dict = page.get_text("dict")
                raw_text = page.get_text("text")
        except Exception as e:
            c["error"] = f"visual extraction: {e}"
            self.candidates["visual"] = c
            self.log_messages.append(f"Visual Scan Error: {c['error']}")
            return

        # ID Scraping
        # Handles new (YYMM.NNNNN) and old (subject/YYMMNNN) formats, with versions
        arxiv_match = re.search(
            r"arXiv:((?:\d{4}\.\d{4,5}(?:v\d+)?)|(?:[a-z-]+(?:\.[A-Z]{2})?\/\d{7}(?:v\d+)?))",
            raw_text,
            re.IGNORECASE,
        )
        if arxiv_match:
            c["arxiv_id"] = arxiv_match.group(1)

        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", raw_text, re.IGNORECASE)
        if doi_match:
            c["doi"] = doi_match.group(0).rstrip(".") # clean trailing dots

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
        self.log_messages.append(f"Visual Scan Results: {c}")

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

        scores = {"visual": s_vis, "filename": s_fn, "metadata": s_meta}
        best_source = max(scores, key=scores.get)

        self.log_messages.append(f"Anchor scores: {scores}")

        return best_source, self.candidates[best_source]

    # ----------------------------------------------------------------------
    # 3. EXTERNAL ENHANCEMENT
    # ----------------------------------------------------------------------

    def _step_id_lookup(self, source_data):
        """Lookup by DOI or Arxiv ID."""
        if source_data.get("arxiv_id"):
            arxiv_id = source_data["arxiv_id"]
            self.log_messages.append(f"Looking up arXiv ID: {arxiv_id}")
            res = lookup_arxiv(arxiv_id)
            if res:
                self.candidates["api"] = self._normalize_arxiv(res)
                return

        if source_data.get("doi"):
            doi = source_data["doi"]
            self.log_messages.append(f"Looking up DOI: {doi}")
            res = lookup_doi(doi)
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

        self.log_messages.append(f"Searching Crossref with query: '{query}'")
        results = lookup_xref_search(query, book_mode=self.book_mode)
        if results:
            # We take the top result tentatively
            self.log_messages.append(f"Crossref search found {len(results)} results.")
            self.candidates["api"] = self._normalize_crossref(results[0])
            self.candidates['crossref-bib'] = dict_to_bibtex_crossref(results[0])
        else:
            self.log_messages.append("Crossref search returned no results.")


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
            self.log_messages.append("No API results to merge. Using local anchor.")
            self.bib.update(anchor)
            # Downgrade status if anchor is weak (e.g., no author)
            self.status = "REVIEW_NEEDED" if not anchor.get("author") else "SUCCESS"
            self.confidence_score = 40 # Low confidence score for anchor-only
            return

        # Validation: Compare API Title vs Anchor Title
        # We use Token Sort Ratio to handle word reordering
        anchor_title = str(anchor.get("title", "")).lower()
        api_title = str(api.get("title", "")).lower()
        similarity = fuzz.token_sort_ratio(anchor_title, api_title)
        self.confidence_score = similarity
        
        self.log_messages.append(f"Validating API title '{api_title}' against anchor title '{anchor_title}' (Similarity: {similarity}%)")


        if similarity > 80:
            # High Confidence: Accept API
            self.log_messages.append("High confidence match. Accepting API results.")
            self.bib.update(api)
            self.status = "SUCCESS"
        elif similarity > 50:
            # Medium Confidence: Accept API but flag for review
            self.log_messages.append(f"Medium confidence match. Accepting API results but flagging for review.")
            self.bib.update(api)
            self.status = "REVIEW_NEEDED"
        else:
            # Low Confidence: Reject API, use Anchor and flag for review
            self.log_messages.append(
                f"Low confidence match. Rejecting API result and using local anchor."
            )
            self.bib.update(anchor)
            self.status = "REVIEW_NEEDED"

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
        if pub and "date-parts" in pub and pub["date-parts"][0]:
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
            journal_ref = data['journal']

            # Attempt to extract volume, number, pages, and year from journal_ref
            # Pages: e.g., 871-904, S1-S10
            pages_match = re.search(r'(\d+[a-zA-Z]?--?\d+[a-zA-Z]?)', journal_ref)
            if pages_match:
                ans['pages'] = pages_match.group(1)

            # Year: e.g., (1999) - careful not to overwrite more reliable year
            year_match = re.search(r'\((\d{4})\)', journal_ref)
            if year_match and not ans.get('year'): # Only set if year not already present
                ans['year'] = year_match.group(1)

            # Volume and Number:
            # Common patterns: "Vol. X", "149", "no. 3"
            volume_match = re.search(r'(?:Vol\.\s*)?(\d+)', journal_ref)
            if volume_match:
                ans['volume'] = volume_match.group(1)

            number_match = re.search(r'no\.\s*(\d+)', journal_ref)
            if number_match:
                ans['number'] = number_match.group(1)

            # Attempt to clean journal title by removing extracted parts
            cleaned_journal_title = journal_ref
            if ans.get('pages'):
                cleaned_journal_title = cleaned_journal_title.replace(ans['pages'], '').strip(' ,')
            if ans.get('year'):
                cleaned_journal_title = re.sub(r'\(\s*' + re.escape(ans['year']) + r'\s*\)', '', cleaned_journal_title).strip(' ,')
            if ans.get('number'):
                cleaned_journal_title = re.sub(r'no\.\s*' + re.escape(ans['number']), '', cleaned_journal_title).strip(' ,')
            if ans.get('volume'):
                # This is tricky as volume can be just a number. Better to keep it messy for now or rely on specific patterns.
                # For simplicity, let's keep the original journal string as the title for now
                pass
            
            ans['journal'] = cleaned_journal_title.strip(' ,') # Set the cleaned title
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

    def text_path(self, text_dir_path: Path, extractor: str) -> Path:
        """
        Return Path to where text is or will be stored.
        Mirrors the sharded structure: text_dir / first_2_of_fn / fn.md
        where fn starts with first 10 chars of hash.
        """
        if hasattr(self, 'hash') and self.hash:
            h10 = self.hash[:10]
        else:
            # Try to extract hash from current path if it looks sharded
            match = re.search(r'([A-F0-9]{10,})', self.doc_path.name)
            h10 = match.group(1)[:10] if match else "Unknown"

        # Sharded structure: h10[:2] / h10 - ... .md
        parent_shard = self.doc_path.parent.name
        shard = h10[:2]
        stem = self.doc_path.stem
        if not stem.startswith(h10):
            stem = f"{h10}_{stem}"
            
        return text_dir_path / shard / f"{stem}.{extractor}.md"

    def text_exists(self, text_dir_path: Path, extractor: str) -> bool:
        """Check if text file exists."""
        return self.text_path(text_dir_path, extractor).exists()

    def extract_text(self, text_dir_path: Optional[Path] = None, extractor: str = "pdftotext") -> str:
        """
        Extracts text using pdftotext (or pymupdf as fallback/alternative).
        Stores result in self._text and returns it.
        If text_dir_path is provided, also saves to disk.
        """
        if self._text:
            return self._text

        # Check disk if path provided
        if text_dir_path:
            tp = self.text_path(text_dir_path, extractor)
            if tp.exists():
                self._text = tp.read_text(encoding="utf-8")
                return self._text

        suffix = self.doc_path.suffix.lower()
        if suffix != ".pdf":
            raise ValueError(f"Text extraction is only supported for PDF files. Found: {suffix}")

        try:
            logger.info("extract text: %s", self.doc_path)
            
            text = ""
            # Only use pdftotext for PDFs
            if suffix == ".pdf" and extractor == "pdftotext":
                # -raw: content stream order, -nopgbrk: no page breaks
                result = subprocess.run(
                    ["pdftotext", "-raw", "-nopgbrk", str(self.doc_path), "-"],
                    capture_output=True,
                    check=True,
                )
                text = result.stdout.decode("utf-8", errors="replace").replace("\r", "")
            else:
                # Use pymupdf for everything else (it supports EPUB, and many others)
                # It might not support DJVU depending on build, but it's our best shot.
                try:
                    with pymupdf.open(self.doc_path) as doc:
                        text = "\n".join(page.get_text("text") for page in doc)
                except Exception as e:
                    raise RuntimeError(f"PyMuPDF extraction failed for {suffix}: {e}") from e

            if not text.strip():
                raise ValueError("Extracted text is empty.")

            # Fix hyphenation (word-\nword -> wordword)
            text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
            # Normalize unicode
            text = unicodedata.normalize("NFC", text)

            self._text = text

            if text_dir_path:
                tp = self.text_path(text_dir_path, extractor)
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_text(text, encoding="utf-8")

            return text
        except (subprocess.CalledProcessError, FileNotFoundError, Exception) as e:
            logger.error(f"Error extracting text for {self.doc_path.name}: {e}")
            raise  # Re-raise to be caught by batch processor

    def report(self, print_fn=print):
        """
        Prints a comprehensive report of the discovery process, including the
        steps taken, final status, and the resulting BibTeX entry.
        """
        print_fn(f"--- Report for: {self.doc_path.name} ---")
        print_fn("\n[Discovery Log]")
        for msg in self.log_messages:
            print_fn(f"- {msg}")

        print_fn("\n[Result]")
        print_fn(f"- Final Status: {self.status}")
        print_fn(f"- Confidence Score: {self.confidence_score}%")

        print_fn("\n[BibTeX Entry]")
        print_fn(self.bibtex())
        print_fn("--- End of Report ---\n")

    def bibtex(self) -> str:
        if not self.bib["title"]:
            return ""

        # Key - will be over-ridden, but this helps the review process
        cite_key = self.key() #  "Author2099"

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
        mendeley_file = format_mendeley_file(p)
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
        if not authors:
            return ""
        a_list = authors.split(' and ')
        out = []
        for a in a_list:
            hn = HumanName(a)
            name_out = f'{hn.last}, {hn.first}' + (
                f' {hn.middle}' if hn.middle else '')
            out.append(name_out)
        return ' and '.join(out)


def extract_text_for_paths(
    pdf_paths: List[Path],
    text_dir_path: Path,
    extractor: str = "pdftotext",
    workers: int = 4,
    hashes: Optional[Dict[Path, str]] = None,
):
    """Batch extract text from a list of PDF paths."""

    def _task(p):
        try:
            doc = Document(p)
            if hashes and p in hashes:
                doc.hash = hashes[p]
            doc.extract_text(text_dir_path=text_dir_path, extractor=extractor)
            return True, None
        except Exception as e:
            msg = f"Failed to extract text for {p.name}: {e}"
            logger.error(msg)
            return False, str(e)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            tqdm(executor.map(_task, pdf_paths), total=len(pdf_paths), desc="Extracting Text")
        )

    return results


def discover_docs(doc_path: Path, lib):
    """
    Discover documents in doc_path if a directory or about
    doc_path if it is a file.

    """
    # find the files(s)
    if doc_path.is_dir():
        doc_paths = lib.find_docs(doc_path)
        logger.info("Found %s files", len(doc_paths))
    else:
        doc_paths = [doc_path]

    logger.info(f'Found {len(doc_paths)} potential docs for import.')
    print(f'Found {len(doc_paths)} potential docs for import.')

    # process the docs
    docs = []
    # path -> hash
    doc_hashes = hash_many3(doc_paths, lib.config.hash_workers)
    existing_hashes = set(lib.doc_df.hash)
    duplicates = {k: v for k, v in doc_hashes.items() if v in existing_hashes}
    new_doc_hashes = {k: v for k, v in doc_hashes.items() if v not in existing_hashes}
    print(f'{len(new_doc_hashes) = } and {len(duplicates) = }.')

    bibs = [f"% import from {doc_path.absolute()}"]
    for p, h in new_doc_hashes.items():
        if p.suffix.lower() != '.pdf':
            logger.warning(f'WARNING non-pdf {p.name}')
        try:
            logger.info('gathering import info for %s', p)
            doc = Document(p)
            doc.hash = h
            doc.process()
            docs.append(doc)
            bibs.append(doc.bibtex())
        except Exception as e:
            logger.error(f"Error for {p.name}: {e}")

    # for the time being...
    bib_str = "\n".join(bibs)
    return bib_str, docs, duplicates


def elaborate_duplicates(lib, duplicates, trim=True):
    """
    Find the refs corresponding to duplicate hashes from discover_docs.

    Return the ref if available. Non-matched returned in missing_refs

    Docs in missing_refs already exist in the Library but are orphans
    with no associated reference record.
    """
    # find the docs
    docs = lib.doc_df.loc[lib.doc_df.hash.isin(duplicates.values())]
    # find the doc-refs
    dr = lib.ref_doc_df.loc[lib.ref_doc_df.path.isin(docs.path)]
    # find the refs
    refs = lib.ref_df.loc[lib.ref_df.tag.isin(dr.tag)].copy()
    refs['path'] = refs.tag.map(dr.set_index('tag').path.get)
    if trim:
        refs = refs[['tag', 'year', 'author', 'title', 'path']]
    # still missing
    missing_refs = docs.loc[~docs.path.isin(dr.path)]
    # result
    return refs, missing_refs
