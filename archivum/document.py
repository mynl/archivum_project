"""
Documents class: manages physical document files.

Meta data, full text extraction etc. For pdf and (?) epub, djvu, dvi and
other formats.
"""

from pathlib import Path

import pandas as pd


class Documents():
    """Manage physical document files."""

    def __init__(self, library):
        """Create Documents class based on Library config."""
        self.library = library
        self._library_file_df = None
        self._file_errs = None
        self._document_df = None

    def __repr__(self):
        return f'Document(lib={self.library.name})'

    @property
    def library_file_df(self):
        """Information about files **referenced** in the library database."""
        if self._library_file_df is None:
            self._parse_library_file_field()
        return self._library_file_df

    def _parse_library_file_field(self):
        """Split out file field."""
        ans = []
        self._file_errs = []
        for i, f in self.library.database[['file']].iterrows():
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
        self._library_file_df = pd.DataFrame(ans, columns=['idx', 'drive', 'file', 'type']).set_index('idx', drop=False)
        self._library_file_df.index.name = 'i'

    @property
    def document_df(self):
        """
        Read file information for the current library's pdf store.

        Returns dataframe describing **actual files**. These may or may not
        be referenced in library.database.
        Currently PDFs only.

        """
        if self._document_df is None:
            pdfs = list(Path(self.library.pdf_dir).rglob('*.pdf'))
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
                })
            df = pd.DataFrame(ans)
            tz = self.library.timezone
            df["create"] = pd.to_datetime(df["create"], unit="ns").dt.tz_localize("UTC").dt.tz_convert(tz)
            df["mod"] = pd.to_datetime(df["mod"], unit="ns").dt.tz_localize("UTC").dt.tz_convert(tz)
            df["access"] = pd.to_datetime(df["access"], unit="ns").dt.tz_localize("UTC").dt.tz_convert(tz)
            self._document_df = df
            print(f'Created document_df with {len(ans)} files')
        return self._document_df

    def reference_docs(self):
        """Files referenced in the database."""
        pass

    def ref_doc_matrix(self):
        """Cross referencing between docs and refs."""

