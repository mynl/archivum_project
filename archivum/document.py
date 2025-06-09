"""
Document class: manages physical document file.

Meta data, full text extraction etc. For pdf and (?) epub, djvu, dvi and
other formats.
"""

from collections import namedtuple
from functools import partial
from multiprocessing import Pool
from pathlib import Path
import re
import subprocess
import unicodedata

import pymupdf
import pandas as pd
from pypdf import PdfReader
from rapidfuzz.fuzz import ratio
from tqdm import tqdm

from . import EMPTY_LIBRARY


class Document():
    """Manage physical document files."""

    def __init__(self, doc_path, library):
        """Create Documents class based on file path."""
        self.library = library
        self.text_dir_path = library.text_dir_path if library else None
        self.extractor = library.extractor if library else None
        self.tz = library.timezone if library else "Europe/London"
        self.doc_path = doc_path
        self.meta_author = ''
        self.meta_author_ex = ''
        self.meta_title = ''
        self.meta_subject = ''
        self.meta_raw = None
        self.meta_crossref = ''
        self.title_similarity = 0
        self.best_guess_title = ''
        self.best_guess_query = ''
        # from page 1, the cover page
        self.cover_author = ''
        self.cover_title = ''
        self._stats = None
        self._text = ''

    def __repr__(self):
        return f'Document({self.doc_path.name})'

    @property
    def has_text(self):
        return self._text != ""

    def exists(self):
        return self.doc_path.exists()

    @property
    def name(self):
        return self.doc_path.name

    @property
    def stats(self):
        """
        Read file information for the current document.

        Currently PDFs only.
        """
        if self._stats is None:
            stat = self.doc_path.stat(follow_symlinks=True)
            self._stats = {
                "name": self.doc_path.name,
                "path": str(self.doc_path.as_posix()),
                "mod": stat.st_mtime_ns,
                "create": stat.st_ctime_ns,
                "access": stat.st_atime_ns,
                "node": stat.st_ino,
                "links": stat.st_nlink,
                "size": stat.st_size,
                "suffix": self.doc_path.suffix[1:],
            }
            self._stats["create"] = pd.to_datetime(self._stats["create"], unit="ns").tz_localize("UTC").tz_convert(self.tz)
            self._stats["mod"] = pd.to_datetime(self._stats["mod"], unit="ns").tz_localize("UTC").tz_convert(self.tz)
            self._stats["access"] = pd.to_datetime(self._stats["access"], unit="ns").tz_localize("UTC").tz_convert(self.tz)
        return self._stats

    def add_meta_data(self):
        """
        Extract meta data from pdf.

        Return author(ex), subject, title and raw output in namedtuple.
        """
        with pymupdf.open(self.doc_path) as doc:
            self.meta_raw = doc.metadata
        meta_keys = [
            'author',
            'subject',
            'title',
        ]
        a = self.meta_raw.get('author', '').strip()
        self.meta_author_ex = ''
        title = self.meta_raw.get('title', '').strip()
        title = re.sub(r'Microsoft (PowerPoint|Word)( - )?|Presentation title', '', title, flags=re.IGNORECASE)
        self.meta_title = title
        self.meta_subject = self.meta_raw.get('subject', '').strip()

        # deal with author
        if a != '':
            if a.find(',') < 0:
                # proba first last
                *f, l = a.split(' ')
                a = l + ', ' + ' '.join(f)
        self.meta_author = a
        if not self.library.is_empty and a != '':
            self.meta_author_ex = self.library.to_name_ex(a, strict=False)
        else:
            self.meta_author_ex = ''
        self.meta_crossref = self.guess_crossref_query()

    def _meta_data_debug(self):
        """Extract meta data from pdf, verbose testing version."""
        # this confirms the two are about the same...we'll use pymupdf
        reader = PdfReader(self.doc_path)
        raw_meta = reader.metadata
        ans = {}
        m1 = {k.strip("/"): v for k, v in raw_meta.items()}
        m1_keys = ['Author',
                   'Category',
                   'Subject',
                   'Title',
                   'doi',
                   ]
        for k in m1_keys:
            v = m1.get(k, None)
            if v:
                ans[f'm1_{k.lower()}'] = v

        with pymupdf.open(self.doc_path) as doc:
            m2 = doc.metadata
        m2_keys = [
            'author',
            'subject',
            'title',
        ]
        for k in m2_keys:
            v = m2.get(k, None)
            if v.title() in m1:
                assert m1[v.title()] == v
            if v:
                ans[f'm2_{k}'] = v
        return ans, m1, m2
        # return {'pdf_reader': m1, 'frtiz': m2}

    def text_path(self):
        """Return Path to where text is or will be stored."""
        if self.text_dir_path is None:
            return None
        else:
            return (self.text_dir_path
                    / self.doc_path.with_suffix(f'.{self.extractor}.md')
                    .relative_to(self.doc_path.anchor))

    def text_path_exists(self):
        """Check if text file exists."""
        return False if self.text_path() is None else self.text_path().exists()

    def extract_text(self):
        """Current best-efforts extraction."""
        if self._text != "":
            return self._text
        if self.has_text:
            txt_out = self.text_path()
            if txt_out.exists():
                self._text = txt_out.read_text(encoding='utf-8')
                return self._text
        self._text = self._extract_text_pdftotext()
        if self.text_dir_path is not None:
            txt_out.parent.mkdir(parents=True, exist_ok=True)
            txt_out.write_text(self._text, encoding='utf-8')
        return self._text

    def _extract_text_compare(self):
        """Compare both methods."""
        fr = self._extract_text_pymupdf()
        fr = ''
        tt = self._extract_text_pdftotext()
        self.doc_path.with_suffix('.pymupdf.md').write_text(fr, encoding='utf-8')
        self.doc_path.with_suffix('.pdftotext.md').write_text(tt, encoding='utf-8')
        DocText = namedtuple('DocText', 'pymupdf,pdftotext')
        return DocText(fr, tt)

    def _extract_text_pymupdf(self):
        """Cross referencing between docs and refs."""
        with pymupdf.open(self.doc_path) as doc:
            return "\n".join(page.get_text("text", sort=True) for page in doc)

    def _extract_text_pdftotext_old(self):
        """Cross referencing between docs and refs."""
        result = subprocess.run(
            ["pdftotext", "-layout", "-raw", "-nopgbrk", str(self.doc_path), "-"],
            capture_output=True,
            check=True
        )
        text = result.stdout.decode("utf-8", errors="replace")
        text = re.sub(r'\r', r'', text)
        return text

    def _extract_text_pdftotext(self) -> str:
        """Extract and clean text from a PDF using pdftotext."""

        # Run pdftotext with UTF-8 output to stdout, suppressing page breaks.
        result = subprocess.run(
            ["pdftotext", "-raw", "-nopgbrk", str(self.doc_path), "-"],
            capture_output=True,
            check=True
        )

        # Decode and normalize line endings to Unix style (\n).
        text = result.stdout.decode("utf-8", errors="replace").replace('\r', '')

        # Remove hyphenated line breaks: "hyphen-\nated" → "hyphenated"
        text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)

        # Normalize Unicode (e.g., é as a single composed character).
        text = unicodedata.normalize("NFC", text)

        return text

    @staticmethod
    def _looks_like_title(text):
        """Test if text looks like a title."""
        if not text or not isinstance(text, str):
            return False
        text = text.strip()
        # Reject if mostly digits, file junk, or boilerplate
        if re.fullmatch(r"[\d\s\-_.]+", text):
            return False
        if len(text) < 5 or len(text.split()) < 2:
            return False
        return True

    @staticmethod
    def _extract_year(text):
        """Extract year from text."""
        if not isinstance(text, str):
            return None
        m = re.search(r"\b(19|20)\d{2}\b", text)
        return m.group() if m else None

    @staticmethod
    def _de_slugify(text):
        return text.replace('_', ' ').strip()

    def get_best_guess_title(self):
        """Extract best guess for a title using meta data, page 1, and file name."""
        self.title_similarity = ratio(self.meta_title, self.cover_title)
        if self.title_similarity > 90:
            return self.meta_title
        elif len(self.meta_title) > len(self.cover_title) and len(self.meta_title) > 10:
            return self.meta_title
        elif len(self.cover_title) > len(self.meta_title) and len(self.cover_title) > 10:
            return self.cover_title
        # no obvious contender
        fn = self.doc_path.name.replace('.pdf', '').replace('_', ' ')
        if len(fn.split(' ')) > 3:
            return fn
        else:
            return ''

    def get_guess_crossref_query(self):
        """Tried to guess a reasonable cross ref query search from the metadata and filename."""
        # Try clean metadata title first
        title = self.meta_title
        author = self.meta_author_ex or self.meta_author
        subject = self.meta_subject
        fname = self.doc_path.stem

        year = self._extract_year(subject) or self._extract_year(title) or self._extract_year(fname)

        author = query = ''
        if self._looks_like_title(title):
            query = title
        elif self._looks_like_title(subject):
            query = subject
        elif self._looks_like_title(self._de_slugify(fname)):
            query = self._de_slugify(fname)
        else:
            return ''  # nothing usable

        # Prefer to include author if it's more than 1 word
        if author and len(author.split()) >= 2:
            query = f"{author} {query}"

        if year:
            query = f"{query} {year}"

        return query

    def add_cover_title_author(self):
        """
        Try to guess authors and title from first page of pdf

        Heuristics for finding title and author
            Title is usually the largest text block near the top.
            Authors often follow the title and may include email/affiliations.
            Try regular expressions for email/domain to help anchor authors.
        """
        doc = pymupdf.open(self.doc_path)
        first_page = doc[0]
        blocks = first_page.get_text("dict")["blocks"]

        texts = []
        for b in blocks:
            for line in b.get("lines", []):
                for span in line["spans"]:
                    texts.append((span["size"], span["text"]))

        # Sort by size descending
        texts.sort(reverse=True)

        title = texts[0][1].strip() if texts else ""
        possible_authors = [t[1].strip() for t in texts[1:4]]
        self.cover_author = possible_authors
        self.cover_title = title

    def uber(self):
        """
        Run all edits to enhance a Document.

        Read meta data, extract title and author from cover page,
        determine best guess title, and best guess query.
        """
        self.stats;
        self.add_meta_data()
        self.add_cover_title_author()
        bgt = self.get_best_guess_title()
        crc = self.get_guess_crossref_query()
        return bgt, crc

