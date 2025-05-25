"""
Code to port over a Mendeley generated bibtex file.

Code in this module would be used once, and adjusted to your specific library.
"""

from pathlib import Path
import re

import latexcodec
import Levenshtein
import numpy as np
import pandas as pd

from . import BASE_DIR
from . trie import Trie
from . utilities import remove_accents, accent_mapper_dict, safe_int, KeyAllocator


def suggest_tag(df):
    """Suggest tags fore each row of df."""
    a = df.author.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().str.replace(r' |\.|\{|\}|\-', '', regex=True)
    e = df.editor.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().replace(r' |\.|\{|\}|\-', '', regex=True)
    y = df['year'].map(str)  # was safe_int, but that's not needed or sensible
    return np.where(a != '', a + y, np.where(e != '', e + y, 'NOTAG'))


class Bib2df():
    """Bibtex file to dataframe."""

    # for de-texing single characters in braces
    _r_brace1 = re.compile(r'{(.)}')
    _r_brace2 = re.compile(r'{{(.)}}')

    # base columns used by the app for quick output displays
    base_cols = ['tag', 'type', 'author', 'title', 'year', 'journal', 'file']

    # =====================================================================================================
    # user defined mappers: these should be customized for each import
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

    # for mapping the edition bibtex field, used in port_mendeley_file
    edition_mapper = {
        "10": "Tenth",
        "2": "Second",
        "2nd": "Second",
        "2nd Editio": "Second",
        "3": "Third",
        "3rd": "Third",
        "5": "Fifth",
        "Enlarged": "Enlarged",
        "Fifth": "Fifth",
        "First": "First",
        "Fourth": "Fourth",
        "Ninth": "Ninth",
        "Second": "Second",
        "Second Edi": "Second",
        "Seventh": "Seventh",
        "Sixth": "Sixth",
        "Third": "Third",
        "fourth": "Fourth",
    }

    # used by port_mendeley_file to drop fields from input bibtex file
    omitted_menedely_fields = ['abstract', 'annote', 'issn', 'isbn', 'archivePrefix', 'arxivId', 'eprint', 'pmid',
                               'primaryClass', 'series', 'chapter', 'school',
                               'organization', 'howpublished', 'keywords'
                               ]

    # end customizable mappers
    # =====================================================================================================

    def __init__(self, p, pdf_dir, *, fillna=True, audit_mode=False):
        """
        Read Path p into bibtex df, pdf_dir is a Path to pdf files (must exist)

        afile = an actual file
        vfile = a named reference in the bibtex file that may not correspond to an afile

        pdf_dir is where the afile documents live.

        Use fillna=False to use the contents functions (see missing fields).

        Note: this function is "bibtex" file based and creates a dataframe, whereas
        the Library class is dataframe based and creates a bibtex file.
        """
        self.audit_mode = audit_mode  # if true, save_audit_file works
        self.pdf_dir = pdf_dir
        assert self.pdf_dir.exists(), 'PDF directory does not exist'
        self.bibtex_file_path = p
        self.txt = p.read_text(encoding='utf-8').translate(self._char_map)
        self.txt, n = self._re_subs_compiled.subn(lambda m: self._re_subs[m.group()], self.txt)
        if self.audit_mode:
            print(f'uber regex sub found {n = } replacements')
        self.stxt = re.split(r'^@', self.txt, flags=re.MULTILINE)
        l = map(self.parse_line, self.stxt[1:])
        self._df = pd.DataFrame(l)
        # the bibtex row 0 is mendeley junk
        # doing this keeps stxt and the df on the same index
        self._df.index = range(1, 1 + len(self._df))
        if fillna:
            self._df = self._df.fillna('')
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

    @property
    def df(self):
        return self._df

    @staticmethod
    def parse_line(entry):
        result = {}

        # Step 1: Extract type and tag
        # windows GS bibtex pastes come in with \r\n
        entry = entry.replace('\r\n  ', '\n')
        header_match = re.match(r'@?(\w+)\{([^,]+),', entry)
        if not header_match:
            print("Error: Unable to parse entry header.")
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
                print('going slow')
                return Bib2df.parse_line_slow(entry)
        return result

    @staticmethod
    def parse_gs(entry):
        """Parse a Google Scholar Bibtex entry, copied and pasted."""
        result = {}

        # Step 1: Extract type and tag
        # windows GS bibtex pastes come in with \r\n
        entry = entry.replace('\r\n  ', '\n')
        header_match = re.match(r'@?(\w+)\{([^,]+),', entry)
        if not header_match:
            print("Error: Unable to parse entry header.")
            return None
        result['type'], result['tag'] = header_match.groups()

        # Step 2: Remove header and final trailing '}'
        body = entry[header_match.end():].strip()
        if body.endswith('}'):
            body = body[:-1].strip() + ",\n"

        # GS has no spaces around equals
        for m in re.finditer(r'([a-zA-Z\-]+)={(.*?)},\n', body, flags=re.DOTALL):
            try:
                k, v = m.groups()
                result[k] = v
            except ValueError:
                print('going slow')
                return Bib2df.parse_line_slow(entry)
        return result

    @staticmethod
    def parse_line_slow(entry):
        result = {}

        # Step 1: Extract type and tag
        header_match = re.match(r'(\w+)\{([^,]+),', entry)
        if not header_match:
            print("Error: Unable to parse entry header.")
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
        """Contents info on df - distinct values, fields etc."""
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
            a = set(df.spaced)
            t = Trie()
            for name in a:
                t.insert(name)
            # mapping will go from name to longest completion
            mapping = {}
            for name in a:
                m = t.longest_unique_completion(name)
                if m != name:
                    # have found a better version
                    mapping[name] = m
            df['longest'] = df.spaced.replace(mapping)
            accent_mapper = accent_mapper_dict(df.longest)
            df['accents'] = df.longest.replace(accent_mapper)
            # initial  periods
            df['proposed'] = df.accents.str.replace(r'(\b)([A-Z])( |$)', r'\1\2.\3', case=True, regex=True)
            print(f'Field: authors\nDecode errors: {len(self.last_decode) = }')
            self._author_map_df = df
            # debug
            self.trie = t
            self.mapping = mapping
            self.accent_mapper = accent_mapper
        return self._author_map_df

    def distinct(self, c, source='ref_df'):
        """Return distinct occurrences of col c."""
        df = getattr(self, source)
        if df is None:
            return df
        if c == 'author':
            return sorted(
                set(author.strip() for s in df.author.dropna() for author in s.split(" and "))
            )
        else:
            return sorted(set([i for i in df[c] if i != '']))

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
            # (f'tex_to_unicode DECODE ERR | {s:<25s} | {e}')
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
        mapper = {k: v for k, v in self.author_map_df[['original', 'proposed']].values}
        # manual fixes
        manual_updates = {
            'Acemoglu, By Daron': 'Acemoglu, Daron',
            # 'Candes, E': 'Candès, Emmanuel J.',
            # 'Candes, E.': 'Candès, Emmanuel J.',
            # 'Candes, E.J.': 'Candès, Emmanuel J.',
            # 'Candes, Emmanuel': 'Candès, Emmanuel J.',
            # 'Candes, Emmanuel J.': 'Candès, Emmanuel J.',
            # 'Cand{\\`{e}}s, Emamnuel J': 'Candès, Emmanuel J.',
            # 'Cand{\\`{e}}s, Emmanuel J': 'Candès, Emmanuel J.',
            # 'Cand{\\`{e}}s, Emmanuel J.': 'Candès, Emmanuel J.'
        }
        mapper.update(manual_updates)
        return mapper

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

    def port_mendeley_file(self):
        """
        Normalize each text-based field.

        Runs through each task in turn, see comments.
        """
        print('Running port_mendeley_file to create ported_df')
        kept_fields = [i for i in self.df.columns if i not in self.omitted_menedely_fields]
        self._ported_df = self.df[kept_fields].copy()

        # ============================================================================================
        # author: initials, extend, accents
        self.map_authors('_ported_df')

        # ============================================================================================
        # de-tex other text fields
        self.all_unicode_errors = {}
        for f in ['title', 'journal', 'publisher', 'institution', 'booktitle', 'address',
                  'editor', 'mendeley-tags']:
            self.last_decode = []
            self._ported_df[f] = self._ported_df[f].map(self.tex_to_unicode)
            if len(self.last_decode):
                print(f'\tField: {f}\t{len(self.last_decode) = }')
                self.all_unicode_errors[f] = self.last_decode.copy()
            print(f'Fixed {f}')
        # audit unicode errors
        ans = []
        for k, v in self.all_unicode_errors.items():
            for mc in v:
                ans.append([k, mc])
        temp = pd.DataFrame(ans, columns=['field', 'miscode'])
        self.save_audit_file(temp, '.tex-unicode-errors')
        # ============================================================================================
        # keywords
        # paper's key words - never used these, they are included in omitted_menedely_fields
        # add code here for alternative treatment

        # ============================================================================================
        # mendeley-tags: these are things like my WangR or Delbaen or PMM
        # nothing to do here --- just carry over

        # ============================================================================================
        # citations: figure number of citations from my notes in the abstract
        # dict index -> number of citations, default = 0
        citation_mapper = self.extract_citations()
        self._ported_df['arc-citations'] = [citation_mapper.get(i, 0) for i in self._ported_df.index]

        # ============================================================================================
        # edition: normalize edition field
        # discover using
        # for v in sorted(b.distinct('edition')):
        #     print(f'"{v}": "{v.title()}",')
        # and set edition_mapper accordingly
        self._ported_df.edition = self._ported_df.edition.replace(self.edition_mapper)

        # ============================================================================================
        # tags: normalize and resolve duplicate TAGS
        # duplicated entries will be handled separately
        self.map_tags()

        # ============================================================================================
        # files: files are entirely separately managed, field just pulled over
        # see code in file_field_df

        # set tag as the index
        self._ported_df = self._ported_df.set_index('tag')

        # Ad hoc changes!!
        print('Ad hoc changes')
        self._ported_df.loc['Robert2022', 'file'] = self._ported_df.loc['Robert2022', 'file'].replace(
            '/Users/steve/AppData/Local/Mendeley Ltd./Mendeley Desktop/Downloaded/',
            '/S/Library/Robert, Denuit/')
        # print()
        # print(self._ported_df.loc['Kuelbs2011', ['author', 'title', 'year']])
        self._ported_df.rename(index={'Kuelbs2011': 'Stroock2011'}, inplace=True)
        self._ported_df.loc['Stroock2011', 'author'] = 'Stroock, Daniel W.'
        # print()
        # print(self._ported_df.loc['Stroock2011', ['author', 'title', 'year']])
        # print()

        # ============================================================================================
        # final checks and balances, and write out info
        self.save_audit_file(self.df, '.raw-df')
        self.save_audit_file(self._ported_df, '.ported-df')
        import_info = pd.DataFrame({
            'created': str(pd.Timestamp.now()),
            'bibtex_file': self.bibtex_file_path.resolve(),
            'raw_entries': len(self.df),
            'ported_entries': len(self._ported_df)
        }.items(), columns=['key', 'value'])
        self.save_audit_file(import_info, '.audit-info')
        if self.audit_mode:
            # for posterity and auditability
            p_ = (BASE_DIR / 'imports' / self.bibtex_file_path.name)
            if p_.exists():
                p_.unlink()
            p_.hardlink_to(self.bibtex_file_path.name)
        return import_info

    def extract_citations(self):
        """Extract citations from abstract field."""
        # regex to extract group like 1000, 2K, 1,000, 1K-2K etc.
        # checked against just [Cc]itation and finds all material answers
        pat = r'(?:(?P<number>[\d]+)\+?|(?P<numberK>[\dKk]+)\+?||(?P<dashed>\d[\dKk\- ]+)\+?) +(?:Google|GS)? ?[Cc]itations?'
        # all matches in dataframe cols number, numberK, dashed
        m = self.df.abstract.str.extract(pat, expand=True).dropna(how='all')
        # number -> convert to int
        m.number = m.number.fillna(0).astype(int)
        # number 000 -> int
        m.numberK = m.numberK.str.replace('K', '000').fillna(0).astype(int)
        # number - number, first convert K
        m.dashed = m.dashed.str.replace('K', '000')
        # split, convert, mean, convert
        m['dashed'] = m.dashed.str.split('-', expand=True).astype(float).mean(axis=1).fillna(0).astype(int)
        # three mutually exclusive options default zero, so sum to get citations
        m['citations'] = m.sum(axis=1)
        # return series to use as a mapper
        return m.citations

    def show_unicode_errors(self):
        """Accumulated unicode errors."""
        if self.all_unicode_errors is None:
            return None
        ans = set()
        for k, v in self.all_unicode_errors.items():
            ans = ans.union(set([c for l in v for c in l if len(c.encode('utf-8')) > 1]))
        return ans

    def no_file(self):
        """Entries with no files listed."""
        return self.df.loc[self.df.file == '', self.base_cols]

    def map_tags(self, df_name='ported_df'):
        """
        Remap the tags into standard AuthorYYYY[a-z] format for named df.

        Saves a dataframe showing what was done as part of import.
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
            print(f'WARNING: Suggested tags failed for {len(noans)} items')
            print(noans)

        # make the proposed tags, build lists as you go with no duplicates
        ka = KeyAllocator([])
        df['proposed_tag'] = df.standard_tag.map(ka)
        df = df.sort_values('proposed_tag')

        # check all unique
        assert len(df.loc[df.proposed_tag.duplicated(keep=False)]) == 0, 'ERROR: map tags produced non-unqiue tags'

        # save for audit purposes
        self.save_audit_file(df, '.tag-mapping')

        # actually make the change
        working_df = getattr(self, df_name)
        working_df['tag'] = df['proposed_tag']
        # check unique
        assert working_df.tag.is_unique, 'ERROR: proposed tags are not unique'

    def save_audit_file(self, df, suffix):
        """Save df audit file with a standard filename."""
        if self.audit_mode:
            fn = self.bibtex_file_path.name + suffix + '.utf-8-sig.csv'
            p = BASE_DIR / 'imports' / fn
            # TODO ENCODING??
            df.to_csv(p, encoding='utf-8-sig')
            print(f'Audit DataFrame {len(df) = } saved to {p}.')
        else:
            print(f'Audit mode OFF, DataFrame {len(df) = } NOT saved.')

    def querex(self, field, regex):
        """Apply regex filter to field."""
        return self.df.loc[self.df[field].str.contains(regex, case=False, regex=True),
                           self.base_cols]

    @staticmethod
    def to_windows_csv(df, file_name):
        """Save to CSV in windows-compatible format. Can be read into Excel."""
        df.to_csv(file_name, encoding='utf-8-sig')

    def _parse_library_file_field(self):
        """Parse file field."""
        ans = []
        self._file_errs = []
        df = self.ported_df

        for tag, value in df.file.str.split(';').fillna('').items():
            # the items are is name=tag, (0,1,2) and value a list of strings :path\:file:file type strings
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
        self._proto_ref_doc_df = pd.DataFrame(ans, columns=['tag', 'drive', 'vfile', 'type']).set_index('tag', drop=True)

    def actual_files(self, library_path):
        """Find actual files in the library path."""

    @property
    def ported_df(self):
        if self._ported_df is None:
            self.port_mendeley_file()
        return self._ported_df

    @property
    def ref_df(self):
        """The reference df contains no file information."""
        if self._ref_df is None:
            self._ref_df = self.ported_df.drop(columns='file')
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
            print(f'Created doc_df with {len(ans)} files')
        return self._doc_df

    @property
    def proto_ref_doc_df(self):
        """Information about files **referenced** in the library database."""
        if self._proto_ref_doc_df is None:
            self._parse_library_file_field()
        return self._proto_ref_doc_df

    @property
    def ref_doc_df(self):
        """Make the reference/document dataframe by matching vfiles to afiles."""
        # columns are ref_id=tag and afile name
        if self._ref_doc_df is None:
            actual_files = set([i for i in self.doc_df.path])
            print(f'{len(actual_files) = }')
            missing_vfiles = []
            for i, r in self.proto_ref_doc_df.iterrows():
                if r.vfile not in actual_files:
                    missing_vfiles.append([i, r.vfile])
            print(f'Found {len(missing_vfiles) = } missing vfiles (expected 558)')
            print('Levenshtein matching...')
            ans = []
            for tag, m_vfile in missing_vfiles:
                best_match = min(actual_files,
                                 key=lambda alt: Levenshtein.distance(m_vfile, alt))
                ans.append([tag, m_vfile, best_match, Levenshtein.distance(m_vfile, best_match)])
            # for reference
            self._best_match_df = pd.DataFrame(ans, columns=['tag', 'missing_vfile', 'match_afile', 'distance'])
            print('Levenshtein matching completed')
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
        """Merged database."""
        if self._database is None:
            self._database = (((
             self.ref_doc_df
                .merge(self.ref_df, on="tag",  how='right'))
                .merge(self.doc_df, on='path', how='left'))
            )
            for c in ['node', 'links', 'size']:
                self._database[c] = self._database[c].fillna(0)
            self._database.fillna('')
        return self._database

    def refs_no_docs(self):
        """Return tags to refs with no files."""
        return self.ref_df.loc[sorted(list(set(self.ref_df.index) - set(self.ref_doc_df.tag)))]

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
        doc_1_ref, doc_2_ref, doc_3_ref, doc_4_ref = refs_per_doc.value_counts()
        assert len(refs_per_doc) == doc_1_ref + doc_2_ref + doc_3_ref + doc_4_ref
        doc_0_ref = len(self.doc_df) - len(refs_per_doc)

        stats = pd.DataFrame({
            'objects': [len(self.ref_df), len(self.doc_df)],
            'no children': [ref_0_doc, doc_0_ref],
            'children': [len(docs_per_ref), len(refs_per_doc)],
            '1 child': [ref_1_doc, doc_1_ref],
            '2 children': [ref_2_doc, doc_2_ref],
            '3 children': [ref_3_doc, doc_3_ref],
            '4 children': [0, doc_4_ref],
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
        # c: len(self.distinct(c)) for c in self.ref_df.columns
        # }, index=['Value']).T
        return stats

    def _add_hashes(self):
        """Lookup hashes from file-db object."""
        fp = Path('\\s\\appdata\\file-database\\kolmogorov\\library.fdb-feather')
        df = pd.read_feather(fp)
        ans = {}
        for p, h in df[['path', 'hash']].values:
            ans[str(Path(p).relative_to('c:').as_posix())] = h
        self.doc_df['hash'] = self.doc_df['path'].replace(ans)
