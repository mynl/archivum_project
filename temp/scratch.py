"""Script experiments."""

from collections import namedtuple
from collections import defaultdict

from pathlib import Path
import re
import unicodedata

import numpy as np
import pandas as pd
import latexcodec


# def remove_accents(s: str) -> str:
#     return ''.join(
#         c for c in unicodedata.normalize('NFD', s)
#         if unicodedata.category(c) != 'Mn'
#     )


# def safeyear(s):
#     """Safe format of s as a year for greater_tables."""
#     try:
#         return f'{int(s)}'
#     except:
#         return s


# def suggest_tag(df):
#     """Suggest a tag from a row of df."""
#     a = df.author.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().str.replace(r' |\.|\{|\}|\-', '', regex=True)
#     e = df.editor.map(remove_accents).str.split(',', expand=True, n=1)[0].str.strip().replace(r' |\.|\{|\}|\-', '', regex=True)
#     y = df['year'].map(safeyear)
#     return np.where(a != '', a + y, np.where(e!='', e + y, 'NOTAG'))


#     # a, e, y = row[['author', 'editor', 'year']]
#     # a = remove_accents(a.split(',')[0])
#     # e = remove_accents(e.split(',')[0])
#     # y = safeyear(y)
#     # if a != '':
#     #     return f'{a}{y}'
#     # elif e != '':
#     #     return f'{e}{y}'
#     # else:
#     #     print(row.index, 'NO A OR E')

# def suggest_filename(s):
#     """Clean file name for windows."""
#     pass


# class KeyAllocator:

#     def __init__(self, existing: set[str]):
#         """Class to determine the next key (@AuthorYYYY) given a list of existing keys."""
#         self.existing = set(existing)
#         self.pattern = re.compile(r'^([A-Z][^0-9]+)(\d{4})([a-z]?)$')
#         self.allocators = defaultdict(self._make_iter)

#     def _make_iter(self):
#         def gen():
#             yield ''  # first without suffix
#             for c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ':
#                 yield c
#         return gen()

#     def next_key(self, tag) -> str:
#         # name: str, year: int
#         m = self.pattern.match(tag)
#         try:
#             name = m[1]
#             year = m[2]
#         except TypeError:
#             # m - none, no match found
#             return tag
#         else:
#             base = f"{name}{year}"
#             it = self.allocators[(name, year)]
#             while True:
#                 suffix = next(it)
#                 candidate = base + suffix
#                 if candidate not in self.existing:
#                     self.existing.add(candidate)
#                     return candidate

#     __call__ = next_key


# class Bib2df():
#     """Bibtex file to dataframe."""

#     _r_brace1 = re.compile(r'{(.)}')
#     _r_brace2 = re.compile(r'{{(.)}}')
#     _r_initials1 = re.compile(r'([A-Z])(?:\.| )?([A-Z])(?:\.| )?([A-Z])\.?')   # for A.B.C. -> A. B. C.
#     _r_initials2 = re.compile(r'([A-Z])(?:\.| )?([A-Z])(\.|$)')   # for AH -> A. H.
#     _r_initials3 = re.compile(r' ([A-Z])$')            # for A  -> A.

#     _char_unicode_dict = {
#         '“': '"',    # left double quote
#         '”': '"',    # right double quote
#         '„': '"',    # low double quote
#         '«': '"',    # double angle quote
#         '»': '"',
#         '′': "'",
#         '‘': "'",    # left single quote
#         '’': "'",    # right single quote
#         '‚': "'",    # low single quote
#         '′': "'",    # prime
#         '‵': "'",    # reversed prime
#         '‹': "'",    # single angle quote
#         '›': "'",

#         '\u00a0': ' ',    # non-breaking space
#         '\u200b': '',     # zero-width space
#         '\ufeff': '',     # BOM
#     }

#     _char_map = str.maketrans(_char_unicode_dict)

#     # for regexp based fixes
#     _re_subs = {
#         '–': '--',    # en dash → hyphen
#         '—': '---',    # em dash → hyphen
#     }
#     _re_subs_compiled = re.compile('|'.join(map(re.escape, _re_subs)))

#     base_cols = ['tag', 'type', 'author', 'title', 'year', 'journal', 'file']

#     def __init__(self, p, fillna=True):
#         """
#         Read Path p into bibtex df.