# extract text
def _process_single_pdf(doc_path: Path, text_dir_path: Path, extractor: str):
    try:
        doc = Document(doc_path, text_dir_path=text_dir_path, extractor=extractor)
        doc.extract_text()
        return doc_path, True
    except Exception as e:
        return doc_path, False, str(e)


def extract_text(pdf_paths: list[Path], text_dir_path: Path, extractor: str = 'pdftotext', workers: int = None):
    """Extract text from all pdfs using multiprocessing with tqdm."""
    with Pool(processes=workers) as pool:
        func = partial(_process_single_pdf, text_dir_path=text_dir_path, extractor=extractor)
        results = list(tqdm(pool.imap_unordered(func, pdf_paths), total=len(pdf_paths)))

    # summary
    success = [p for p, ok, *_ in results if ok]
    failure = [(p, err) for p, ok, *err in results if not ok]
    TextResult = namedtuple('TextResult', 'success,failure')
    return TextResult(success, failure)


def pdf_dir_to_text(lib_dir_name, text_dir_name='\\temp\\pdf-full-text', extractor='pdftotext'):
    """Extract text from all PDFs in lib_dir_name (recursive)."""
    pdfs = list(Path(lib_dir_name).rglob('*.pdf'))
    print(f'PDFs found: {len(pdfs)=}')

    text_dir_path = Path(text_dir_name)
    pDocument = partial(Document, text_dir_path=text_dir_path, extractor=extractor)

    docs = [pDocument(p) for p in pdfs]
    print(f'doc object created: {len(docs)=} and {len(pdfs)=}')

    # do the extraction
    result = extract_text(pdfs, text_dir_path)

    return pdfs, docs, result


def find_pdfs(*dir_names):
    """Find all pdfs in list dir_names, return list of Paths."""
    ans = []
    # size of list before and after each directory to show progress
    lb = la = 0
    for dn in dir_names:
        lb = len(ans)
        p = Path(dn)
        if p.exists():
            ans.extend(p.rglob('*.pdf'))
            la = len(ans)
            print(f'Found {la - lb} files in {p.name} and subfolders')
        else:
            print(f'{p} does not exist; skipping')
    return ans


def find_missing_txt(pdf_paths, text_dir_name='\\temp\\pdf-full-text', extractor='pdftotext'):
    """Find pdfs with missing text files."""
    text_dir_path = Path(text_dir_name)
    pDocument = partial(Document, text_dir_path=text_dir_path, extractor=extractor)

    docs = [pDocument(p) for p in pdf_paths]
    docs_wo_text = [d for d in docs if not d.text_path_exists()]
    return docs_wo_text
