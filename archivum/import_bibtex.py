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
import json
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

from . import BASE_DIR, APP_NAME, EMPTY_LIBRARY
from .utilities import (remove_accents, make_qd,
                        accent_mapper_dict, safe_int,
                        TagAllocator)
from . trie import Trie

logger = logging.getLogger(__name__)


# GEMINI CODE
@dataclass
class ImportResult:
    """
    Summary of a single BibTeX import run.
    """

    run_dir: Path
    added_refs: int
    added_docs: int


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
    _edition_mapper = {
        "First": "First",
        "2": "Second",
        "2nd": "Second",
        "Second": "Second",
        "Second Edi": "Second",
        "3": "Third",
        "3rd": "Third",
        "Third": "Third",
        "4": "Fourth",
        "4th": "Fourth",
        "Fourth": "Fourth",
        "fourth": "Fourth",
        "5": "Fifth",
        "5th": "Fifth",
        "Fifth": "Fifth",
        "Sixth": "Sixth",
        "Seventh": "Seventh",
        "Ninth": "Ninth",
        "10": "Tenth",
        "2nd Editio": "Second",
        "Enlarged": "Enlarged",
    }

    # used by import_bibtex_file to drop fields from input bibtex file
    _omitted_bibtex_fields = ['abstract', 'annote', 'issn', 'isbn', 'archivePrefix',
                               # 'arxivId',
                               'eprint', 'pmid',
                               'primaryClass', 'series', 'chapter', 'school',
                               'organization', 'howpublished', 'keywords'
                               ]

    # end customizable mappers
    # =====================================================================================================

    def __init__(self, *, bibtex_file_path, pdf_dir, reference_library,
                fillna=True, errors_mapper=None, remap_dashes=False, qd=None):
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

        errors_mapper is to allow this class to do the inital porting.
        Pass something like:

            # special unicode errors used by tex_to_unicode
            errors_mapper = {'Caicedo, Andr´es Eduardo': 'Caicedo, Andrés Eduardo',
                             'Cerreia‐Vioglio, Simone': 'Cerreia‐Vioglio, Simone',
                             'Cerreia–Vioglio, S.': 'Cerreia–Vioglio, S.',
                             'Cireşan, Dan': 'Cireșan, Dan',
                             'J.B., SEOANE-SEP´ULVEDA': 'J.B., Seoane-Sepúlveda',
                             'JIM´ENEZ-RODR´IGUEZ, P.': 'Jiménez-Rodríguez, P.',
                             'Joldeş, Mioara': 'Joldeș, Mioara',
                             'Lesne, Jean‐Philippe ‐P': 'Lesne, Jean‐Philippe ‐P',
                             'MU˜NOZ-FERN´ANDEZ, G.A.': 'Muñoz-Fernández, G.A.',
                             'Naneş, Ana Maria': 'Naneș, Ana Maria',
                             'Paradıs, J': 'Paradís, J',
                             "P{\\'{a}}stor, Ľ": 'Pástor, Ľ',
                             'Uludağ, Muhammed': 'Uludağ, Muhammed',
                             'Ulug{\\"{u}}lyaǧci, Abdurrahman': 'Ulugülyaǧci, Abdurrahman',
                             'Zitikis, Riċardas': 'Zitikis, Riċardas',
                             'de la Pen̄a, Victor H.': 'de la Peña, Victor H.',
                             "{L{\\'{o}}pez\xa0de\xa0Vergara}, Jorge E.": 'López\xa0de\xa0Vergara, Jorge E.'}


        Audit mode is just ALWAYS ON - you can delete the files if you like!
        """
        self.bibtex_file_path = bibtex_file_path
        assert self.bibtex_file_path.exists(), 'Bibtex file must exist'
        self.pdf_dir = pdf_dir
        assert self.pdf_dir.exists(), 'PDF directory does not exist'
        self.reference_library = reference_library or EMPTY_LIBRARY
        self.fillna = fillna
        self.errors_mapper = errors_mapper or {}
        self.remap_dashes = remap_dashes
        # if you write audits, also save  - this is a flag
        self._errors_mapper_saved = False
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
        self.qd = qd or print

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
            if self.remap_dashes:
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
            df = pd.DataFrame({'original': self.distinct('author', self.df)})
            self.last_decode = []
            df['unicoded'] = df.original.map(self.tex_to_unicode).str.replace('.', '')
            # space out initials Mild, SJM -> Mild, S J M; works for two of three consecutive initials
            df['spaced'] = df.unicoded.str.replace(r'(?<=, )([A-Z]{2,3})\b',
                                                   lambda m: ' '.join(m.group(1)),
                                                   regex=True)

            # diverge from Bib2df: use the reference library
            t = Trie()
            # distinct returns a set
            if self.reference_library != EMPTY_LIBRARY:
                ref_authors = self.distinct("author", self.reference_library.ref_df)
            else:
                # no reference authors
                ref_authors = []
            logger.info(f'Building author Trie from reference library, {len(ref_authors)} distinct authors')
            for name in ref_authors:
                t.insert(name)
            # mapping will go from name to longest completion
            mapping = {}
            # authors in self.df
            a = self.distinct("author", self.df)
            logger.info(f'Import contains {len(a)} distinct authors -> remapping')
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

    @staticmethod
    def distinct(column_name, df):
        """Return distinct occurrences of col c in df."""
        # signature changed from mendeley version
        if df is None:
            return None
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
            s = self.errors_mapper.get(s_in, s_in)
            if s_in not in self.errors_mapper:
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
        kept_fields = [i for i in self.df.columns if i not in self._omitted_bibtex_fields]
        self._ported_df = self.df[kept_fields].copy()

        # ============================================================================================
        # author: initials, extend, accents
        self.map_authors('_ported_df')

        # ensure other edited fields are present
        # this may not be the case for small imports
        for f in self._base_fields:
            if f not in self._ported_df:
                logger.debug('Imported df missing %s - adding', f)
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
            logger.debug(f'Fixed {f}')

        # audit unicode errors
        ans = []
        for k, v in self.all_unicode_errors.items():
            for mc in v:
                ans.append([k, mc])
        temp = pd.DataFrame(ans, columns=['field', 'miscode'])
        self.save_audit_file(temp, '.tex-unicode-errors')

        # ============================================================================================
        # keywords
        # paper's key words - never used these, they are included in _omitted_bibtex_fields
        # add code here for alternative treatment

        # ============================================================================================
        # mendeley-tags: these are things like my WangR or Delbaen or PMM
        # nothing to do here --- just carry over

        # ============================================================================================
        # citations: figure number of citations from my notes in the abstract - DROPPED
        # dict index -> number of citations, default = 0

        # ============================================================================================
        # edition: normalize edition field
        self._ported_df.edition = self._ported_df.edition.replace(self._edition_mapper)

        # ============================================================================================
        # tags: normalize and resolve duplicate TAGS
        self.map_tags()

        # ============================================================================================
        # files: files are entirely separately managed, field just pulled over
        # see code in file_field_df

        # set tag as the index
        # self._ported_df = self._ported_df.set_index('tag')

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
        if self.reference_library != EMPTY_LIBRARY:
            # reset ref lib tag gen
            self.reference_library.reset_tag_allocator()
            df['proposed_tag'] = [self.reference_library.next_tag(a, y)
                                for a, y in zip(np.where(a != '', a, e), y)]
        else:
            # make the proposed tags, build lists as you go with no duplicates
            ta = TagAllocator([])
            df['proposed_tag'] = df.standard_tag.map(ta)
            df = df.sort_values('proposed_tag')

        # df = df.sort_values('proposed_tag')

        # check all unique
        non_uq_tags = df.loc[df.proposed_tag.duplicated(keep=False)]
        if len(non_uq_tags):
            logger.warning(f'Non-unique tags {len(non_uq_tags) = }')
            print(non_uq_tags)
            logger.info(set(non_uq_tags.proposed_tag))
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
        fn = self.bibtex_file_path.name + suffix + '.csv'
        p = self.audit_dir_path / fn
        df.to_csv(p, encoding='utf-8')
        logger.info(f'Audit DataFrame {len(df) = } saved to {p.name}.')
        # check about errors mapper
        if self.errors_mapper and not self._errors_mapper_saved:
            fn = self.audit_dir_path / "errors_mapper.json"
            with open(fn, 'w', encoding='utf-8') as f:
                json.dump(self.errors_mapper, f, indent=4)
            self._errors_mapper_saved = True

    def show_audit_files(self, top=5, trim=100, bib=False):
        """qd all the audit files."""
        if bib:
            for f in self.audit_dir_path.glob("*.bib"):
                print(f.name)
                print("=" * len(f.name))
                print(f'Lines trimmed to {trim} characters.')
                txt = f.read_text()
                txt = '\n'.join([i[:trim] for i in txt.split('\n')])
                print(txt)
                print()

        for f in self.audit_dir_path.glob("*.json"):
            print(f.name)
            print("=" * len(f.name))
            print(f.read_text())
            print()

        if self.qd is None:
            logger.error('Must provide qd to use show_ functions')
            return
        for f in self.audit_dir_path.glob("*.csv"):
            df = pd.read_csv(f, encoding='utf-8-sig')
            self.qd(df.head(top), caption=f.stem, tikz=False)

    def show_generated_dfs(self):
        """Use self.qd to display the main generated dfs."""
        if self.qd is None:
            logger.error('Must provide qd to use show_ functions')
            return
        for nm in ('df', 'ported_df', 'ref_df', 'doc_df', 'ref_doc_df'):
            d = getattr(self, nm, None)
            if d is not None:
                self.qd(d, caption=nm)

    @staticmethod
    def to_windows_csv(df, file_name):
        """Save to CSV in windows-compatible format. Can be read into Excel."""
        df.to_csv(file_name, encoding='utf-8-sig')

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
            self._ref_df = self.ported_df.drop(columns='file')#.reset_index(drop=False)
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
            logger.info(f'Scanned pdf folder and created doc_df with {len(ans)} files')
        return self._doc_df

    @property
    def proto_ref_doc_df(self):
        """
        Information about files **referenced** in the library database.

        Parse file field created by Mendeley.

        Mendeley's internal file(s) field added to bibtex files. Looks like:
        :C\\:/S/new-papers/Blackwell/1953_Equivalent Comparisons of Experiments.pdf:pdf
        """
        if self._proto_ref_doc_df is None:
            logger.info('proto_ref_doc_df creating property ***************************************')
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
            logger.info(f'Created proto_ref_doc_df with {len(self._proto_ref_doc_df)} rows.')
        return self._proto_ref_doc_df

    @property
    def ref_doc_df(self):
        """Make the reference/document dataframe by matching vfiles to afiles."""
        # columns are ref_id=tag and afile name
        if self._ref_doc_df is None:
            logger.info('ref_doc_df creating property ***************************************')
            actual_files = set([i for i in self.doc_df.path])
            logger.info(f'\tFound {len(actual_files)} actual files')
            missing_vfiles = []
            for i, r in self.proto_ref_doc_df.iterrows():
                if r.vfile not in actual_files:
                    missing_vfiles.append([i, r.vfile])
            logger.info(f'\tFound {len(missing_vfiles) = } missing vfiles (Mend main extract expects 558)')
            logger.info('\tLevenshtein matching in ref_doc...')
            ans = []
            for tag, m_vfile in missing_vfiles:
                best_match = min(actual_files,
                                 key=lambda alt: Levenshtein.distance(m_vfile, alt))
                ans.append([tag, m_vfile, best_match, Levenshtein.distance(m_vfile, best_match)])
            # for reference
            self._best_match_df = pd.DataFrame(ans, columns=['tag', 'missing_vfile', 'match_afile', 'distance'])
            logger.info('\t...Levenshtein matching completed')
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
        data = {}

        # Configuration for the two rows
        configs = [
            ('references', len(self.ref_df), 'tag'),
            ('documents', len(self.doc_df), 'path')
        ]

        for name, total, group_col in configs:
            # Calculate counts per group
            counts = self.ref_doc_df.groupby(group_col).size()

            # Frequency distribution of those counts
            dist = counts.value_counts()

            # specific row data
            row = {
                'objects': total,
                'no children': total - len(counts),
                'children': len(counts)
            }

            # Dynamically add columns for each count found (1, 2, 3, 4, 5...)
            for num_children, freq in dist.items():
                label = f"{num_children} child{'ren' if num_children != 1 else ''}"
                row[label] = freq

            data[name] = row

        # Create DataFrame
        df = pd.DataFrame.from_dict(data, orient='index').fillna(0).astype(int)

        # Sort columns: fixed headers first, then numerical distribution
        fixed_cols = ['objects', 'no children', 'children']
        dist_cols = sorted(
            [c for c in df.columns if c not in fixed_cols],
            key=lambda x: int(str(x).split()[0])
        )

        return df[fixed_cols + dist_cols].T

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

    # GEMINI CODE for interactive update library
    @staticmethod
    def _normalize_title(s: str) -> str:
        """Simple normalization for title comparison."""
        if not isinstance(s, str):
            return ""
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return " ".join(s.split())

    def _possible_duplicate(self, ref_row) -> str | None:
        """
        Heuristic duplicate check: by DOI (if present) and by normalized title.
        Returns a short message if something looks like a duplicate.
        """
        doi = str(ref_row.get("doi", "") or "").strip().lower()
        title_norm = self._normalize_title(ref_row.get("title", ""))
        existing_title_norm = self.reference_library.ref_df.title.map(self._normalize_title)

        # DOI check
        if doi and "doi" in self.reference_library.ref_df.columns:
            mask = self.reference_library.ref_df["doi"].astype(str).str.lower().str.strip() == doi
            if mask.any():
                tags = self.reference_library.ref_df.loc[mask, "tag"].tolist()
                return f"Possible duplicate by DOI; existing tag(s): {tags}"

        # Title check
        if title_norm:
            mask = existing_title_norm == title_norm
            if mask.any():
                tags = self.reference_library.ref_df.loc[mask, "tag"].tolist()
                return f"Possible duplicate by title; existing tag(s): {tags}"

        return None

    def _write_skipped_bibtex(self,
        skipped_rows: list[pd.Series]
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
                if key in {"type", "tag"}:
                    continue
                if pd.isna(val):
                    continue
                v = str(val).strip()
                if not v:
                    continue
                lines.append(f"  {key} = {{{v}}},")
            lines.append("}\n")
        fn = self.bibtex_file_path.name + '.skipped.bib'
        path = self.audit_dir_path / fn
        path.write_text("\n".join(lines), encoding="utf-8")

    def interactive_import(self) -> ImportResult:
        """
        Interactive import of references/documents from a BibTeX file into
        self.reference_library (may be None for a new library).

        For each candidate reference:
          * display the "raw" view as input,
          * display the normalized view (ref_df row),
          * indicate which rows have changed,
          * flag possible duplicates,
          * prompt user to [a]ccept / [s]kip / [q]uit.

        Accepted items are appended to the library. Skipped items are written
        to a BibTeX file in the run directory for later manual editing.
        """
        accepted_tags: list[str] = []
        skipped_raw_rows: list[str] = []
        qdp = make_qd(max_string_length=60, display=print, max_rows=10, tikz=False)
        count = 0
        total = len(self.ported_df)
        import msvcrt
        for (l, raw_input), (r, revised) in zip(self.df.iterrows(), self.ported_df.reset_index(drop=False).iterrows()):
            c = pd.concat((raw_input, revised), axis=1, keys=['Original', 'Revised'])
            c = c.loc[~c.Revised.isna()]
            c['Comparison'] = c.Original==c.Revised
            c['Comparison'] = ['-' if i else 'CHGD' for i in c['Comparison']]
            count += 1
            tag = revised.tag
            print(f'{count} / {total} ({count/total:.0%}): {tag}')
            qdp(c, show_index=True)
            dup_msg = self._possible_duplicate(revised)
            if dup_msg:
                print()
                print(f"WARNING: {dup_msg}")
            else:
                print('No possible duplicates found.')
            while True:
                # ans = input("[a]ccept / [s]kip / [q]uit? [a/s/q]: ").strip().lower()
                print("[a]ccept / [s]kip / [q]uit? [a/s/q]: ")
                ans = msvcrt.getwch()
                ans = ans.lower()
                if ans in {"", "a", "s", "q"}:
                    break
                print("Please enter 'a', 's', or 'q'.")
            if ans in {"", "a"}:
                accepted_tags.append(tag)
            elif ans == "s":
                skipped_raw_rows.append(raw_input)
            elif ans == "q":
                # break out of the outer loop
                break

        # If nothing accepted, just write skipped bibtex (if any) and return.
        pip_path = self.bibtex_file_path.with_suffix(".pip.bib")
        if not accepted_tags:
            if skipped_raw_rows:
                self._write_skipped_bibtex(skipped_raw_rows, pip_path)
                logger.info("No references accepted; wrote %s skipped entries to %s",
                    len(skipped_raw_rows), pip_path)
            return ImportResult(run_dir=run_dir, added_refs=0, added_docs=0)

        accepted_tags_set = set(accepted_tags)
        assert len(accepted_tags_set) == len(accepted_tags), 'Set accepted != accepted?'

        # Build ref/doc/ref_doc additions based on accepted tags.
        # ref and ported differ only in dropping the file column
        ref_add = self.ref_df.loc[accepted_tags].copy()

        # Restrict ref_doc and doc_df to the accepted refs.
        rd_candidates = self.ref_doc_df[self.ref_doc_df["tag"].isin(accepted_tags_set)].copy()
        new_paths = set(rd_candidates["path"])
        doc_add_candidates = self.doc_df[self.doc_df["path"].isin(new_paths)].copy()

        # Drop any docs we already know about.
        existing_paths = set(self.reference_library.doc_df["path"])
        doc_add = doc_add_candidates[~doc_add_candidates["path"].isin(existing_paths)].copy()

        # And restrict ref_doc to only the retained docs.
        kept_paths = set(doc_add["path"])
        rd_add = rd_candidates[rd_candidates["path"].isin(kept_paths)].copy()

        # Append to existing dataframes.
        ref_out = pd.concat([self.reference_library.ref_df, ref_add], ignore_index=True)
        doc_out = pd.concat([self.reference_library.doc_df, doc_add], ignore_index=True)
        ref_doc_out = pd.concat([self.reference_library.ref_doc_df, rd_add], ignore_index=True)

        # ref_path = self.reference_library.config_path.with_suffix(f".{APP_NAME}-ref-feather")
        # doc_path = self.reference_library.config_path.with_suffix(f".{APP_NAME}-doc-feather")
        # ref_doc_path = self.reference_library.config_path.with_suffix(f".{APP_NAME}-ref-doc-feather")

        # persist TODO
        # ref_out.to_feather(ref_path)
        # doc_out.to_feather(doc_path)
        # ref_doc_out.to_feather(ref_doc_path)

        # Update in-memory self.reference_library.
        logger.info(f'Adding {len(ref_out)} rows to library ref_df')
        logger.info(f'Adding {len(doc_out)} rows to library doc_df')
        logger.info(f'Adding {len(ref_doc_out)} rows to library ref_doc_df')
        self.reference_library._ref_df = ref_out
        self.reference_library._doc_df = doc_out
        self.reference_library._ref_doc_df = ref_doc_out

        #?? presumably
        # self.reference_library.save()

        # Write skipped entries (if any) to a "pip" BibTeX file.
        if skipped_raw_rows:
            self._write_skipped_bibtex(skipped_raw_rows, pip_path)
            logger.info("Wrote %d skipped entries to %s", len(skipped_raw_rows), pip_path)

        return ImportResult(
            run_dir=run_dir,
            added_refs=len(ref_add),
            added_docs=len(doc_add),
        )
