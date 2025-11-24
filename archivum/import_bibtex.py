# coding: utf-8
"""
BibTeX import helpers for archivum.

This module uses a Bib2df_Incremental, derived from the Mendeley porting
logic to incrementally import new references from a BibTeX file into an
existing Library.

Each import run is recorded under a timestamped directory so that
the original .bib and a copy of the PDFs are preserved and the
ETL is, in principle, replayable.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from types import MethodType
from typing import Optional

import datetime as dt
import shutil
from IPython.display import display

# import latexcodec
import Levenshtein
import numpy as np
import pandas as pd

from . import BASE_DIR, APP_NAME
from .utilities import (make_partial_GT, remove_accents,
                        accent_mapper_dict, safe_int)
from . trie import Trie

logger = logging.getLogger(__name__)

fGT = make_partial_GT()

def qd(df, **kwargs):
    """Handy local qd."""
    display(fGT(df, **kwargs))

# GEMINI CODE
def _normalize_title(s: str) -> str:
    """Simple normalization for title comparison."""
    if not isinstance(s, str):
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _possible_duplicate(ref_row, existing_ref_df: pd.DataFrame) -> str | None:
    """
    Heuristic duplicate check: by DOI (if present) and by normalized title.
    Returns a short message if something looks like a duplicate.
    """
    doi = str(ref_row.get("doi", "") or "").strip().lower()
    title_norm = _normalize_title(ref_row.get("title", ""))

    # DOI check
    if doi and "doi" in existing_ref_df.columns:
        mask = existing_ref_df["doi"].astype(str).str.lower().str.strip() == doi
        if mask.any():
            tags = existing_ref_df.loc[mask, "tag"].tolist()
            return f"Possible duplicate by DOI; existing tag(s): {tags}"

    # Title check
    if title_norm:
        if "_title_norm" not in existing_ref_df.columns:
            existing_ref_df["_title_norm"] = existing_ref_df["title"].map(_normalize_title)
        mask = existing_ref_df["_title_norm"] == title_norm
        if mask.any():
            tags = existing_ref_df.loc[mask, "tag"].tolist()
            return f"Possible duplicate by title; existing tag(s): {tags}"

    return None


def _summarize_entry(label: str, row: pd.Series, drop_fields: set[str]) -> str:
    """
    Build a short, readable multi-line summary of a reference row,
    dropping very long or uninteresting fields.
    """
    keep = []
    for key, val in row.items():
        if key in drop_fields:
            continue
        if pd.isna(val):
            continue
        v = str(val).strip()
        if not v:
            continue
        keep.append(f"{key}: {v}")
    body = "\n  ".join(keep)
    return f"{label}:\n  {body}"


def _write_skipped_bibtex(
    skipped_rows: list[pd.Series],
    path: Path,
    drop_fields: set[str],
) -> None:
    """
    Write a minimal BibTeX file with the skipped entries.

    Uses the ported/raw-style rows (including 'type' and 'tag') and drops
    long or unwanted fields.
    """
    lines: list[str] = []
    for row in skipped_rows:
        entry_type = str(row.get("type", "article") or "article")
        tag = str(row.get("tag", "untagged") or "untagged")
        lines.append(f"@{entry_type}{{{tag},")
        for key, val in row.items():
            if key in drop_fields:
                continue
            if key in {"type", "tag"}:
                continue
            if pd.isna(val):
                continue
            v = str(val).strip()
            if not v:
                continue
            lines.append(f"  {key} = {{{v}}},")
        lines.append("}\n")

    path.write_text("\n".join(lines), encoding="utf-8")



@dataclass
class ImportResult:
    """
    Summary of a single BibTeX import run.
    """

    run_dir: Path
    added_refs: int
    added_docs: int


def _resolve_imports_root(imports_dir: Optional[Path]) -> Path:
    """
    Resolve the root directory under which import runs are recorded.

    If an explicit path is provided it is used as-is, otherwise a
    default under BASE_DIR is created.
    """
    if imports_dir is not None:
        root = imports_dir
    else:
        root = BASE_DIR / "imports"

    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_import(
    bibtex_path: Path,
    library,
    imports_dir: Optional[Path] = None,
    pdf_dir: Optional[Path] = None,
    audit_mode: bool = False,
) -> ImportResult:
    """
    Interactive import of references/documents from a BibTeX file into an existing Library.

    For each candidate reference:
      * display a "raw" ported view (no abstracts, etc.),
      * display the rationalized view (ref_df row),
      * flag possible duplicates,
      * prompt user to [a]ccept / [s]kip / [q]uit.

    Accepted items are appended to the library. Skipped items are written
    to a BibTeX file in the run directory for later manual editing.
    """
    bibtex_path = Path(bibtex_path).expanduser().resolve()
    logger.info(f'{bibtex_path = }')
    imports_root = _resolve_imports_root(imports_dir)

    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = imports_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    # Keep the original .bib for replay/audit.
    bib_copy = run_dir / bibtex_path.name
    shutil.copy2(bibtex_path, bib_copy)

    # Where to look for PDFs referenced in this BibTeX file.
    if pdf_dir is None:
        pdf_dir = Path(library.config.pdf_dir_name)
    else:
        pdf_dir = Path(pdf_dir).expanduser().resolve()

    logger.info("Importing BibTeX from %s", bibtex_path)
    logger.info("PDF source directory: %s", pdf_dir)
    logger.info("Import run directory: %s", run_dir)

    bib = Bib2df_Incremental(bibtex_path, pdf_dir=pdf_dir,
        reference_library=library, fillna=True, audit_mode=audit_mode)

    # "Raw/ported" view (includes 'file'); cleaned reference view (no file).
    raw_df = bib.ported_df.copy()
    ref_df = bib.ref_df.copy()
    doc_df = bib.doc_df.copy()
    ref_doc_df = bib.ref_doc_df.copy()

    existing_ref_df = library.ref_df.copy()
    existing_doc_df = library.doc_df.copy()
    existing_ref_doc_df = library.ref_doc_df.copy()

    # We treat "new candidate" as "tag not in existing ref_df".
    existing_tags = set(existing_ref_df["tag"])
    candidate_mask = ~ref_df["tag"].isin(existing_tags)
    # index is tag - want to keep tht
    candidate_ref_df = ref_df[candidate_mask].reset_index(drop=True)

    from IPython.display import display
    # qd(raw_df.head(10), caption='raw_df', show_index=True)
    # raw_df.head(10)

    # qd(ref_df.head(10), caption='ref_df', show_index=True)
    # ref_df.head(10)

    # qd(doc_df.head(10), caption='doc_df', show_index=True)
    # doc_df.head(10)

    # qd(ref_doc_df.head(10), caption='ref_doc_df', show_index=True)
    # ref_doc_df.head(10)

    # XXXX
    qd(candidate_ref_df.head(10), caption='candidate_ref_df', show_index=True)

    # Align raw_df by tag so we can show "raw" vs "rationalized".
    qd(raw_df, caption="raw_df")

    raw_by_tag = raw_df # .set_index("tag")

    # Which fields to suppress in summaries and in the skipped BibTeX.
    drop_fields = set(Bib2df_Incremental.omitted_bibtex_fields) | {
        "abstract",
        "keywords",
        "arc-source",
        "_title_norm",
    }

    accepted_tags: list[str] = []
    skipped_raw_rows: list[pd.Series] = []

    for _, ref_row in candidate_ref_df.iterrows():
        display(qd(ref_row))
        tag = ref_row["tag"]
        print("\n" + "=" * 80)
        print(f"Candidate tag: {tag}")

        # Raw/ported view.
        try:
            raw_row = raw_by_tag.loc[tag]
        except KeyError:
            raw_row = ref_row  # fallback; should be rare

        print(_summarize_entry("Raw (ported) entry", raw_row, drop_fields))

        # Rationalized/ref_df view.
        print()
        print(_summarize_entry("Rationalized entry", ref_row, drop_fields))

        dup_msg = _possible_duplicate(ref_row, existing_ref_df)
        if dup_msg:
            print()
            print(f"WARNING: {dup_msg}")

        # Simple prompt loop.
        while True:
            ans = input("[a]ccept / [s]kip / [q]uit? [a/s/q]: ").strip().lower()
            if ans in {"", "a", "s", "q"}:
                break
            print("Please enter 'a', 's', or 'q'.")

        if ans in {"", "a"}:
            accepted_tags.append(tag)
        elif ans == "s":
            skipped_raw_rows.append(raw_row)
        elif ans == "q":
            skipped_raw_rows.append(raw_row)
            # break out of the outer loop
            break

    # If nothing accepted, just write skipped bibtex (if any) and return.
    if not accepted_tags:
        if skipped_raw_rows:
            pip_path = run_dir / "bibtex-pip.bib"
            _write_skipped_bibtex(skipped_raw_rows, pip_path, drop_fields)
            logger.info("No references accepted; wrote skipped entries to %s", pip_path)
        return ImportResult(run_dir=run_dir, added_refs=0, added_docs=0)

    accepted_tags_set = set(accepted_tags)

    # Build ref/doc/ref_doc additions based on accepted tags.
    ref_add = ref_df[ref_df["tag"].isin(accepted_tags_set)].copy()

    # Restrict ref_doc and doc_df to the accepted refs.
    rd_candidates = ref_doc_df[ref_doc_df["tag"].isin(accepted_tags_set)].copy()
    new_paths = set(rd_candidates["path"])
    doc_add_candidates = doc_df[doc_df["path"].isin(new_paths)].copy()

    # Drop any docs we already know about.
    existing_paths = set(existing_doc_df["path"])
    doc_add = doc_add_candidates[~doc_add_candidates["path"].isin(existing_paths)].copy()

    # And restrict ref_doc to only the retained docs.
    kept_paths = set(doc_add["path"])
    rd_add = rd_candidates[rd_candidates["path"].isin(kept_paths)].copy()

    # Append to existing dataframes.
    ref_out = pd.concat([existing_ref_df, ref_add], ignore_index=True)
    doc_out = pd.concat([existing_doc_df, doc_add], ignore_index=True)
    ref_doc_out = pd.concat([existing_ref_doc_df, rd_add], ignore_index=True)

    ref_path = library.config_path.with_suffix(f".{APP_NAME}-ref-feather")
    doc_path = library.config_path.with_suffix(f".{APP_NAME}-doc-feather")
    ref_doc_path = library.config_path.with_suffix(f".{APP_NAME}-ref-doc-feather")

    # persist TODO
    # ref_out.to_feather(ref_path)
    # doc_out.to_feather(doc_path)
    # ref_doc_out.to_feather(ref_doc_path)

    # Update in-memory library.
    library._ref_df = ref_out
    library._doc_df = doc_out
    library._ref_doc_df = ref_doc_out

    # Write skipped entries (if any) to a "pip" BibTeX file.
    if skipped_raw_rows:
        pip_path = run_dir / "bibtex-pip.bib"
        _write_skipped_bibtex(skipped_raw_rows, pip_path, drop_fields)
        logger.info("Wrote %d skipped entries to %s", len(skipped_raw_rows), pip_path)

    return ImportResult(
        run_dir=run_dir,
        added_refs=len(ref_add),
        added_docs=len(doc_add),
    )


def suggest_tag(df):
    """Suggest tags fore each row of df."""
    a = df.author.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().str.replace(r' |\.|\{|\}|\-', '', regex=True)
    e = df.editor.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().replace(r' |\.|\{|\}|\-', '', regex=True)
    y = df['year'].map(str)  # was safe_int, but that's not needed or sensible
    return np.where(a != '', a + y, np.where(e != '', e + y, 'NOTAG'))



## ==================================================================
class Bib2df_Incremental():
    """
    Bibtex file to dataframe - library aware.

    Code started as a copy of mendeley_port.Bib2df, adjusted for incremental,
    library-aware use. .
    """

    # for de-texing single characters in braces
    _r_brace1 = re.compile(r'{(.)}')
    _r_brace2 = re.compile(r'{{(.)}}')

    # base columns used by the app for quick output displays
    _base_cols = ['tag', 'type', 'author', 'title', 'year', 'journal', 'file']

    # base columns expected by import_bibtex_file
    _base_fields = ['title', 'journal', 'publisher', 'institution', 'booktitle', 'address',
              'editor', 'mendeley-tags', 'edition']


    # =====================================================================================================
    # user defined mappers: these can be customized
    # _char_map is less likely to be changed: it is applied to the raw text read from the bibtex file
    _char_unicode_dict = {
        '“': '"',    # left double quote
        '”': '"',    # right double quote
        '„': '"',    # low double quote
        '«': '"',    # double angle quote
        '»': '"',
        '′': "'",
        '‘': "'",    # left single quote
        '’': "'",    # right single quote
        '‚': "'",    # low single quote
        '′': "'",    # prime
        '‵': "'",    # reversed prime
        '‹': "'",    # single angle quote
        '›': "'",

        '\u00a0': ' ',    # non-breaking space
        '\u200b': '',     # zero-width space
        '\ufeff': '',     # BOM
    }

    _char_map = str.maketrans(_char_unicode_dict)

    # _re_subs is also applied to raw text to adjust en and em dashes.
    _re_subs = {
        '–': '--',    # en dash → hyphen
        '—': '---',    # em dash → hyphen
    }
    _re_subs_compiled = re.compile('|'.join(map(re.escape, _re_subs)))

    # for mapping the edition bibtex field, used in import_bibtex_file
    edition_mapper = {
        "First": "First",
        "2": "Second",
        "2nd": "Second",
        "Second": "Second",
        "3": "Third",
        "3rd": "Third",
        "Third": "Third",
        "4": "Fourth",
        "4th": "Fourth",
        "5": "Fifth",
        "5th": "Fifth",
        "Fifth": "Fifth",
        "Seventh": "Seventh",
        "Sixth": "Sixth",
        "Ninth": "Ninth",
        "10": "Tenth",
    }

    # used by import_bibtex_file to drop fields from input bibtex file
    omitted_bibtex_fields = ['abstract', 'annote', 'issn', 'isbn', 'archivePrefix',
                               'arxivId', 'eprint', 'pmid',
                               'primaryClass', 'series', 'chapter', 'school',
                               'organization', 'howpublished', 'keywords'
                               ]

    # end customizable mappers
    # =====================================================================================================

    def __init__(self, *, bibtex_file_path, pdf_dir, reference_library,
                fillna=True):
        """
        Read Path p into bibtex df, pdf_dir is a Path to pdf files (must exist)

        This class is very property driven...dataframes are created
        when needed. Eg the audit dir is only created if you get to that
        point.

        The one-time Mendeley porting version of this finds all
        documents in pdf_dir. We don't need to do that here, we
        know the docs will exist in the right place. However,
        for the time being will keep the code, but only transfer
        over files as needed.

        afile = an actual file
        vfile = a named reference in the bibtex file that may not correspond to an afile

        pdf_dir is where the afile documents live.

        Use fillna=False to use the contents functions (see missing fields).

        Note: this function is "bibtex" file based and creates a dataframe, whereas
        the Library class is dataframe based and creates a bibtex file.

        Audit mode is just ALWAYS ON - you can delete the files if you like!
        """
        self.bibtex_file_path = bibtex_file_path
        assert self.bibtex_file_path.exists(), 'Bibtex file must exist'
        self.pdf_dir = pdf_dir
        assert self.pdf_dir.exists(), 'PDF directory does not exist'
        self.reference_library = reference_library
        self.fillna = fillna

        # for properties
        self._df = None
        self._author_map_df = None
        self.all_unicode_errors = None
        self._proto_ref_doc_df = None
        self._doc_df = None
        self._ref_doc_df = None
        self._ref_df = None
        self._best_match_df = None
        self._ref_no_doc = None
        self._ported_df = None    # the "raw" ported df, includes file column, but otherwise like ref_df
        self._database = None
        self.last_missing_vfiles = None
        self._audit_dir_path = None

        # timestamp for audit files
        self.timestamp = dt.datetime.now().strftime("%Y-%m-%d_at_%H-%M-%S")

    @property
    def audit_dir_path(self):
        """
        Time-stamped location to save audit data.

        If created, copies the input bibtex file (hard link).
        """
        if self._audit_dir_path is None:
            self._audit_dir_path = BASE_DIR / "imports" / self.timestamp
            # ensure it exists
            self._audit_dir_path.mkdir(parents=True, exist_ok=True)
            logger.info('Created audit path at %s', str(self._audit_dir_path))
            # audit the bibtex input file
            p_ = self._audit_dir_path / self.bibtex_file_path.name
            if p_.exists():
                logger.warning('REALLY WEIRD - audit of input bibtex already exists.')
                p_.unlink()
            p_.hardlink_to(self.bibtex_file_path)
        return self._audit_dir_path

    @property
    def df(self):
        """DataFrame of raw(ish) information read directly from bibtex file."""
        if self._df is None:
            logger.info('df creating property ***************************************')
            # read text
            self.txt = self.bibtex_file_path.read_text(encoding='utf-8').translate(self._char_map)
            # basic remapping for dashes
            self.txt, n = self._re_subs_compiled.subn(lambda m: self._re_subs[m.group()], self.txt)
            logger.info(f'uber regex sub found {n = } replacements')

            # split into references
            self.stxt = re.split(r'^@', self.txt, flags=re.MULTILINE)

            # parse each line
            parsed_lines = map(self.parse_line, self.stxt[1:])
            self._df = pd.DataFrame(parsed_lines)

            # the bibtex row 0 is mendeley junk
            # doing this keeps stxt and the df on the same index
            self._df.index = range(1, 1 + len(self._df))
            if self.fillna:
                self._df = self._df.fillna('')
        return self._df

    @staticmethod
    def parse_line(entry):
        result = {}

        # Step 1: Extract type and tag
        # windows GS bibtex pastes come in with \r\n
        entry = entry.replace('\r\n  ', '\n')
        header_match = re.match(r'@?(\w+)\{([^,]+),', entry)
        if not header_match:
            logger.error("Error: Unable to parse entry header.")
            return None
        result['type'], result['tag'] = header_match.groups()

        # Step 2: Remove header and final trailing '}'
        body = entry[header_match.end():].strip()
        if body.endswith('}'):
            body = body[:-1].strip() + ",\n"

        for m in re.finditer(r'([a-zA-Z\-]+) = {(.*?)},\n', body, flags=re.DOTALL):
            try:
                k, v = m.groups()
                result[k] = v
            except ValueError:
                logger.info('going slow')
                return Bib2df_Incremental.parse_line_slow(entry)
        return result

    @staticmethod
    def parse_line_slow(entry):
        result = {}

        # Step 1: Extract type and tag
        header_match = re.match(r'(\w+)\{([^,]+),', entry)
        if not header_match:
            logger.error("Error: Unable to parse entry header.")
            return None
        result['type'], result['tag'] = header_match.groups()

        # Step 2: Remove header and final trailing '}'
        body = entry[header_match.end():].strip()
        if body.endswith('}'):
            body = body[:-1].strip()

        # Step 3: Find all key = { positions
        matches = list(re.finditer(r'([a-zA-Z\-]+) = \{', body))
        n = len(matches)

        for i, match in enumerate(matches):
            key = match.group(1)
            val_start = match.end()
            val_end = matches[i + 1].start() if i + 1 < n else len(body)

            # Strip off the trailing "}," (assumes always ",\n" after value)
            value = body[val_start:val_end].rstrip().rstrip(',')
            if value.endswith('}'):
                value = value[:-1].rstrip()

            result[key] = value

        return result

    def contents(self, ported=False, verbose=False):
        """Summary contents info on df - distinct values, fields etc."""
        ans = []
        if ported:
            df = self.ported_df
        else:
            df = self.df
        for c in df.columns:
            vc = df[c].value_counts()
            nonna = len(df) - sum(df[c].isna())
            ans.append([c, nonna, len(vc)])
            if verbose:
                print(c)
                print('=' * len(c))
                print(f'{len(vc)} distinct values')
                print(vc.head(10))
                print('-' * 80)
                print()
        cdf = pd.DataFrame(ans, columns=['column', 'nonna', 'distinct'])
        return cdf

    @property
    def author_map_df(self):
        """
        DataFrame of author name showing a transition to a normalized form.

        Adjusts for initials (puts periods in), takes the longest ! name
        using a Trie, adjusts for accents (guess work!)
        """
        if self._author_map_df is None:
            df = pd.DataFrame({'original': self.distinct('author')})
            self.last_decode = []
            df['unicoded'] = df.original.map(self.tex_to_unicode).str.replace('.', '')
            # space out initials Mild, SJM -> Mild, S J M; works for two of three consecutive initials
            df['spaced'] = df.unicoded.str.replace(r'(?<=, )([A-Z]{2,3})\b',
                                                   lambda m: ' '.join(m.group(1)),
                                                   regex=True)

            # diverge from Bib2df: use the reference library
            t = Trie()
            # distinct returns a set
            ref_authors = self.distinct("author", self.reference_library.ref_df)
            logger.info(f'Building author Trie from reference library, {len(ref_authors)} distinct authors')
            for name in ref_authors:
                t.insert(name)
            # mapping will go from name to longest completion
            mapping = {}
            # authors in self.df
            a = self.distinct("author", self.df)
            logger.info(f'Import contains {len(ref_authors)} distinct authors -> remapping')
            for name in a:
                m = t.longest_unique_completion(name, strict=False)
                if m != name:
                    # have found a better version
                    mapping[name] = m
            df['longest'] = df.spaced.replace(mapping)
            accent_mapper = accent_mapper_dict(df.longest)
            df['accents'] = df.longest.replace(accent_mapper)
            # initial  periods
            df['proposed'] = df.accents.str.replace(r'(\b)([A-Z])( |$)', r'\1\2.\3', case=True, regex=True)
            logger.info(f'Field: authors\nDecode errors: {len(self.last_decode) = }')
            self._author_map_df = df
            # debug
            self.trie = t
            self.mapping = mapping
            self.accent_mapper = accent_mapper
        return self._author_map_df

    def distinct(self, column_name, source_df=None, source_name='ref_df'):
        """Return distinct occurrences of col c."""
        # signature changed from mendeley version
        if source_df is not None:
            df = source_df
        else:
            if source_name == 'ref_df' and self._ref_df is None:
                logger.warning('*** distinct with ref_df not set, defaulting to df')
                source = 'df'
            df = getattr(self, source)
        if df is None:
            return df
        if column_name == 'author':
            return sorted(
                set(author.strip() for s in df.author.dropna() for author in s.split(" and "))
            )
        else:
            return sorted(set([i for i in df[column_name] if i != '']))

    def tex_to_unicode(self, s_in: str) -> str:
        """
        Tex codes to Unicode for a string and removing braces with single character.

        Errors are added to self.last_decode and looked up in the dictionary
        self.errors_mapper. Work iteratively: run, look at errors, add or update
        entries in self.errors_mapper.

        Dropped ref to error_mapper from mendeley verison.
        """
        if pd.isna(s_in):
            return s_in
        try:
            s = self._r_brace2.sub(r'\1', s_in.encode('latin1').decode('latex'))
            s = self._r_brace1.sub(r'\1', s)
            if s.find(',') > 0 and s == s.upper():
                # title case what appear to be names (comma) that are all caps
                s = s.title()
            return s
        except ValueError as e:
            self.last_decode.append(s_in)
            return s

    def author_last_multiple_firsts(self):
        """Last names with multiple firsts, showing the parts."""
        df = self.author_map_df
        df[['last', 'rest']] = df['proposed'].str.split(',', n=1, expand=True)
        df['rest'] = df['rest'].str.strip()

        return (df.fillna('')
                .groupby('last')
                .apply(lambda x:
                pd.Series([len(x), sorted(set(x.rest))], index=('n', 'set')),
                include_groups=False)
                .query('n>1'))

    def author_mapper(self):
        """dict mapper for author name."""
        # dropped manual fixes
        return  {k: v for k, v in self.author_map_df[['original', 'proposed']].values}

    def map_authors(self, df_name):
        """Actually apply the author mapper to the author column."""
        df = getattr(self, df_name)
        am = self.author_mapper()

        def f(x):
            sx = x.split(' and ')
            msx = map(lambda x: am.get(x, x), sx)
            return ' and '.join(msx)

        df.author = df.author.map(f)
        # audit
        amdf = pd.DataFrame(am.items(), columns=['key', 'value'])
        self.save_audit_file(amdf, '.author-mapping')

    def import_bibtex_file(self):
        """
        Normalize each text-based field.

        Runs through each task in turn, see comments.

        For the initial port choose run_add_hoc=True, but
        for incremental updates use False.

        Updated to remove ad_hoc adjustments, dropped extract citations
        from abstract, tags use library, etc.
        """
        logger.info('Running import_bibtex_file to create ported_df')
        kept_fields = [i for i in self.df.columns if i not in self.omitted_bibtex_fields]
        self._ported_df = self.df[kept_fields].copy()

        # ============================================================================================
        # author: initials, extend, accents
        self.map_authors('_ported_df')

        # ensure other edited fields are present
        # this may not be the case for small imports
        for f in self._base_fields:
            if f not in self._ported_df:
                logger.info('Imported df missing %s - adding', f)
                # probably a string?
                self._ported_df[f] = ""

        # ============================================================================================
        # de-tex other text fields
        self.all_unicode_errors = {}
        for f in ['title', 'journal', 'publisher', 'institution', 'booktitle', 'address',
                  'editor', 'mendeley-tags']:
            self.last_decode = []
            self._ported_df[f] = self._ported_df[f].map(self.tex_to_unicode)
            if len(self.last_decode):
                logger.info(f'\tField: {f}\t{len(self.last_decode) = }')
                self.all_unicode_errors[f] = self.last_decode.copy()
            logger.info(f'Fixed {f}')

        # audit unicode errors
        ans = []
        for k, v in self.all_unicode_errors.items():
            for mc in v:
                ans.append([k, mc])
        temp = pd.DataFrame(ans, columns=['field', 'miscode'])
        self.save_audit_file(temp, '.tex-unicode-errors')

        # ============================================================================================
        # keywords
        # paper's key words - never used these, they are included in omitted_bibtex_fields
        # add code here for alternative treatment

        # ============================================================================================
        # mendeley-tags: these are things like my WangR or Delbaen or PMM
        # nothing to do here --- just carry over

        # ============================================================================================
        # citations: figure number of citations from my notes in the abstract - DROPPED
        # dict index -> number of citations, default = 0

        # ============================================================================================
        # edition: normalize edition field
        self._ported_df.edition = self._ported_df.edition.replace(self.edition_mapper)

        # ============================================================================================
        # tags: normalize and resolve duplicate TAGS
        self.map_tags()

        # ============================================================================================
        # files: files are entirely separately managed, field just pulled over
        # see code in file_field_df

        # set tag as the index
        self._ported_df = self._ported_df.set_index('tag')

        # ============================================================================================
        # final checks and balances, and write out info
        self.save_audit_file(self.df, '.raw-df')
        self.save_audit_file(self._ported_df, '.ported-df')
        import_info = pd.DataFrame({
            'created': str(self.timestamp),
            'bibtex_file': self.bibtex_file_path.resolve(),
            'raw_entries': len(self.df),
            'ported_entries': len(self._ported_df)
        }.items(), columns=['key', 'value'])
        self.save_audit_file(import_info, '.audit-info')
        return import_info

    # def extract_citations(self):
    #     """Extract manually entered citations from abstract field."""
    # dropped

    def show_unicode_errors(self):
        """Accumulated Unicode errors."""
        if self.all_unicode_errors is None:
            return None
        ans = set()
        for k, v in self.all_unicode_errors.items():
            ans = ans.union(set([c for line in v for c in line if len(c.encode('utf-8')) > 1]))
        return ans

    def no_file(self):
        """Entries with no files listed."""
        return self.df.loc[self.df.file == '', self._base_cols]

    def map_tags(self, df_name='ported_df'):
        """
        Remap the tags into standard AuthorYYYY[a-z] format for named df.

        Saves a dataframe showing what was done as part of import.

        Updated to use reference library.
        """
        # pattern to remove non-bibtex like characters
        df = getattr(self, df_name)[['author', 'editor', 'year', 'tag', 'title']].copy()
        # figure out what the tag "should be"
        pat = r" |\.|\{|\}|\-|'"
        a = df.author.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().str.replace(pat, '', regex=True)
        e = df.editor.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().replace(pat, '', regex=True)
        y = df['year'].map(safe_int)
        # the standardized tag, standard_tag (stem)
        df['standard_tag'] = np.where(a != '', a + y, np.where(e != '', e + y, 'NOTAG'))

        noans = df.standard_tag[df.standard_tag == 'NOTAG']
        if len(noans):
            logger.warning(f'WARNING: Suggested tags failed for {len(noans)} items')
            logger.warning('YOU NEED TO FIX THIS!')
            logger.info(noans)

        # make the proposed tags, build lists as you go with no duplicates
        df['proposed_tag'] = [self.reference_library.next_tag(a, y)
                                for a, y in zip(np.where(a != '', a, e), y)]
        # df = df.sort_values('proposed_tag')

        # check all unique
        non_uq_tags = df.loc[df.proposed_tag.duplicated(keep=False)]
        if len(non_uq_tags):
            logger.warning(f'Non-unique tags {len(non_uq_tags) = }\n')
            logger.info(set(non_uq_tags.proposed_tags))
            raise ValueError('Non-unique proposed tags')

        # save for audit purposes
        self.save_audit_file(df, '.tag-mapping')

        # actually make the change
        working_df = getattr(self, df_name)
        working_df['tag'] = df['proposed_tag']
        # check unique
        assert working_df.tag.is_unique, 'ERROR: proposed tags are not unique'

    def save_audit_file(self, df, suffix):
        """Save df audit file with a standard filename."""
        fn = self.bibtex_file_path.name + suffix + '.utf-8-sig.csv'
        p = self.audit_dir_path / fn
        df.to_csv(p, encoding='utf-8-sig')
        logger.info(f'Audit DataFrame {len(df) = } saved to {p}.')

    @staticmethod
    def to_windows_csv(df, file_name):
        """Save to CSV in windows-compatible format. Can be read into Excel."""
        df.to_csv(file_name, encoding='utf-8-sig')

    def _parse_library_file_field(self):
        """
        Parse file field created by Mendeley.

        Mendeley's internal file(s) field added to bibtex files. Looks like:
        :C\\:/S/new-papers/Blackwell/1953_Equivalent Comparisons of Experiments.pdf:pdf
        """
        ans = []
        self._file_errs = []
        df = self.ported_df

        for tag, value in df.file.str.split(';').fillna('').items():
            # the items are is name=tag, (0,1,2) and value a list of strings
            # :path\:file:file type strings
            # on split..":" these have four parts:
            # before drive (empty), drive, path, type
            try:
                for ref in value:
                    x = ref.split(':')
                    if len(x) == 4:
                        ans.append([tag, *x[1:]])
                    else:
                        self._file_errs.append([tag, *x[1:]])
            except AttributeError:
                self._file_errs.append([tag, 'Attribute', *ref])
        self._proto_ref_doc_df = pd.DataFrame(ans, columns=['tag', 'drive', 'vfile', 'type']
            ).set_index('tag', drop=True)

    @property
    def ported_df(self):
        if self._ported_df is None:
            logger.info('ported_df creating property ***************************************')
            self.import_bibtex_file()
        return self._ported_df

    @property
    def ref_df(self):
        """The reference df contains no file information and has tag NOT as the index."""
        if self._ref_df is None:
            logger.info('ref_df creating property ***************************************')
            self._ref_df = self.ported_df.drop(columns='file').reset_index(drop=False)
            self._ref_df['arc-source'] = 'mendeley'
        return self._ref_df

    @property
    def doc_df(self):
        """
        Read file information for the current library's pdf store.

        Returns dataframe describing **actual files** (afiles). These may or may not
        be referenced in library.database.
        Currently only PDFs.
        """
        if self._doc_df is None:
            logger.info('doc_df creating property ***************************************')
            pdfs = list(self.pdf_dir.rglob('*.pdf'))
            ans = []
            for p in pdfs:
                stat = p.stat(follow_symlinks=True)
                ans.append({
                    "name": p.name,
                    "path": str(p.as_posix()),
                    "mod": stat.st_mtime_ns,
                    "create": stat.st_ctime_ns,
                    "access": stat.st_atime_ns,
                    "node": stat.st_ino,
                    "links": stat.st_nlink,
                    "size": stat.st_size,
                    "suffix": p.suffix[1:],
                    "hash": 'TBD'
                })
            df = pd.DataFrame(ans)
            tz = 'Europe/London'
            df["create"] = pd.to_datetime(df["create"], unit="ns").dt.tz_localize("UTC").dt.tz_convert(tz)
            df["mod"] = pd.to_datetime(df["mod"], unit="ns").dt.tz_localize("UTC").dt.tz_convert(tz)
            df["access"] = pd.to_datetime(df["access"], unit="ns").dt.tz_localize("UTC").dt.tz_convert(tz)
            self._doc_df = df
            # mend version added hashes from precomputed data frame - fragile!
            # self._add_hashes()
            logger.info(f'Created doc_df with {len(ans)} files')
        return self._doc_df

    @property
    def proto_ref_doc_df(self):
        """Information about files **referenced** in the library database."""
        if self._proto_ref_doc_df is None:
            logger.info('proto_ref_doc_df creating property ***************************************')
            self._parse_library_file_field()
        return self._proto_ref_doc_df

    @property
    def ref_doc_df(self):
        """Make the reference/document dataframe by matching vfiles to afiles."""
        # columns are ref_id=tag and afile name
        if self._ref_doc_df is None:
            logger.info('ref_doc_df creating property ***************************************')
            actual_files = set([i for i in self.doc_df.path])
            logger.info(f'{len(actual_files) = }')
            missing_vfiles = []
            for i, r in self.proto_ref_doc_df.iterrows():
                if r.vfile not in actual_files:
                    missing_vfiles.append([i, r.vfile])
            logger.info(f'Found {len(missing_vfiles) = } missing vfiles (expected 558)')
            logger.info('Levenshtein matching...')
            ans = []
            for tag, m_vfile in missing_vfiles:
                best_match = min(actual_files,
                                 key=lambda alt: Levenshtein.distance(m_vfile, alt))
                ans.append([tag, m_vfile, best_match, Levenshtein.distance(m_vfile, best_match)])
            # for reference
            self._best_match_df = pd.DataFrame(ans, columns=['tag', 'missing_vfile', 'match_afile', 'distance'])
            logger.info('Levenshtein matching completed')
            matcher = {vfile: afile for vfile, afile in self._best_match_df[['missing_vfile', 'match_afile']].values}
            self._ref_doc_df = pd.DataFrame({
                'tag': self.proto_ref_doc_df.index,
                'path': self.proto_ref_doc_df['vfile'].replace(matcher).values
            })
            # for ref.
            self.last_missing_vfiles = missing_vfiles
        return self._ref_doc_df

    @property
    def database(self):
        """Merged database, with exploded authors."""
        if self._database is None:
            exploded_authors = (
                self.ref_df.assign(author=self.ref_df.author.str.split(" and "))
                .explode("author", ignore_index=True)
            )
            self._database = (((
                self.ref_doc_df
                .merge(exploded_authors, on="tag", how='right'))
                .merge(self.doc_df, on='path', how='left'))
            )
            for c in ['node', 'links', 'size']:
                self._database[c] = self._database[c].fillna(0)
            self._database.fillna('')
        return self._database

    def refs_no_docs(self):
        """Return tags to refs with no files."""
        idx = sorted(list(set(self.ref_df.tag) - set(self.ref_doc_df.tag)))
        return self.ref_df.query('tag in @idx')

    def docs_no_refs(self):
        """Return docs with no associated refs."""
        paths = set(self.doc_df.path) - set(self.ref_doc_df.path)
        return self.doc_df.query('path in @paths')

    def stats(self):
        """Statistics about refs (tags), docs (paths)."""
        docs_per_ref = self.ref_doc_df.groupby('tag').count()
        # I know most is 3
        ref_1_doc, ref_2_doc, ref_3_doc = docs_per_ref.value_counts().values
        assert len(docs_per_ref) == ref_1_doc + ref_2_doc + ref_3_doc
        ref_0_doc = len(self.ref_df) - len(docs_per_ref)

        refs_per_doc = self.ref_doc_df.groupby('path').count()
        # I know most is 4
        doc_1_ref, doc_2_ref, doc_3_ref, *doc_4_ref = refs_per_doc.value_counts()
        doc_4_ref = sum(doc_4_ref)
        assert len(refs_per_doc) == doc_1_ref + doc_2_ref + doc_3_ref + doc_4_ref
        doc_0_ref = len(self.doc_df) - len(refs_per_doc)

        stats = pd.DataFrame({
            'objects': [len(self.ref_df), len(self.doc_df)],
            'no children': [ref_0_doc, doc_0_ref],
            'children': [len(docs_per_ref), len(refs_per_doc)],
            '1 child': [ref_1_doc, doc_1_ref],
            '2 children': [ref_2_doc, doc_2_ref],
            '3 children': [ref_3_doc, doc_3_ref],
            '4+ children': [0, doc_4_ref],
        }, index=['references', 'documents']).T

        return stats

    def stats_ref_fields(self):
        """Statistics on distinct values by field."""
        ans = {}
        for c in self.ref_df.columns:
            vc = self.ref_df[c].value_counts()
            if c == 'arc-citations':
                ans[c] = [len(vc), vc.get(0, 0)]
            else:
                ans[c] = [len(vc), vc.get('', 0)]

        stats = pd.DataFrame(ans.values(),
                             columns=[ 'distinct', 'missing'],
                             index=ans.keys())
        return stats