#         Use fillna=False to use contents.
#         """
#         self.txt = p.read_text(encoding='utf-8').translate(self._char_map)
#         self.txt, n = self._re_subs_compiled.subn(lambda m: self._re_subs[m.group()], self.txt)
#         print(f'uber regex sub found {n = } replacements')
#         self.stxt = re.split(r'^@', self.txt, flags=re.MULTILINE)
#         l = map(self.parse_line, self.stxt[1:])
#         self._df = pd.DataFrame(l)
#         # the bibtex row 0 is mendeley junk
#         # doing this keeps stxt and the df on the same index
#         self._df.index = range(1, 1 + len(self._df))
#         if fillna:
#             self._df = self._df.fillna('')
#         self._file_field_df = None
#         self._file_errs = None
#         self.ported_df = None
#         self._author_map_df = None
#         self.all_unicode_errors = None

#     @property
#     def df(self):
#         return self._df

#     @property
#     def file_field_df(self):
#         if self._file_field_df is None:
#             self.parse_file_field()
#         return self._file_field_df

#     @staticmethod
#     def parse_line(entry):
#         result = {}

#         # Step 1: Extract type and tag
#         # windows GS bibtex pastes come in with \r\n
#         entry = entry.replace('\r\n  ', '\n')
#         header_match = re.match(r'@?(\w+)\{([^,]+),', entry)
#         if not header_match:
#             print("Error: Unable to parse entry header.")
#             return None
#         result['type'], result['tag'] = header_match.groups()

#         # Step 2: Remove header and final trailing '}'
#         body = entry[header_match.end():].strip()
#         if body.endswith('}'):
#             body = body[:-1].strip() + ",\n"

#         for m in re.finditer(r'([a-zA-Z\-]+) = {(.*?)},\n', body, flags=re.DOTALL):
#             try:
#                 k, v = m.groups()
#                 result[k] = v
#             except ValueError:
#                 print('going slow')
#                 return Bib2df.parse_line_slow(entry)
#         return result

#     @staticmethod
#     def parse_gs(entry):
#         """Parse a Google Scholar Bibtex entry, copied and pasted."""
#         result = {}

#         # Step 1: Extract type and tag
#         # windows GS bibtex pastes come in with \r\n
#         entry = entry.replace('\r\n  ', '\n')
#         header_match = re.match(r'@?(\w+)\{([^,]+),', entry)
#         if not header_match:
#             print("Error: Unable to parse entry header.")
#             return None
#         result['type'], result['tag'] = header_match.groups()

#         # Step 2: Remove header and final trailing '}'
#         body = entry[header_match.end():].strip()
#         if body.endswith('}'):
#             body = body[:-1].strip() + ",\n"

#         # GS has no spaces around equals
#         for m in re.finditer(r'([a-zA-Z\-]+)={(.*?)},\n', body, flags=re.DOTALL):
#             try:
#                 k, v = m.groups()
#                 result[k] = v
#             except ValueError:
#                 print('going slow')
#                 return Bib2df.parse_line_slow(entry)
#         return result

#     @staticmethod
#     def parse_line_slow(entry):
#         result = {}

#         # Step 1: Extract type and tag
#         header_match = re.match(r'(\w+)\{([^,]+),', entry)
#         if not header_match:
#             print("Error: Unable to parse entry header.")
#             return None
#         result['type'], result['tag'] = header_match.groups()

#         # Step 2: Remove header and final trailing '}'
#         body = entry[header_match.end():].strip()
#         if body.endswith('}'):
#             body = body[:-1].strip()

#         # Step 3: Find all key = { positions
#         matches = list(re.finditer(r'([a-zA-Z\-]+) = \{', body))
#         n = len(matches)

#         for i, match in enumerate(matches):
#             key = match.group(1)
#             val_start = match.end()
#             val_end = matches[i + 1].start() if i + 1 < n else len(body)

#             # Strip off the trailing "}," (assumes always ",\n" after value)
#             value = body[val_start:val_end].rstrip().rstrip(',')
#             if value.endswith('}'):
#                 value = value[:-1].rstrip()

#             result[key] = value

#         return result

