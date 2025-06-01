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
from pendulum import local_timezone
from pypdf import PdfReader
from tqdm import tqdm

from . import EMPTY_LIBRARY


class Document():
    """Manage physical document files."""

    def __init__(self, doc_path, text_dir_path=None, extractor: str = "pdftotext"):
        """Create Documents class based on file path."""
        self.doc_path = doc_path
        self._stats = None
        self._text_dir_path = text_dir_path
        self.extractor = extractor
        self.meta_author = ''
        self.meta_author_ex = ''
        self.meta_title = ''
        self.meta_subject = ''
        self.meta_raw = None
        self.meta_cross_ref = ''

    def __repr__(self):
        return f'Document({self.doc_path.name})'

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
            tz = local_timezone()
            self._stats["create"] = pd.to_datetime(self._stats["create"], unit="ns").tz_localize("UTC").tz_convert(tz)
            self._stats["mod"] = pd.to_datetime(self._stats["mod"], unit="ns").tz_localize("UTC").tz_convert(tz)
            self._stats["access"] = pd.to_datetime(self._stats["access"], unit="ns").tz_localize("UTC").tz_convert(tz)
        return self._stats

    def add_meta_data(self, lib=EMPTY_LIBRARY):
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
        self.meta_title = self.meta_raw.get('title', '').strip()
        self.meta_subject = self.meta_raw.get('subject', '').strip()

        # deal with author
        if a != '':
            if a.find(',') < 0:
                # proba first last
                *f, l = a.split(' ')
                a = l + ', ' + ' '.join(f)
        self.meta_author = a
        if not lib.is_empty and a != '':
            self.meta_author_ex = lib.to_name_ex(a, strict=False)
        else:
            self.meta_author_ex = ''
        # meta['raw'] = meta_in
        # Meta = namedtuple('Meta', meta.keys())
        # return Meta(*meta.values())
        self.meta_crossref = self.guess_crossref_query()

    def meta_data_debug(self):
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
        return ( self._text_dir_path
                 / self.doc_path.with_suffix(f'.{self.extractor}.md')
                 .relative_to(self.doc_path.anchor))

    def text_exists(self):
        """Check if text file exists."""
        return self.text_path().exists()

    def extract_text(self):
        """Current best-efforts extraction."""
        txt_out = self.text_path()
        if txt_out.exists():
            return txt_out.read_text(encoding='utf-8')
        txt = self._extract_text_pdftotext()
        txt_out.parent.mkdir(parents=True, exist_ok=True)
        txt_out.write_text(txt, encoding='utf-8')
        return txt

    def extract_text_compare(self):
        """Copmare both methods."""
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

        # these attempts fail because pdftotext does not separate paragraphs
        # Join wrapped lines (non-empty lines split by a single newline)
        # regex = r"(?<!\n)\n(?!\n)"

        # Replace the matched lonely newline with a space
        # text = re.sub(regex, " ", text)

        # # Join wrapped lines (non-empty lines split by a single newline)
        # # Step 1: Protect paragraph breaks (convert two or more \n to a placeholder)
        # text = re.sub(r'\n{2,}', '<<<PARA>>>', text)

        # # Step 2: Remove all remaining (soft) line breaks
        # text = text.replace('\n', ' ')

        # # Step 3: Restore paragraph breaks
        # text = text.replace('<<<PARA>>>', '\n\n')

        # Normalize overlong paragraph breaks: more than 2 newlines → 2.
        # text = re.sub(r'\n{3,}', '\n\n', text)

        # Strip trailinag whitespace from each line.
        # text = "\n".join(line.rstrip() for line in text.splitlines())

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

    def guess_crossref_query(self):
        """Tried to guess a reasonable cross ref query search from the metadata and filename."""
        # Try clean metadata title first
        title = self.meta_title
        title = re.sub(r'Microsoft (PowerPoint|Word)( - )?|Presentation title', '', title, flags=re.IGNORECASE)
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
    print(f'PDFs found: {len(pdfs) = }')

    text_dir_path = Path(text_dir_name)
    pDocument = partial(Document, text_dir_path=text_dir_path, extractor=extractor)

    docs = [pDocument(p) for p in pdfs]
    print(f'doc object created: {len(docs) = } and {len(pdfs) = }')

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
    docs_wo_text = [d for d in docs if not d.text_exists()]
    return docs_wo_text
