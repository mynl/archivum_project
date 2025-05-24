"""
Code to port over a Mendeley generated bibtex file.

Code in this module would be used once, and adjusted to your specific library.
"""

import re

import latexcodec
import numpy as np
import pandas as pd

from . import BASE_DIR
from . trie import Trie
from . utilities import remove_accents, accent_mapper_dict, safeyear, KeyAllocator


def suggest_tag(df):
    """Suggest tags fore each row of df."""
    a = df.author.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().str.replace(r' |\.|\{|\}|\-', '', regex=True)
    e = df.editor.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().replace(r' |\.|\{|\}|\-', '', regex=True)
    y = df['year'].map(str)  # was safeyear, but that's not needed or sensible
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

    def __init__(self, p, fillna=True):
        """
        Read Path p into bibtex df.

        Use fillna=False to use the contents functions (see missing fields).

        Note: this function is "bibtex" file based and creates a dataframe, whereas
        the Library class is dataframe based and creates a bibtex file.
        """
        self.bibtex_file_path = p
        self.txt = p.read_text(encoding='utf-8').translate(self._char_map)
        self.txt, n = self._re_subs_compiled.subn(lambda m: self._re_subs[m.group()], self.txt)
        print(f'uber regex sub found {n = } replacements')
        self.stxt = re.split(r'^@', self.txt, flags=re.MULTILINE)
        l = map(self.parse_line, self.stxt[1:])
        self._df = pd.DataFrame(l)
        # the bibtex row 0 is mendeley junk
        # doing this keeps stxt and the df on the same index
        self._df.index = range(1, 1 + len(self._df))
        if fillna:
            self._df = self._df.fillna('')
        self._file_field_df = None
        self._file_errs = None
        self.ported_df = None
        self._author_map_df = None
        self.all_unicode_errors = None

    @property
    def df(self):
        return self._df

    @property
    def file_field_df(self):
        if self._file_field_df is None:
            self.parse_file_field()
        return self._file_field_df

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

    def parse_file_field(self):
        """Split out file field."""
        ans = []
        self._file_errs = []
        for i, f in self.df[['file']].iterrows():
            try:
                for bit in f.file.split(';'):
                    x = bit.split(':')
                    # print(i, len(x))
                    if len(x) == 4:
                        ans.append([i, *x[1:]])
                    # elif len(x) > 3:
                    #     ans.append([i, x[1], x[2], x[3:]])
                    else:
                        self._file_errs.append([i, x[1:]])
            except AttributeError:
                self._file_errs.append([i, 'Attribute', f.file])
        self._file_field_df = pd.DataFrame(ans, columns=['idx', 'drive', 'file', 'type']).set_index('idx', drop=False)
        self._file_field_df.index.name = 'i'

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
            a = set(df.unicoded)
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
            df['longest'] = df.unicoded.replace(mapping)
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

    def distinct(self, c):
        """Return distinct occurrences of col c."""
        if c == 'author':
            return sorted(
                set(author.strip() for s in self.df.author.dropna() for author in s.split(" and "))
            )
        else:
            return sorted(set([i for i in self.df[c] if i != '']))

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

    def map_authors(self, df_name='ported_df'):
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
        kept_fields = [i for i in self.df.columns if i not in self.omitted_menedely_fields]

        self.ported_df = self.df[kept_fields].copy()

        # ============================================================================================
        # author: initials, extend, accents
        self.map_authors('ported_df')

        # ============================================================================================
        # de-tex other text fields
        self.all_unicode_errors = {}
        for f in ['title', 'journal', 'publisher', 'institution', 'booktitle', 'address',
                  'editor', 'mendeley-tags']:
            self.last_decode = []
            self.ported_df[f] = self.ported_df[f].map(self.tex_to_unicode)
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
        self.ported_df['arc-citations'] = [citation_mapper.get(i, 0) for i in self.ported_df.index]

        # ============================================================================================
        # edition: normalize edition field
        # discover using
        # for v in sorted(b.distinct('edition')):
        #     print(f'"{v}": "{v.title()}",')
        # and set edition_mapper accordingly
        self.ported_df.edition = self.ported_df.edition.replace(self.edition_mapper)

        # ============================================================================================
        # tags: normalize and resolve duplicate TAGS
        # duplicated entries will be handled separately
        self.map_tags()

        # ============================================================================================
        # files: files are entirely separately managed, field just pulled over
        # see code in file_field_df

        # ============================================================================================
        # final checks and balances, and write out info
        self.save_audit_file(self.df, '.raw-df')
        self.save_audit_file(self.ported_df, '.ported-df')
        import_info = pd.DataFrame({
            'created': str(pd.Timestamp.now()),
            'bibtex_file': self.bibtex_file_path.resolve(),
            'raw_entries': len(self.df),
            'ported_entries': len(self.ported_df)
        }.items(), columns=['key', 'value'])
        self.save_audit_file(import_info, '.audit-info')
        # for posterity and auditability
        p_ = (BASE_DIR / 'imports' / self.bibtex_file_path.name)
        if p_.exists():
            p_.unlink()
        p_.hardlink_to(self.bibtex_file_path.name)

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
        y = df['year'].map(safeyear)
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

    def save_audit_file(self, df, suffix):
        """Save df audit file with a standard filename."""
        fn = self.bibtex_file_path.name + suffix  + '.utf-8-sig.csv'
        p = BASE_DIR / 'imports' / fn
        # TODO ENCODING??
        df.to_csv(p, encoding='utf-8-sig')
        print(f'Audit DataFrame {len(df) = } saved to {p}')

    def querex(self, field, regex):
        """Apply regex filter to field."""
        return self.df.loc[self.df[field].str.contains(regex, case=False, regex=True),
                           self.base_cols]

    @staticmethod
    def to_windows_csv(df, file_name):
        """Save to CSV in windows-compatible format. Can be read into Excel."""
        df.to_csv(file_name, encoding='utf-8-sig')
