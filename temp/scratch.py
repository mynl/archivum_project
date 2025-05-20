"""Script experiments."""

from pathlib import Path
import re

import pandas as pd
# import latexcodec


class Bib2df():
    """Bibtex file to dataframe."""

    _r_brace1 = re.compile(r'{(.)}')
    _r_brace2 = re.compile(r'{{(.)}}')
    _r_initials1 = re.compile(r'([A-Z])(?:\.| )?([A-Z])(?:\.| )?([A-Z])\.?')   # for A.B.C. -> A. B. C.
    _r_initials2 = re.compile(r'([A-Z])(?:\.| )?([A-Z])(\.|$)')   # for AH -> A. H.
    _r_initials3 = re.compile(r' ([A-Z])$')            # for A  -> A.


    def __init__(self, p, fillna=True):
        """
        Read Path p into bibtex df.

        Use fillna=False to use contents.
        """
        self.txt = p.read_text(encoding='utf-8')
        self.stxt = re.split(r'^@', self.txt, flags=re.MULTILINE)
        l = map(self.parse_line, self.stxt[1:])
        self._df = pd.DataFrame(l)
        # the bibtex row 0 is mendeley junk
        # doing this keeps stxt and the df on the same index
        self._df.index = range(1, 1+len(self._df))
        if fillna:
            self._df = self._df.fillna('')
        self._file_field_df = None
        self._file_errs = None

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

    def contents(self, verbose=False):
        """Contents info on df - distinct values, fields etc."""
        ans = []
        for c in self.df.columns:
            vc = self.df[c].value_counts()
            nonna = len(self.df) - sum(self.df[c].isna())
            ans.append([c, nonna, len(vc)])
            if verbose:
                print(c)
                print('='*len(c))
                print(f'{len(vc)} distinct values')
                print(vc.head(10))
                print('-'*80)
                print()
        cdf = pd.DataFrame(ans, columns=['column', 'nonna', 'distinct'])
        return cdf

    def author_map_df(self):
        """DataFrame of author name showing a transition to a normalized form."""
        df = pd.DataFrame({'original': self.distinct('author')})
        self.last_decode = []
        df['unicoded'] = df.original.map(self.tex_to_unicode)
        df['initials'] = df.unicoded.map(self.normalize_initials)
        df['proposed'] = df.initials
        print(f'Decode errors: {len(self.last_decode) = }')
        return df

    def journal_map_df(self):
        df = pd.DataFrame({'original': self.distinct('journal')})
        self.last_decode = []
        df['unicoded'] = df.original.map(self.tex_to_unicode)
        print(f'Decode errors: {len(self.last_decode) = }')
        return df

    def distinct(self, c):
        """Return distinct occurrences of col c."""
        if c == 'author':
            return sorted(
                set(author.strip() for s in self.df.author.dropna() for author in s.split(" and "))
            )
        else:
            return sorted(set([i for i in self.df[c] if i != '']))

    def tex_to_unicode(self, s: str) -> str:
        """Tex codes to Unicode for a string and removing braces with single character."""
        try:
            s = self._r_brace2.sub(r'\1', s.encode('latin1').decode('latex'))
            s= self._r_brace1.sub(r'\1', s)
            if s.find(',') > 0 and s == s.upper():
                s = s.title()
            return s
        except ValueError as e:
            self.last_decode.append(f'tex_to_unicode DECODE ERR | {s:<25s} | {e}')
            return s

    _r_filter = re.compile(r'^[A-Z]{,5}$')

    def normalize_initials(self, s: str) -> str:
        """Ensure initials all have periods."""

        try:
            if  self._r_filter.match(s):
                return s
            s1 = self._r_initials1.sub(r'\1. \2. \3.', s)
            s2 = self._r_initials2.sub(r'\1. \2.', s1)
            s3 = self._r_initials3.sub(r' \1.', s2)
            return s3
        except ValueError as e:
            self.last_decode.append(f'normalize_initials INITIALS ERR: {s:<25s}\t{e}')
            return s