#     def parse_file_field(self):
#         """Split out file field."""
#         ans = []
#         self._file_errs = []
#         for i, f in self.df[['file']].iterrows():
#             try:
#                 for bit in f.file.split(';'):
#                     x = bit.split(':')
#                     # print(i, len(x))
#                     if len(x) == 4:
#                         ans.append([i, *x[1:]])
#                     # elif len(x) > 3:
#                     #     ans.append([i, x[1], x[2], x[3:]])
#                     else:
#                         self._file_errs.append([i, x[1:]])
#             except AttributeError:
#                 self._file_errs.append([i, 'Attribute', f.file])
#         self._file_field_df = pd.DataFrame(ans, columns=['idx', 'drive', 'file', 'type']).set_index('idx', drop=False)
#         self._file_field_df.index.name = 'i'

#     def contents(self, ported=False, verbose=False):
#         """Contents info on df - distinct values, fields etc."""
#         ans = []
#         if ported:
#             df = self.ported_df
#         else:
#             df = self.df
#         for c in df.columns:
#             vc = df[c].value_counts()
#             nonna = len(df) - sum(df[c].isna())
#             ans.append([c, nonna, len(vc)])
#             if verbose:
#                 print(c)
#                 print('=' * len(c))
#                 print(f'{len(vc)} distinct values')
#                 print(vc.head(10))
#                 print('-' * 80)
#                 print()
#         cdf = pd.DataFrame(ans, columns=['column', 'nonna', 'distinct'])
#         return cdf

#     @property
#     def author_map_df(self):
#         """DataFrame of author name showing a transition to a normalized form."""
#         if self._author_map_df is None:
#             df = pd.DataFrame({'original': self.distinct('author')})
#             self.last_decode = []
#             df['unicoded'] = df.original.map(self.tex_to_unicode)
#             df['initials'] = df.unicoded.map(self.normalize_initials)
#             df['proposed'] = df.initials
#             print(f'Field: authors\nDecode errors: {len(self.last_decode) = }')
#             self._author_map_df = df
#         return self._author_map_df

#     def distinct(self, c):
#         """Return distinct occurrences of col c."""
#         if c == 'author':
#             return sorted(
#                 set(author.strip() for s in self.df.author.dropna() for author in s.split(" and "))
#             )
#         else:
#             return sorted(set([i for i in self.df[c] if i != '']))

#     # special unicode errors
#     _errors_mapper = {'Caicedo, Andr´es Eduardo': 'Caicedo, Andrés Eduardo',
#                       'Cerreia‐Vioglio, Simone': 'Cerreia‐Vioglio, Simone',
#                       'Cerreia–Vioglio, S.': 'Cerreia–Vioglio, S.',
#                       'Cireşan, Dan': 'Cireșan, Dan',
#                       'J.B., SEOANE-SEP´ULVEDA': 'J.B., Seoane-Sepúlveda',
#                       'JIM´ENEZ-RODR´IGUEZ, P.': 'Jiménez-Rodríguez, P.',
#                       'Joldeş, Mioara': 'Joldeș, Mioara',
#                       'Lesne, Jean‐Philippe ‐P': 'Lesne, Jean‐Philippe ‐P',
#                       'MU˜NOZ-FERN´ANDEZ, G.A.': 'Muñoz-Fernández, G.A.',
#                       'Naneş, Ana Maria': 'Naneș, Ana Maria',
#                       'Paradıs, J': 'Paradís, J',
#                       "P{\\'{a}}stor, Ľ": 'Pástor, Ľ',
#                       'Uludağ, Muhammed': 'Uludağ, Muhammed',
#                       'Ulug{\\"{u}}lyaǧci, Abdurrahman': 'Ulugülyaǧci, Abdurrahman',
#                       'Zitikis, Riċardas': 'Zitikis, Riċardas',
#                       'de la Pen̄a, Victor H.': 'de la Peña, Victor H.',
#                       "{L{\\'{o}}pez\xa0de\xa0Vergara}, Jorge E.": 'López\xa0de\xa0Vergara, Jorge E.'}

#     def tex_to_unicode(self, s_in: str) -> str:
#         """Tex codes to Unicode for a string and removing braces with single character."""
#         if pd.isna(s_in):
#             return s_in
#         try:
#             s = self._r_brace2.sub(r'\1', s_in.encode('latin1').decode('latex'))
#             s = self._r_brace1.sub(r'\1', s)
#             if s.find(',') > 0 and s == s.upper():
#                 s = s.title()
#             return s
#         except ValueError as e:
#             s = self._errors_mapper.get(s_in, s_in)
#             if s_in not in self._errors_mapper:
#                 self.last_decode.append(s_in)
#             # (f'tex_to_unicode DECODE ERR | {s:<25s} | {e}')
#             return s

