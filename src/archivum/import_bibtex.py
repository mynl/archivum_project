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

import json
import logging
from functools import partial
from pathlib import Path
import re
from difflib import SequenceMatcher

import datetime as dt

import latexcodec  # noqa

# import Levenshtein  # per Gemini prefer to use rapidfuzz
from rapidfuzz import distance
import numpy as np
import pandas as pd

from . import EMPTY_LIBRARY
from .utilities import (
    remove_accents,
    accent_mapper_dict,
    safe_int,
    TagAllocator,
)
from .trie import Trie
from .library_base import LibraryBase
from .hasher import hash_many3 as hash_many

from .enhancements import save_from_row, path_from_row
# set to True to override where audit files are stored to tmp
# False is default

logger = logging.getLogger(__name__)


## ==================================================================
class Bib2df_Incremental(LibraryBase):
    """
    Bibtex file to dataframe - library aware.

    Code started as a copy of mendeley_port.Bib2df, adjusted for incremental,
    library-aware use. .
    """

    # for de-texing single characters in braces
    _r_brace1 = re.compile(r"{(.)}")
    _r_brace2 = re.compile(r"{{(.)}}")

    # base columns used by the app for quick output displays
    _base_cols = ["tag", "type", "author", "title", "year", "journal", "file"]

    # base columns expected by import_bibtex_file
    _base_fields = [
        "title",
        "journal",
        "publisher",
        "institution",
        "booktitle",
        "address",
        "editor",
        "mendeley-tags",
        "edition",
    ]

    # =====================================================================================================
    # user defined mappers: these can be customized
    # _char_map is less likely to be changed: it is applied to the raw text read from the bibtex file
    _char_unicode_dict = {
        "“": '"',  # left double quote
        "”": '"',  # right double quote
        "„": '"',  # low double quote
        "«": '"',  # double angle quote
        "»": '"',
        "‘": "'",  # left single quote
        "’": "'",  # right single quote
        "‚": "'",  # low single quote
        "′": "'",  # prime
        "‵": "'",  # reversed prime
        "‹": "'",  # single angle quote
        "›": "'",
        "\u00a0": " ",  # non-breaking space
        "\u200b": "",  # zero-width space
        "\ufeff": "",  # BOM
    }

    _char_map = str.maketrans(_char_unicode_dict)

    # _re_subs is also applied to raw text to adjust en and em dashes.
    _re_subs = {
        "–": "--",  # en dash → hyphen
        "—": "---",  # em dash → hyphen
    }
    _re_subs_compiled = re.compile("|".join(map(re.escape, _re_subs)))

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
    _omitted_bibtex_fields = [
        "abstract",
        "annote",
        "issn",
        # "isbn",
        # "archivePrefix",
        # 'arxivId',
        # "eprint",
        "pmid",
        "primaryClass",
        "series",
        "chapter",
        "school",
        # "organization",
        "howpublished",
        "keywords",
    ]

    # end customizable mappers
    # =====================================================================================================

    def __init__(
        self,
        *,
        bibtex_file_path,
        doc_dir,
        reference_library,
        fillna=True,
        errors_mapper=None,
        remap_dashes=False,
        add_hashes=False,
        incremental=False,
        qd=None,
        write_audit=True,
    ):
        """
        Read Path p into bibtex df, doc_dir is a Path to pdf files (must exist)

        This class is very property driven...dataframes are created
        when needed. Eg the audit dir is only created if you get to that
        point.

        The one-time Mendeley porting version of this finds all
        documents in doc_dir. We don't need to do that here, we
        know the docs will exist in the right place. However,
        for the time being will keep the code, but only transfer
        over files as needed.

        afile = an actual file
        vfile = a named reference in the bibtex file that may not correspond to an afile

        doc_dir is where the afile documents live; all afiles are found.

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
        Files saved to /tmp for nightly delete. On update they are committed
        to the library import folder.
        """
        self.bibtex_file_path = Path(bibtex_file_path)
        self.name = self.bibtex_file_path.stem
        self.doc_dir = Path(doc_dir) if doc_dir else None
        self.reference_library = reference_library or EMPTY_LIBRARY
        self.fillna = fillna
        self.errors_mapper = errors_mapper or {}
        self.remap_dashes = remap_dashes
        self._add_hashes = add_hashes or incremental
        self.incremental = incremental
        self.write_audit = write_audit
        assert self.bibtex_file_path.exists(), "Bibtex file must exist"
        if self.doc_dir and not self.doc_dir.exists():
            logger.info("PDF directory is None or does not exist")

        # if you write audits, also save  - this is a flag
        self._errors_mapper_saved = False
        # for properties
        self._raw_df = pd.DataFrame()
        self._author_map_df = pd.DataFrame()
        self._vfile_df = pd.DataFrame()
        self._doc_df = pd.DataFrame()
        self._ref_doc_df = pd.DataFrame()
        self._ref_df = pd.DataFrame()
        self._best_match_df = pd.DataFrame()
        self._ref_no_doc = pd.DataFrame()
        self._ported_df = (
            pd.DataFrame()
        )  # the "raw" ported df, includes file column, but otherwise like ref_df
        self._database = pd.DataFrame()

        # for audit and debugging
        self._last_missing_vfiles = None
        self._last_decode = None
        self.__audit_dir_path = None
        self._all_unicode_errors = None
        # for duplicate detection: normalized titles and doi
        self._existing_title_norm = None
        self._dois = None
        self._self_test = False

        # timestamp for audit files and arc-source for imports
        self.timestamp = dt.datetime.now().strftime("%Y-%m-%d_at_%H-%M-%S")
        self.qd = qd or print

    @property
    def raw_df(self):
        """DataFrame of raw(ish) information read directly from bibtex file."""
        # gemini improvement: filter nones and the space before @
        if self._raw_df.empty:
            logger.info("===>> creating raw_df property <<====")

            self.txt = self.bibtex_file_path.read_text(encoding="utf-8").translate(
                self._char_map
            )

            if self.remap_dashes:
                self.txt, n = self._re_subs_compiled.subn(
                    lambda m: self._re_subs[m.group()], self.txt
                )
                logger.info(f"remap dashes regex sub found {n = } replacements")

            # Split on Start-of-line + optional space + @
            # This consumes the indent and the @, leaving the type as the start of the chunk
            # (?m) means multiline (like a flag)
            self.stxt = re.split(r"(?m)^\s*@", self.txt)

            # Process ALL chunks. parse_line handles filtering.
            # Use a list comprehension to filter out Nones immediately
            parsed_lines = [
                res for res in map(self.parse_line, self.stxt)
                if res is not None
            ]

            self._raw_df = pd.DataFrame(parsed_lines)

            # Reset index to be 1-based standard
            self._raw_df.index = range(1, 1 + len(self._raw_df))

            if self.fillna:
                self._raw_df = self._raw_df.fillna("")

        return self._raw_df

    @property
    def ported_df(self):
        if self._ported_df.empty:
            logger.info("===>> creating ported_df property <<====")
            self.import_bibtex_file()
        return self._ported_df

    @property
    def ref_df(self):
        """The reference df contains no file information and has tag NOT as the index."""
        if self._ref_df.empty:
            logger.info("===>> creating ref_df property <<====")
            self._ref_df = (
                self.ported_df.drop(columns="file")
                if "file" in self.ported_df
                else self.ported_df
            )
            self._ref_df["arc-source"] = f"bibtex {self.name} at {self.timestamp}"
        return self._ref_df

    @property
    def doc_df(self):
        """
        Read file information for the current library's pdf store.

        Returns dataframe describing **actual files** (afiles). These may or may not
        be referenced in library.database.
        Currently only PDFs.
        """
        if self._doc_df.empty and len(self._doc_df.columns) == 0:
            logger.info("===>> creating doc_df property <<====")
            if self.doc_dir is None or not self.doc_dir.exists():
                dt_type = f'datetime64[ns, {self.reference_library.config.timezone}]'
                column_dtypes = {
                    "name": "object",
                    "path": "object",
                    "mod": dt_type,
                    "create": dt_type,
                    "access": dt_type,
                    "node": "int64",
                    "links": "int64",
                    "size": "int64",
                    "suffix": "object",
                    "hash": "object",
                }
                # Create an empty DataFrame using the defined dtypes
                self._doc_df = pd.DataFrame(columns=column_dtypes.keys()).astype(
                    column_dtypes
                )
                logger.info(
                    f"pdf folder is None or does not exist; and created empty doc_df with {len(self._doc_df.columns)} columns"
                )
            else:
                # actually have documents
                docs = self.reference_library.find_docs(self.doc_dir)
                logger.info("Found %s afiles (actual document files).", len(docs))
                ans = []
                for p in docs:
                    p = p.absolute()
                    stat = p.stat(follow_symlinks=True)
                    ans.append(
                        {
                            "name": p.name,
                            "path": str(p.as_posix()),
                            "mod": stat.st_mtime_ns,
                            "create": stat.st_ctime_ns,
                            "access": stat.st_atime_ns,
                            "node": stat.st_ino,
                            "links": stat.st_nlink,
                            "size": stat.st_size,
                            "suffix": p.suffix,
                            "hash": "",
                        }
                    )
                df = pd.DataFrame(ans)
                tz = self.reference_library.config.timezone  # "Europe/London"
                df["create"] = (
                    pd.to_datetime(df["create"], unit="ns")
                    .dt.tz_localize("UTC")
                    .dt.tz_convert(tz)
                )
                df["mod"] = (
                    pd.to_datetime(df["mod"], unit="ns")
                    .dt.tz_localize("UTC")
                    .dt.tz_convert(tz)
                )
                df["access"] = (
                    pd.to_datetime(df["access"], unit="ns")
                    .dt.tz_localize("UTC")
                    .dt.tz_convert(tz)
                )
                if self._add_hashes:
                    logger.info("Adding hashes and versions")
                    missing_docs = df.path.values
                    hashes = hash_many(
                        [Path(p) for p in missing_docs], 
                        workers=self.reference_library.config.hash_workers
                    )
                    # hashes returns dict Path->hash, so lookup on Path(x)
                    df.hash = df.path.map(lambda x: hashes.get(Path(x), ""))
                    
                    # Assign versions based on existing library docs
                    # We need to be careful not to duplicate (hash, path) pairs if they already exist
                    lib_docs = self.reference_library.doc_df
                    
                    def assign_version(row):
                        h = row.hash
                        p = row.path
                        if h == "": return -1
                        
                        # 1. Check if this exact (hash, path) already exists in lib
                        if not lib_docs.empty:
                            match = lib_docs[(lib_docs.hash == h) & (lib_docs.path == p)]
                            if not match.empty:
                                return match.version.iloc[0]
                            
                            # 2. If not, get next available version for this hash
                            existing_versions = lib_docs[lib_docs.hash == h].version
                            if not existing_versions.empty:
                                return existing_versions.max() + 1
                        
                        return 0 # Default for new hash

                    # This is a bit slow for large imports, but safe.
                    # We also need to account for duplicates WITHIN the import itself.
                    df = df.sort_values(['hash', 'path'])
                    df['version'] = 0 # Placeholder
                    
                    # Group by hash and assign incremental versions, 
                    # but offset by whatever is in the library
                    for h, group in df.groupby('hash'):
                        offset = 0
                        if not lib_docs.empty:
                            existing = lib_docs[lib_docs.hash == h]
                            if not existing.empty:
                                offset = existing.version.max() + 1
                        
                        df.loc[group.index, 'version'] = range(offset, offset + len(group))

                # set variable
                self._doc_df = df
                logger.info(
                    f"Scanned documents folder and created doc_df with {len(ans)} files"
                )
        return self._doc_df

    @property
    def vfile_df(self):
        """
        Information about virtual files (vfiles) found in the file field
        in the Mendeley bibtex file.

        Parses file field created by Mendeley in order to discover them.

        Mendeley's internal file(s) field added to bibtex files. Looks like
        a semicolon separated list of the form
        :C\\:/S/new-papers/Blackwell/1953_Equivalent Comparisons of Experiments.pdf:pdf
        Oddly, empty vfiles are ::
        """
        def proc_vfile(vf_drive, vf_name):
            """create correct absolute Path from vf_name, str from bibtex file."""
            # weirdly :c\:
            vf_drive = f'{vf_drive[0]}:'
            p = Path(vf_drive + vf_name)
            if p.is_absolute():
                return str(p.as_posix())
            else:
                p = self.bibtex_file_path.parent / vf_name
                return str(p.as_posix())

        if self._vfile_df.empty:
            logger.info("===>> creating vfile_df property <<====")
            ans = []
            self._file_errs = []
            df = self.ported_df.set_index("tag")
            for tag, value in df.file.str.split(";").fillna("").items():
                # the items are: tag=0,1,... and value a list of strings
                # of the form :drive\\:file:file_type
                # on splitting at ":" these have four parts:
                # before drive (empty), drive, path, file_type
                try:
                    for ref in value:
                        # some empty refs come through as [::]
                        # these should be ignored - they are not afiles
                        if ref == "::":
                            continue
                        x = ref.split(":")
                        if len(x) == 4:
                            # drive, filename and type
                            d, f, t = x[1:]
                            ans.append([tag, d, proc_vfile(d, f), t])
                        else:
                            self._file_errs.append([tag, *x[1:]])
                except AttributeError:
                    self._file_errs.append([tag, "Attribute", *ref])
            self._vfile_df = pd.DataFrame(
                ans, columns=["tag", "drive", "vfile", "type"]
            )
            # resolve the file names
            # self._vfile_df.vfile = [
            #     str(Path(vf).absolute().as_posix()) for vf in self._vfile_df.vfile
            # ]
            logger.info(f"Created vfile_df with {len(self._vfile_df)} rows.")
        return self._vfile_df

    @property
    def ref_doc_df(self):
        """
        Make the reference/document dataframe by matching vfiles to afiles.

        vfiles (virtual files) are references within the file field in the
          mendeley bibtex file.
        afiles are actual files that exist in the pdf_path directory.
        """
        # columns are ref_id=tag and afile name
        if self._ref_doc_df.empty and len(self._ref_doc_df.columns) == 0:
            logger.info("===>> creating ref_doc_df property <<====")
            
            # Identify which vfiles aren't in our doc_df scan
            actual_files = set(self.doc_df.path)
            missing_vfiles_initial = [r.vfile for _, r in self.vfile_df.iterrows() if r.vfile not in actual_files]
            
            # Check if any of these "missing" vfiles actually exist on disk
            found_externally = []
            for vfile in missing_vfiles_initial:
                p = Path(vfile)
                if p.exists() and p.is_file():
                    found_externally.append(p)
            
            if found_externally:
                logger.info(f"Found {len(found_externally)} files at absolute paths outside scan directory.")
                # We need to add these to doc_df so they get hashed and versioned
                new_rows = []
                for p in found_externally:
                    p = p.absolute()
                    stat = p.stat(follow_symlinks=True)
                    new_rows.append({
                        "name": p.name,
                        "path": str(p.as_posix()),
                        "mod": stat.st_mtime_ns,
                        "create": stat.st_ctime_ns,
                        "access": stat.st_atime_ns,
                        "node": stat.st_ino,
                        "links": stat.st_nlink,
                        "size": stat.st_size,
                        "suffix": p.suffix,
                        "hash": "",
                    })
                
                df_ext = pd.DataFrame(new_rows)
                tz = self.reference_library.config.timezone
                for col in ["create", "mod", "access"]:
                    df_ext[col] = (
                        pd.to_datetime(df_ext[col], unit="ns")
                        .dt.tz_localize("UTC")
                        .dt.tz_convert(tz)
                    )
                
                # Hash them immediately
                logger.info(f"Hashing {len(df_ext)} external files...")
                ext_hashes = hash_many(
                    [Path(p) for p in df_ext.path], 
                    workers=self.reference_library.config.hash_workers
                )
                df_ext.hash = df_ext.path.map(lambda x: ext_hashes.get(Path(x), ""))
                
                # Assign versions (0 for these new external files)
                df_ext['version'] = 0
                
                # Append to our doc_df
                self._doc_df = pd.concat([self._doc_df, df_ext], ignore_index=True)
                # Refresh actual_files set
                actual_files = set(self.doc_df.path)

            # Now proceed with matching, much fewer should be "missing" now
            missing_vfiles = []
            for i, r in self.vfile_df.iterrows():
                if r.vfile not in actual_files:
                    missing_vfiles.append([i, r.vfile])

            if len(missing_vfiles) == 0:
                logger.info('GOOD NEWS: No missing vfiles!!')
                matcher = {}
            else:
                # Proceed with Levenshtein for truly missing files
                logger.info("\tFound %s missing vfiles (%s of actual files)", len(missing_vfiles),
                    f'{len(missing_vfiles) / len(actual_files):.1%}')
                logger.info("\tLevenshtein (rapidfuzz) matching in ref_doc...")
                ans = []
                for tag, m_vfile in missing_vfiles:
                    best_match = min(
                        actual_files,
                        key=lambda alt: distance.Levenshtein.distance(m_vfile, alt),
                    )
                    ans.append(
                        [
                            tag,
                            m_vfile,
                            best_match,
                            distance.Levenshtein.distance(m_vfile, best_match),
                        ]
                    )
                    logger.debug('\tmatching for %s -> %s', m_vfile, best_match)
                # for reference
                self._best_match_df = pd.DataFrame(
                    ans, columns=["tag", "missing_vfile", "match_afile", "distance"]
                )
                logger.info("\t...Levenshtein matching completed")
                matcher = {
                    vfile: afile
                    for vfile, afile in self._best_match_df[
                        ["missing_vfile", "match_afile"]
                    ].values
                }
            # identity model: ref_doc_df uses hash and version
            # join with this import's doc_df to get the hash and assigned version
            merged = self.vfile_df.merge(
                self.doc_df[['path', 'hash', 'version']], 
                left_on='vfile', right_on='path', how='left'
            )
            # Apply matcher if needed
            if matcher:
                # For files that were matched by Levenshtein, we need to lookup their hash/version
                # in doc_df using the matched afile path
                for i, r in merged[merged.hash.isna()].iterrows():
                    match_path = matcher.get(r.vfile)
                    if match_path:
                        doc_match = self.doc_df[self.doc_df.path == match_path]
                        if not doc_match.empty:
                            merged.loc[i, 'hash'] = doc_match.hash.iloc[0]
                            merged.loc[i, 'version'] = doc_match.version.iloc[0]

            self._ref_doc_df = pd.DataFrame(
                {
                    "tag": merged.tag,
                    "hash": merged.hash,
                    "version": merged.version,
                }
            ).dropna(subset=['hash'])
            self._ref_doc_df['version'] = self._ref_doc_df['version'].astype(int)
            # for ref.
            self._last_missing_vfiles = missing_vfiles
        return self._ref_doc_df

    @property
    def author_map_df(self):
        """
        DataFrame of author name showing a transition to a normalized form.

        Adjusts for initials (puts periods in), takes the longest ! name
        using a Trie, adjusts for accents (guess work!).

        For a new import into an empty library, needs to be run
        on the authors in raw_df to prime the pump
        """
        if self._author_map_df.empty:
            df = pd.DataFrame({"original": self.distinct("author", self.raw_df)})
            self._last_decode = []
            df["unicoded"] = df.original.map(self.tex_to_unicode).str.replace(".", "")
            # space out initials Mild, SJM -> Mild, S J M; works for two of three consecutive initials
            df["spaced"] = df.unicoded.str.replace(
                r"(?<=, )([A-Z]{2,3})\b", lambda m: " ".join(m.group(1)), regex=True
            )

            # diverge from Bib2df: use the reference library
            t = Trie()
            # distinct returns a set
            if (
                self.reference_library != EMPTY_LIBRARY
                and not self.reference_library.ref_df.empty
            ):
                ref_authors = self.distinct("author", self.reference_library.ref_df)
                logger.info(
                    "Building author Trie from reference library, "
                    f"{len(ref_authors)} distinct authors"
                )
            else:
                # no reference authors in the reference library
                # e.g., it could be a start from scratch library
                # prime the pump with the author names we have
                ref_authors = self.distinct("author", self.raw_df)
                logger.info(
                    "Building author Trie from self.raw_df - no reference library authors; "
                    f"{len(ref_authors) = } distinct authors"
                )
            for name in ref_authors:
                t.insert(name.strip(". "))
            # mapping will go from name to longest completion
            mapping = {}
            # authors in self.raw_df
            a = self.distinct("author", self.raw_df)
            logger.info(f"Import contains {len(a)} distinct authors -> remapping")
            for name in a:
                try:
                    m = t.longest_unique_completion(name.strip("."), strict=False)
                except ValueError:
                    # in strict mode means prefix not found -> no change
                    pass
                else:
                    if m != name:
                        # have found a better version
                        mapping[name] = m
            df["longest"] = df.spaced.replace(mapping)
            accent_mapper = accent_mapper_dict(df.longest)
            df["accents"] = df.longest.replace(accent_mapper)
            # initial  periods
            df["proposed"] = df.accents.str.replace(
                r"(\b)([A-Z])( |$)", r"\1\2.\3", case=True, regex=True
            )
            logger.debug(f"Field: authors\nDecode errors: {len(self._last_decode) = }")
            self._author_map_df = df
            # debug
            self.trie = t
            self.mapping = mapping
            self.accent_mapper = accent_mapper
        return self._author_map_df

    @property
    def database(self):
        """Merged database, with exploded authors."""
        if self._database.empty:
            exploded_authors = self.ref_df.assign(
                author=self.ref_df.author.str.split(" and ")
            ).explode("author", ignore_index=True)
            self._database = (
                self.ref_doc_df.merge(exploded_authors, on="tag", how="right")
            ).merge(self.doc_df, on=["hash", "version"], how="left")
            for c in ["node", "links", "size"]:
                if c in self._database.columns:
                    self._database[c] = self._database[c].fillna(0)
            self._database.fillna("")
        return self._database

    def raw_no_file(self):
        """Raw entries with no files listed."""
        return self.raw_df.loc[self.raw_df.file == "", self._base_cols]

    @staticmethod
    def parse_line_original(entry):
        result = {}

        # Step 1: Extract type and tag
        # windows GS bibtex pastes come in with \r\n
        logger.debug('working on entry = %s', entry)
        entry = entry.replace("\r\n *", "\n")
        logger.debug('working on adjusted entry = %s', entry)
        header_match = re.match(r"@?(\w+)\{([^,]+),", entry)
        if not header_match:
            # this is expected, but note it
            logger.info("Unable to parse entry header (generally expected).")
            return None
        result["type"], result["tag"] = header_match.groups()

        # Step 2: Remove header and final trailing '}'
        body = entry[header_match.end() :].strip()
        if body.endswith("}"):
            body = body[:-1].strip()

        logger.debug('working on body = %s', body)
        # this is a bit of an art...
        # for m in re.finditer(r" *([a-zA-Z\-]+) *= *{(.*?)},?\n", body, flags=re.DOTALL):
        # Updated loop with robust regex
        # 1. \s* handles indentation and trailing spaces (fixes eprint)
        # 2. (?:\n|\Z) handles end of line OR end of string (fixes missing file)

        for m in re.finditer(r"\s*([a-zA-Z\-]+)\s*=\s*\{(.*?)\}\s*,?\s*(?:\n|\Z)", body, flags=re.DOTALL):
            try:
                k, v = m.groups()
                result[k] = v
                logger.debug("key = %s and value = %s from m = %s", k, v, m)
            except ValueError:
                logger.info("going slow")
                return Bib2df_Incremental.parse_line_slow(entry)
        return result

    @staticmethod
    def parse_line(entry):
        # 1. Early Exit and Normalization
        if not entry or len(entry.strip()) < 5:
            return None
        entry = entry.replace('\r\n', '\n').replace('\r', '\n')

        # 2. Extract Header
        # Matches type{tag,
        # We allow leading whitespace and a broader range of characters for the entry type
        # to handle artifacts like @temp\.ipynb...
        header_match = re.match(r"\s*@?([a-zA-Z0-9\.\\\-_]+)\s*\{\s*([^,]+),", entry)

        if not header_match:
            logger.debug("Skipping header")
            return None

        result = {}
        result["type"], result["tag"] = header_match.groups()

        # 3. Extract Body
        body = entry[header_match.end():].strip()
        if body.endswith('}'):
            body = body[:-1]

        # 4. Parse Fields
        # Keys: [a-zA-Z]+ only
        # End anchor: (?:\n|\Z) matches newline OR end of string (fixing the missing 'file' issue)
        field_pattern = r"\s*([a-zA-Z\-]+)\s*=\s*\{(.*?)\}\s*,?\s*(?:\n|\Z)"

        for m in re.finditer(field_pattern, body, flags=re.DOTALL):
            try:
                k, v = m.groups()
                result[k] = v.strip()
                logger.debug("key = %s and value = %s from m = %s", k, v, m)
            except ValueError:
                logger.info("going slow for %s", entry)
                return Bib2df_Incremental.parse_line_slow(entry)
        return result

    @staticmethod
    def parse_line_slow(entry):
        result = {}

        # Step 1: Extract type and tag
        header_match = re.match(r"(\w+)\{([^,]+),", entry)
        if not header_match:
            logger.error("Skipping entry header.")
            return None
        result["type"], result["tag"] = header_match.groups()

        # Step 2: Remove header and final trailing '}'
        body = entry[header_match.end() :].strip()
        if body.endswith("}"):
            body = body[:-1].strip()

        # Step 3: Find all key = { positions
        matches = list(re.finditer(r"([a-zA-Z\-]+) = \{", body))
        n = len(matches)

        for i, match in enumerate(matches):
            key = match.group(1)
            val_start = match.end()
            val_end = matches[i + 1].start() if i + 1 < n else len(body)

            # Strip off the trailing "}," (assumes always ",\n" after value)
            value = body[val_start:val_end].rstrip().rstrip(",")
            if value.endswith("}"):
                value = value[:-1].rstrip()

            result[key] = value

        return result

    @staticmethod
    def distinct(column_name, df):
        """Return distinct occurrences of col c in df."""
        # signature changed from mendeley version
        if df is None or df.empty:
            return []
        if column_name == "author":
            return sorted(
                set(
                    author.strip()
                    for s in df.author.dropna()
                    for author in s.split(" and ")
                )
            )
        else:
            return sorted(set([i for i in df[column_name] if i != ""]))

    def tex_to_unicode(self, s_in: str) -> str:
        """
        Tex codes to Unicode for a string and removing braces with single character.

        Errors are added to self._last_decode and looked up in the dictionary
        self.errors_mapper. Work iteratively: run, look at errors, add or update
        entries in self.errors_mapper.
        """
        if pd.isna(s_in):
            return s_in
        try:
            s = self._r_brace2.sub(r"\1", s_in.encode("latin1").decode("latex"))
            s = self._r_brace1.sub(r"\1", s)
            if s.find(",") > 0 and s == s.upper():
                # title case what appear to be names (comma) that are all caps
                s = s.title()
            return s
        except ValueError as e:
            s = self.errors_mapper.get(s_in, s_in)
            if s_in not in self.errors_mapper:
                self._last_decode.append(s_in)
            return s

    def map_tags(self):
        """
        Remap the tags into standard AuthorYYYY[a-z] format for named df.

        Saves a dataframe showing what was done as part of import.

        Updated to use reference library.
        """
        # pattern to remove non-bibtex like characters
        df = self.ported_df[["author", "editor", "year", "tag", "title"]].copy()
        # figure out what the tag "should be"
        pat = r" |\.|\{|\}|\-|'"
        cpat = re.compile(pat)
        # handle mapping names to abbreviations
        # pass the mapping through the same transformation
        tag_mapper = {
            cpat.sub("", k): v
            for k, v in self.reference_library.config.tag_name_mapper.items()
        }
        # but not sure that's worth it?
        # TODO: this is not working!
        a = (
            df.author.map(remove_accents)
            .str.split(",", expand=True, n=1)[0]
            .str.strip()
            .str.replace(pat, "", regex=True)
            .map(lambda x: tag_mapper.get(x, x))
        )
        e = (
            df.editor.map(remove_accents)
            .str.split(",", expand=True, n=1)[0]
            .str.strip()
            .str.replace(pat, "", regex=True)
            .map(lambda x: tag_mapper.get(x, x))
        )
        y = df["year"].map(safe_int)
        # the standardized tag, standard_tag (stem)
        df["standard_tag"] = np.where(a != "", a + y, np.where(e != "", e + y, "NOTAG"))

        noans = df.loc[df.standard_tag == "NOTAG", :]
        if len(noans):
            logger.warning(f"WARNING: Suggested tags failed for {len(noans)} items")
            logger.warning("********  YOU NEED TO FIX THIS!")
            logger.warning(noans)

        # make the proposed tags, build lists as you go with no duplicates
        if self.reference_library != EMPTY_LIBRARY:
            # library is aware and returns tag allocator on [] if
            # if has no database
            self.reference_library.reset_tag_allocator()
            df["proposed_tag"] = [
                self.reference_library.next_tag(a, y)
                for a, y in zip(np.where(a != "", a, e), y)
            ]
        else:
            # make the proposed tags, build lists as you go with no duplicates
            ta = TagAllocator([])
            df["proposed_tag"] = df.standard_tag.map(ta)
            df = df.sort_values("proposed_tag")

        # check all unique
        non_uq_tags = df.loc[df.proposed_tag.duplicated(keep=False)]
        if len(non_uq_tags):
            logger.warning(f"Non-unique tags {len(non_uq_tags) = }")
            print(non_uq_tags)
            logger.info(set(non_uq_tags.proposed_tag))
            raise ValueError("Non-unique proposed tags")
        # enforce unique
        assert df.proposed_tag.is_unique, "ERROR: proposed tags are not unique"

        # save for audit purposes
        self.save_audit_file(df, ".tag-mapping")

        # actually make the change to ported_df
        self.ported_df["tag"] = df["proposed_tag"]

    def author_mapper(self):
        """dict mapper for author name."""
        # dropped manual fixes
        return {k: v for k, v in self.author_map_df[["original", "proposed"]].values}

    def map_authors(self, df_name):
        """Actually apply the author mapper to the author column."""
        df = getattr(self, df_name)
        am = self.author_mapper()

        def f(x):
            sx = x.split(" and ")
            msx = map(lambda x: am.get(x, x), sx)
            return " and ".join(msx)

        df.author = df.author.map(f)
        # audit
        amdf = pd.DataFrame(am.items(), columns=["key", "value"])
        self.save_audit_file(amdf, ".author-mapping")

    def import_bibtex_file(self):
        """
        The work happens here! Do the actual import, and
        normalize each text-based field.

        Runs through each task in turn, see comments.

        For the initial port choose run_add_hoc=True, but
        for incremental updates use False.

        Updated to remove ad_hoc adjustments, dropped extract citations
        from abstract, tags use library, etc.

        Called automatically by ported_df property if needed.
        """
        logger.info("Running import_bibtex_file to create ported_df")
        kept_fields = [
            i for i in self.raw_df.columns if i not in self._omitted_bibtex_fields
        ]
        self._ported_df = self.raw_df[kept_fields].copy()

        # ============================================================================================
        # author: initials, extend, accents - either from reference library or self.raw_df
        # if a new import
        self.map_authors("_ported_df")

        # ensure other edited fields are present
        # this may not be the case for small imports
        for f in self._base_fields:
            if f not in self._ported_df:
                logger.debug("Imported df missing %s - adding", f)
                # probably a string?
                self._ported_df[f] = ""

        # ============================================================================================
        # de-tex other text fields
        self._all_unicode_errors = {}
        for f in [
            "title",
            "journal",
            "publisher",
            "institution",
            "booktitle",
            "address",
            "editor",
            "mendeley-tags",
        ]:
            self._last_decode = []
            self._ported_df[f] = self._ported_df[f].map(self.tex_to_unicode)
            if len(self._last_decode):
                logger.debug(f"\tField: {f}\t{len(self._last_decode) = }")
                self._all_unicode_errors[f] = self._last_decode.copy()
            logger.debug(f"Fixed {f}")

        # audit unicode errors
        ans = []
        for k, v in self._all_unicode_errors.items():
            for mc in v:
                ans.append([k, mc])
        temp = pd.DataFrame(ans, columns=["field", "miscode"])
        self.save_audit_file(temp, ".tex-unicode-errors")

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
        # 1. Run Analysis on FULL unfiltered data
        self._analysis_cache = self.import_analysis(lib_test=True)
        
        # 2. Filter duplicates in Incremental Mode
        if self.incremental:
            logger.info("Incremental Mode: Identifying and removing duplicates...")
            if not self._analysis_cache.empty:
                # ONLY kick out if it is a Skip action (Hash + Metadata match)
                dupes_df = self._analysis_cache[self._analysis_cache.action == "SKIP (Dupe)"]
                if not dupes_df.empty:
                    dupe_tags = dupes_df.tag.unique()
                    logger.warning(f"Kicking out {len(dupe_tags)} metadata+hash duplicates from import.")
                    # We remove from ported_df based on the remapped tag
                    self._ported_df = self._ported_df[~self._ported_df.tag.isin(dupe_tags)]
                    # Update raw_df to match ported_df for reporting purposes
                    self._raw_df = self._raw_df[self._raw_df.tag.isin(self._ported_df.tag)]
                    
                    # Force properties to re-evaluate for the actual import
                    self._ref_df = pd.DataFrame()
                    self._vfile_df = pd.DataFrame()
                    self._ref_doc_df = pd.DataFrame()

        # ============================================================================================
        # final checks and balances, and write out info
        self.save_audit_file(self.raw_df, ".raw-df")
        self.save_audit_file(self._ported_df, ".ported-df")
        
        num_raw = len(self.raw_df)
        num_ported = len(self._ported_df)
        num_dupes = num_raw - num_ported

        import_info_dict = {
            "created": str(self.timestamp),
            "bibtex_file": str(self.bibtex_file_path.absolute()),
            "raw_entries": num_raw,
        }
        
        if self.incremental:
            import_info_dict["duplicates"] = num_dupes
            import_info_dict["net_entries"] = num_ported
        else:
            import_info_dict["ported_entries"] = num_ported

        import_info = pd.DataFrame(
            import_info_dict.items(),
            columns=["key", "value"],
        )
        self.save_audit_file(import_info, ".audit-info")
        return import_info

    def import_analysis(self, lib_test=True):
        """
        Prepare a detailed analysis of the import.
        Returns a DataFrame with columns:
        tag | author | title | hash match | doi match | title match | action
        """
        # Return cached analysis if available (ensures we see filtered records)
        if hasattr(self, '_analysis_cache') and self._analysis_cache is not None:
            return self._analysis_cache

        rows = []
        # We MUST use the internal ported_df BEFORE it is filtered
        # or reconstructed from raw_df
        for idx, raw in self.raw_df.iterrows():
            # Get the remapped tag if it exists in ported_df
            tag = self._ported_df.loc[idx].tag if idx in self._ported_df.index else raw.tag
            title = raw.title
            author = raw.author[:25]
            
            # Get detailed duplicate info
            dup_info = self._check_all_duplicates_v3(raw, tag, idx, lib_test=lib_test)
            
            # Format matches for display
            def fmt_match(m):
                if not m: return "N"
                status = "doc" if m['has_doc'] else "no doc"
                return f"Y ({status})"

            hash_m = "Y" if dup_info['hash'] else "N"
            doi_m = fmt_match(dup_info['doi'])
            title_m = fmt_match(dup_info['title'])
            
            # Determine Action
            action = "Import"
            has_meta_match = (dup_info['doi'] or dup_info['title'])
            
            if dup_info['hash'] and has_meta_match:
                action = "SKIP (Dupe)"
            elif dup_info['hash']:
                action = "Link Existing"
            elif has_meta_match:
                m = dup_info['doi'] or dup_info['title']
                if not m['has_doc']:
                    action = "Import (Fill)"
                else:
                    action = "Merge/Warn"
            
            rows.append({
                "tag": tag,
                "author": author,
                "title": title[:50],
                "hash match": hash_m,
                "doi match": doi_m,
                "title match": title_m,
                "action": action
            })
            
        return pd.DataFrame(rows)

    def _check_all_duplicates_v3(self, raw_row, ported_tag, idx, lib_test=True):
        """Standardized duplicate checker."""
        res = {'hash': None, 'doi': None, 'title': None}
        
        # 1. Hash Check
        if self._add_hashes:
            # Find path in this import's doc_df
            # Use the index to stay aligned
            if idx in self.ported_df.index:
                vfile_mask = self.vfile_df.tag == ported_tag
                if vfile_mask.any():
                    p_str = self.vfile_df[vfile_mask].vfile.iloc[0]
                    p = Path(p_str)
                    doc_mask = self.doc_df.path.map(lambda x: Path(x)) == p
                    if doc_mask.any():
                        h = self.doc_df[doc_mask].hash.iloc[0]
                        if h and h in self.reference_library.doc_df.hash.values:
                            res['hash'] = h

        # 2. DOI/Title Check
        for kind in ('doi', 'title'):
            m = self._possible_duplicate_v2(raw_row, idx, kind, lib_test)
            if m:
                has_doc = m['match_tag'] in self.reference_library.ref_doc_df.tag.values
                res[kind] = {'tag': m['match_tag'], 'has_doc': has_doc}
        
        return res

    def import_analysis_full(self, lib_test=True, strict=False):
        """
        Original detailed diagnostic analysis.
        Shows scores, field changes, and raw vs ported comparison.
        """
        results = []
        for (left, raw_input), (right, revised) in zip(
            self.raw_df.iterrows(), self.ported_df.iterrows()
        ):
            tag_in = raw_input.tag
            tag = revised.tag
            title = revised.title
            (
                kind,
                score_title,
                score_tag,
                match_title,
                match_tag,
            ) = self._possible_duplicate(revised, right, lib_test=lib_test) or (
                "",
                "",
                "",
                0,
                0,
            )

            change = "-" if self._compare(raw_input, revised) else "CHANGED"
            if change == "CHANGED":
                temp = []
                for c in revised.index:
                    if c in raw_input.index and revised[c] != raw_input[c]:
                        temp.append(c)
                if temp:
                    change_cols = ",".join(temp)
                else:
                    change = "-"
                    if strict:
                        index_change = set(revised.index) - set(raw_input.index)
                        change_cols = "idx: " + ",".join(index_change)
                    else:
                        change_cols = ""
            else:
                change_cols = " no chg "
            
            results.append(
                [
                    tag_in,
                    tag,
                    title,
                    kind,
                    score_title,
                    score_tag,
                    match_title,
                    match_tag,
                    raw_input.author[:20],
                    revised.author[:20],
                    change,
                    change_cols,
                ]
            )
        result_df = pd.DataFrame(
            results,
            columns=[
                "tag_in",
                "tag_ported",
                "title",
                "kind",
                "score_title",
                "score_tag",
                "match_title",
                "match_tag",
                "author_in",
                "author_ported",
                "change",
                "change_cols",
            ],
        ).sort_values(["kind", "change", "change_cols"], ascending=[True, False, True])

        return result_df

    def _check_all_duplicates(self, ref_row, ref_row_idx, lib_test=True):
        """Comprehensive check for all types of duplicates."""
        res = {'hash': None, 'doi': None, 'title': None}
        tag = ref_row.get('tag', '')
        
        # 1. Hash Check
        if self._add_hashes:
            mask = self.vfile_df.tag == tag
            if mask.any():
                path_str = self.vfile_df[mask].vfile.iloc[0]
                path = Path(path_str)
                # Lookup in this import's doc_df
                # Ensure we compare Path objects or standardized posix strings
                h_mask = self.doc_df.path.map(lambda x: Path(x)) == path
                if h_mask.any():
                    h = self.doc_df[h_mask].hash.iloc[0]
                    if h and h in self.reference_library.doc_df.hash.values:
                        res['hash'] = h

        # 2. DOI/Title Check
        for kind in ('doi', 'title'):
            m = self._possible_duplicate_v2(ref_row, ref_row_idx, kind, lib_test)
            if m:
                # Check if the matched tag has a document
                has_doc = m['match_tag'] in self.reference_library.ref_doc_df.tag.values
                res[kind] = {'tag': m['match_tag'], 'has_doc': has_doc}
        
        return res

    def _possible_duplicate_v2(self, ref_row, ref_row_idx, kind, lib_test):
        """Helper for DOI/Title matching."""
        # Initialize internal caches if needed
        _ = self._possible_duplicate(ref_row, ref_row_idx, lib_test)
        
        val = ""
        if kind == 'doi':
            val = str(ref_row.get("doi", "") or "").strip().lower()
            if not val: return None
            mask = self._dois == val
        else:
            val = self._normalize_title(ref_row.get("title", ""))
            if not val: return None
            mask = self._existing_title_norm == val
            
        if self._self_test:
            mask[ref_row_idx] = False
            
        if mask.any():
            match_tag = self._possible_duplicate_tags.loc[mask].iloc[0]
            return {'match_tag': match_tag}
        return None

    def update_library(self, save=True):
        """
        Update self.library underlying files and save.

        If self.incremental is True, also shards the new documents into the 
        library's document store.
        """
        if self.incremental:
            logger.info("Incremental Import: Sharding new documents...")
            # Merge necessary metadata for sharding
            # We use the newly created ref_df, ref_doc_df, and doc_df from THIS import
            to_shard = (
                self.ref_doc_df.merge(self.ref_df, on='tag', how='inner')
                               .merge(self.doc_df, on=['hash', 'version'], how='inner')
            )
            
            if not to_shard.empty:
                base_path = self.reference_library.doc_store_path
                
                # Perform hardlinking
                hardlink_maker = partial(save_from_row, base_path=base_path)
                results = to_shard.apply(hardlink_maker, axis=1)
                logger.info("Sharding complete: %s rich links created.", len(results))

                # Update paths in the importer so the library update uses the sharded paths
                to_shard['new_path'] = to_shard.apply(lambda r: path_from_row(r, base_path), axis=1)
                
                # Update doc_df with new paths
                new_doc_df = to_shard.merge(self.doc_df, on=['hash', 'version'], how='inner', suffixes=('', '_old'))
                new_doc_df['path'] = new_doc_df['new_path']
                new_doc_df['name'] = new_doc_df['path'].apply(lambda x: Path(x).name)
                
                # Keep original columns of doc_df
                self._doc_df = new_doc_df[self.doc_df.columns].drop_duplicates(subset=['hash', 'version'])
            else:
                logger.info("Nothing to shard.")

        self.reference_library.update(self)

        # create audit trail
        import_path = (
            self.reference_library.config_path / "import-audit" / self.timestamp
        )
        import_path.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in self._audit_dir_path.glob("*.*"):
            newf = import_path / f.name
            if newf.exists():
                newf.unlink()
            newf.hardlink_to(f)
            count += 1
        logger.info("UPDATE AUDIT: %s files copied to %s", count, import_path)

    def save_audit_file(self, df, suffix):
        """Save df audit file with a standard filename."""
        if not self.write_audit:
            return
        fn = self.bibtex_file_path.stem + suffix + ".csv"
        p = self._audit_dir_path / fn
        df.to_csv(p, encoding="utf-8")
        logger.debug(f"Audit: dataFrame, {len(df) = }, saved to {p.name}.")
        # check about errors mapper
        if self.errors_mapper and not self._errors_mapper_saved:
            fn = self._audit_dir_path / "errors_mapper.json"
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(self.errors_mapper, f, indent=4)
            self._errors_mapper_saved = True

    def show_audit_files(self, top=5, trim=100, bib=False):
        """qd all the audit files."""
        if bib:
            for f in self._audit_dir_path.glob("*.bib"):
                print(f.name)
                print("=" * len(f.name))
                print(f"Lines trimmed to {trim} characters.")
                txt = f.read_text()
                txt = "\n".join([i[:trim] for i in txt.split("\n")])
                print(txt)
                print()

        for f in self._audit_dir_path.glob("*.json"):
            print(f.name)
            print("=" * len(f.name))
            print(f.read_text())
            print()

        if self.qd is None:
            logger.error("Must provide qd to use show_ functions")
            return
        for f in self._audit_dir_path.glob("*.csv"):
            df = pd.read_csv(f, encoding="utf-8-sig")
            self.qd(df.head(top), caption=f.stem, tikz=False)

    def show_generated_dfs(self):
        """Use self.qd to display the main generated dfs."""
        if self.qd is None:
            logger.error("Must provide qd to use show_ functions")
            return
        for nm in ("df", "ported_df", "ref_df", "doc_df", "ref_doc_df"):
            d = getattr(self, nm, None)
            if d is not None:
                self.qd(d, caption=nm)

    def show_unicode_errors(self):
        """Accumulated Unicode errors."""
        if self._all_unicode_errors is None:
            return None
        ans = set()
        for k, v in self._all_unicode_errors.items():
            ans = ans.union(
                set([c for line in v for c in line if len(c.encode("utf-8")) > 1])
            )
        return ans

    @property
    def _audit_dir_path(self):
        """
        Time-stamped location to save audit data.

        If created, copies the input bibtex file (hard link).
        """
        if self.__audit_dir_path is None:
            self.__audit_dir_path = self.reference_library.debug_dir_path / "imports" / self.timestamp
            # ensure it exists
            self.__audit_dir_path.mkdir(parents=True, exist_ok=True)
            logger.info("Created audit path at %s", str(self.__audit_dir_path))
            # audit the bibtex input file
            p_ = self.__audit_dir_path / self.bibtex_file_path.name
            if p_.exists():
                logger.warning("REALLY WEIRD - audit of input bibtex already exists.")
                p_.unlink()
            p_.hardlink_to(self.bibtex_file_path)
        return self.__audit_dir_path

    # GEMINI CODE for interactive update library
    @staticmethod
    def _normalize_title(s: str) -> str:
        """Simple normalization for title comparison."""
        if not isinstance(s, str):
            return ""
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return " ".join(s.split())

    def _possible_duplicate(
        self, ref_row, ref_row_idx, lib_test: bool = True
    ) -> str | None:
        """
        Heuristic duplicate check: by DOI (if present) and by normalized title.
        Returns a short message if something looks like a duplicate.

        lib_test = test against the library, else self test (dups within the import).
        """
        # basic checks
        doi = str(ref_row.get("doi", "") or "").strip().lower()
        title = ref_row.get("title", "")
        tag = ref_row.get("tag", "")
        title_norm = self._normalize_title(title)

        # Hash Check (The "Guardian" logic)
        if self.incremental and self._add_hashes:
            # Check if this file hash already exists in the library
            # Note: doc_df is the library's document database
            # We need to find the hash of the file currently being imported
            
            # Use self.vfile_df to find the actual path for this tag
            # (ref_row index is matching ported_df index which is 1-based, 
            # but tag is always reliable)
            mask = self.vfile_df.tag == tag
            if mask.any():
                current_path = self.vfile_df[mask].vfile.iloc[0]
                # lookup in doc_df which contains hashes of files in this import
                doc_mask = self.doc_df.path == current_path
                if doc_mask.any():
                    current_hash = self.doc_df[doc_mask].hash.iloc[0]
                    
                    if current_hash:
                        lib_docs = self.reference_library.doc_df
                        if current_hash in lib_docs.hash.values:
                            # Find which tags in the library use this hash
                            match_tags = self.reference_library.ref_doc_df[
                                self.reference_library.ref_doc_df.hash == current_hash
                            ].tag.tolist()
                            if match_tags:
                                return "HASH", 100, 100, title, match_tags[0]

        # what are we checking against?
        if self._existing_title_norm is None or self._dois is None:
            if (
                lib_test
                and self.reference_library != EMPTY_LIBRARY
                and not self.reference_library.ref_df.empty
            ):
                logger.info("Checking duplicates relative to reference library.")
                self._existing_title_norm = self.reference_library.ref_df.title.map(
                    self._normalize_title
                )
                self._dois = (
                    self.reference_library.ref_df.doi.astype(str)
                    .str.lower()
                    .str.strip()
                    if "doi" in self.reference_library.ref_df
                    else []
                )
                self._self_test = False
                # temporary storage - note titles are non-normalized
                self._possible_duplicate_tags = self.reference_library.ref_df.tag
                self._possible_duplicate_titles = self.reference_library.ref_df.title
            else:
                # check against self - new import with no library or lib_test == False
                logger.info(
                    "No reference library...checking duplicates relative to import."
                )
                self._existing_title_norm = self.ref_df.title.map(self._normalize_title)
                self._dois = (
                    self.ref_df.doi.astype(str).str.lower().str.strip()
                    if "doi" in self.ref_df
                    else []
                )
                self._self_test = True
                # temporary storage
                self._possible_duplicate_tags = self.ref_df.tag
                self._possible_duplicate_titles = self.ref_df.title

            # assert len(self._existing_title_norm) == len(self._dois), "WRONG SIZES"

        def create_message(mask, kind):
            tags = self._possible_duplicate_tags.loc[mask].tolist()
            titles = self._possible_duplicate_titles.loc[mask].tolist()
            score_title = 0  # similarity score
            score_tag = 0  # similarity score
            match_tag = ""
            match_title = ""
            for tg, tl in zip(tags, titles):
                #
                # title_similarity = fuzz.ratio(title, tl)
                # tag_similarity = fuzz.ratio(tag, tg)
                # gemini recommends
                title_similarity = SequenceMatcher(None, title, tl).ratio() * 100
                tag_similarity = SequenceMatcher(None, tag, tg).ratio() * 100
                if title_similarity > score_title:
                    score_title = title_similarity
                    score_tag = tag_similarity
                    match_tag = tg
                    match_title = title
            if score_title > 66 and score_tag > 80:
                return kind, score_title, score_tag, match_title, match_tag
            else:
                return

        # DOI check
        if doi:
            mask = self._dois == doi
            # when run against itself (import into empty library), not match yourself!
            if self._self_test:
                mask[ref_row_idx] = False
            if mask.any():
                if (t := sum(mask)) > 1:
                    logger.debug("MULTI TITLE: %s, %s", ref_row.tag, t)
                return create_message(mask, "DOI")
        # Title check
        if title_norm:
            mask = self._existing_title_norm == title_norm
            if self._self_test:
                mask[ref_row_idx] = False
            if mask.any():
                if (t := sum(mask)) > 1:
                    logger.debug("MULTI TITLE: %s, %s", ref_row.tag, t)
                return create_message(mask, "title")

        return None

    @staticmethod
    def _compare(orig: pd.Series, revised: pd.Series) -> bool:
        """
        Returns True if revised.index is a subset of orig.index and
        corresponding values match (treating NaNs as equal).
        """
        if not revised.index.isin(orig.index).all():
            return False
        return revised.equals(orig.loc[revised.index])