#     _r_filter = re.compile(r'^[A-Z]{,5}$')

#     def normalize_initials(self, s: str) -> str:
#         """Ensure initials all have periods."""

#         try:
#             if self._r_filter.match(s):
#                 return s
#             s1 = self._r_initials1.sub(r'\1. \2. \3.', s)
#             s2 = self._r_initials2.sub(r'\1. \2.', s1)
#             s3 = self._r_initials3.sub(r' \1.', s2)
#             return s3
#         except ValueError as e:
#             self.last_decode.append(f'normalize_initials INITIALS ERR: {s:<25s}\t{e}')
#             return s

#     def author_last_multiple_firsts(self):
#         """Last names with multiple firsts, showing the parts."""
#         df = self.author_map_df
#         df[['last', 'rest']] = df['proposed'].str.split(',', n=1, expand=True)
#         df['rest'] = df['rest'].str.strip()

#         return (df.fillna('')
#                 .groupby('last')
#                 .apply(lambda x:
#                 pd.Series([len(x), sorted(set(x.rest))], index=('n', 'set')),
#                 include_groups=False)
#                 .query('n>1'))

#     def author_mapper(self):
#         """dict mapper for author name."""
#         mapper = {k: v for k, v in self.author_map_df[['original', 'proposed']].values}
#         # manual fixes
#         manual_updates = {
#             'Acemoglu, By Daron': 'Acemoglu, Daron',
#             'Candes, E': 'Candès, Emmanuel J.',
#             'Candes, E.': 'Candès, Emmanuel J.',
#             'Candes, E.J.': 'Candès, Emmanuel J.',
#             'Candes, Emmanuel': 'Candès, Emmanuel J.',
#             'Candes, Emmanuel J.': 'Candès, Emmanuel J.',
#             'Cand{\\`{e}}s, Emamnuel J': 'Candès, Emmanuel J.',
#             'Cand{\\`{e}}s, Emmanuel J': 'Candès, Emmanuel J.',
#             'Cand{\\`{e}}s, Emmanuel J.': 'Candès, Emmanuel J.'}
#         mapper.update(manual_updates)
#         return mapper

#     def port_mendeley_file(self):
#         """
#         Normalize each text-based field.

#         Runs through each task in turn, see comments.
#         """
#         dropped_fields = ['abstract', 'annote', 'issn', 'isbn', 'archivePrefix', 'arxivId', 'eprint', 'pmid',
#                           'primaryClass', 'series', 'chapter', 'school',
#                           'organization', 'howpublished'
#                           ]
#         kept_fields = [i for i in self.df.columns if i not in dropped_fields]

#         self.ported_df = self.df[kept_fields].copy()

#         # ============================================================================================
#         # author
#         am = self.author_mapper()

#         def a_fix(v):
#             a = v.split(' and ')
#             anew = [am.get(i, i) for i in a]
#             return ' and '.join(anew)
#         self.ported_df.author = self.ported_df.author.map(a_fix)
#         print('fixed authors')

#         # ============================================================================================
#         # de-tex other text fields
#         self.all_unicode_errors = {}
#         for f in ['title', 'journal', 'publisher', 'institution', 'booktitle', 'address', 'editor',

#                   ]:
#             self.last_decode = []
#             self.ported_df[f] = self.ported_df[f].map(self.tex_to_unicode)
#             if len(self.last_decode):
#                 print(f'Field: {f}\n{len(self.last_decode) = }')
#                 self.all_unicode_errors[f] = self.last_decode.copy()
#             print(f'Fixed {f}')

#         # ============================================================================================
#         # keywords

#         # ============================================================================================
#         # mendeley-tags

#         # ============================================================================================
#         # citations

#         # ============================================================================================
#         # edition

#         # ============================================================================================
#         # files(!)

#         # ============================================================================================
#         # tags and duplicates

#         # ============================================================================================
#         # final checks and balances

#     def show_unicode_errors(self):
#         """Accumulated unicode errors."""
#         if self.all_unicode_errors is None:
#             return None
#         ans = set()
#         for k, v in self.all_unicode_errors.items():
#             ans = ans.union(set([c for l in v for c in l if len(c.encode('utf-8')) > 1]))
#         return ans

#     def no_file(self):
#         """Entries with no files listed."""
#         return self.df.loc[self.df.file == '', self.base_cols]

#     def suggested_tag(self):
#         df = self.ported_df.copy().sort_values(['author', 'year'])
