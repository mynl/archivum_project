"""
Manage config file and index database creation and updating.

Equivalent to and based on manager module in file_database.

Querying uses a file-database project-like combo regex-sql (querex) querier.
"""

from functools import partial
import logging 
from pathlib import Path
import re
import subprocess
from types import MethodType

import yaml
import pandas as pd
from pydantic import ValidationError

from . import BASE_DIR, APP_NAME, DEFAULT_CONFIG_FILE
from . trie import Trie
from . querex import querex_work
from . utilities import TagAllocator
from . document import Document
from . config import Configurator
from . library_base import LibraryBase
logger = logging.getLogger(__name__)


class Library(LibraryBase):
    """Library specified by config yaml (archivum-config) file."""

    # base columns used by the app for quick output displays
    base_cols = ['tag', 'type', 'author', 'title', 'year', 'journal', 'file']

    def __init__(self, config_file: Path | None = None, **overrides):
        """
        Load YAML config from file. If None, defaults to DEFAULT_CONFIG_FILE.

        The archivum-config suffix optional and added if missing.
        If not found in current directory, looks in local (eg. for default config).
        """
        self.BASE_DIR = BASE_DIR.resolve()    # helpful externally, keep it all in the library
        logger.debug('config_file = %s', config_file)
        config_file = config_file or DEFAULT_CONFIG_FILE

        # figure config path and load
        self.config_path = Path(config_file)
        if not self.config_path.exists():
            self.config_path = self.BASE_DIR / f'{config_file}.{APP_NAME}-config'
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            base_config = Configurator.model_validate(raw)
        except (ValidationError, OSError) as e:
            raise ValueError(
                f"Failed to load config from {config_file}") from e

        # access through config
        # update and validate; need to merge to avoid repeated args
        # merged = dict(base_config.model_dump(), **overrides)
        merged = base_config.model_dump() | overrides
        self.config = Configurator(**merged)
        self.text_dir_path = self.BASE_DIR / self.config.text_dir_name
        self.text_dir_full_name = str(self.text_dir_path)
        self.reset()

    def reset(self):
        """Reset all cache variables."""
        self._last_query = None
        self._last_unrestricted = 0
        self._last_query_title = ''
        self._last_query_expr = ''
        self._config_df = pd.DataFrame()
        self._doc_df = pd.DataFrame()
        # paths in doc df get mangled - this is the original
        self._doc_read_df = pd.DataFrame()
        self._ref_df = pd.DataFrame()
        self._ref_doc_df = pd.DataFrame()
        # fully blown up docs x refs x authors
        self._database = pd.DataFrame()
        self._trie = None
        self._tag_allocator = None
        self.is_dirty = False
        self.is_empty = False

    def __repr__(self):
        """Create simple string representation."""
        return f'Library({self.config_path.name})'

    @property
    def config_df(self):
        if self._config_df.empty:
            self._config_df = pd.Series(self.config.model_dump()).to_frame('value')
            self._config_df.index.name = 'key'
        return self._config_df

    @property
    def name(self):
        return self.config.name if self.config else "~~no name~~"

    @property
    def doc_df(self):
        """Return the document df, loading if needed."""
        if self._doc_df.empty:
            try:
                self._doc_read_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-doc-feather'))
            except FileNotFoundError:
                return self._doc_df
            pdf_dir = Path(self.config.pdf_dir_name)
            # mangle path names to make more readable
            self._doc_df = self._doc_read_df.copy()
            self._doc_df['tpath'] = [
                str(Path(i).relative_to(pdf_dir).parent)
                for i in self._doc_df.path]
            # set base cols
            base_cols = ['name', 'create', 'size', 'tpath']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='name',
                             recent_field='mod')
            self._doc_df.querex = MethodType(querex, self._doc_df)
        return self._doc_df

    @property
    def ref_df(self):
        """Return the document df, loading if needed."""
        if self._ref_df.empty:
            try:
                self._ref_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-feather'))
            except FileNotFoundError:
                return self._ref_df
            # set base cols
            base_cols = ['tag', 'author', 'title', 'journal']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='author',
                             recent_field='year')
            self._ref_df.querex = MethodType(querex, self._ref_df)
        return self._ref_df

    @property
    def ref_doc_df(self):
        """Return the document df, loading if needed."""
        if self._ref_doc_df.empty:
            try:
                self._ref_doc_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-doc-feather'))
            except FileNotFoundError:
                return self._ref_doc_df
            # set base cols
            base_cols = ['tag', 'path']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='path',
                             recent_field='tag')
            self._ref_doc_df.querex = MethodType(querex, self._ref_doc_df)
        return self._ref_doc_df

    @property
    def database(self):
        """Merged database, with exploded authors."""
        if self._database.empty:
            if self.ref_df.empty:
                return self._database
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
            # set base cols
            base_cols = ['tag', 'author', 'title', 'journal', 'create']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='author',
                             recent_field='mod')
            self._database.querex = MethodType(querex, self._database)
        return self._database

    def update(self, importer):
        """
        Update internal database and save.

        Invalidate all caches to force clean re-load.

        Called by the import routine, after figuring what needs to be added.

        importer is an import_bibtex.Bib2df_Incremental object.
        """
        # extract additions
        ref_add = importer.ref_df
        doc_add = importer.doc_df
        ref_doc_add = importer.ref_doc_df

        logger.info(f'Appending {len(ref_add) = } references')
        logger.info(f'Appending {len(doc_add) = } documents')
        logger.info(f'Appending {len(ref_doc_add) = } ref-doc mappings')

        # Append to existing dataframes.
        ref_out = pd.concat([self.ref_df, ref_add], ignore_index=True)
        doc_out = pd.concat([self._doc_read_df, doc_add], ignore_index=True)
        ref_doc_out = pd.concat([self.ref_doc_df, ref_doc_add], ignore_index=True)

        # make these the reference object
        self._ref_df = ref_out
        self._doc_df = doc_out
        self._ref_doc_df = ref_doc_out

        # save
        self.save()

        # invalidate to force cache refresh
        self.reset()
        logger.info('save library and invalidated cache')

    def save(self):
        """Save config and all dataframes."""
        self._config.save(self.config_path, backup=True)
        self._ref_df.to_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-feather'))
        self._doc_read_df.to_feather(self.config_path.with_suffix(f'.{APP_NAME}-doc-feather'))
        self._ref_doc_df.to_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-doc-feather'))

    def querex(self, expr):
        """Run ``expr`` through the querex on database."""
        self._last_query_expr = expr
        try:
            self._last_query = self.database.querex(expr)
            self._last_unrestricted = getattr(self.database, "qx_unrestricted_len", -1)
        except ValueError:
            return None
        return self._last_query

    def distinct(self, c):
        """Return distinct occurrences of col c."""
        # database is fully exploded so this is OK:
        if self.database.empty: return []
        return sorted(set([i for i in self.database[c] if i != '']))

    @staticmethod
    def get_library_path_list():
        """Get a list of available libraries (no suffix) as list of Paths (see also ``list``)."""
        return list(BASE_DIR.glob(f'*.{APP_NAME}-config'))

    @staticmethod
    def list():
        """List of projects in the default location."""
        # TODO
        return [f.name for f in Library.get_library_path_list()]

    @staticmethod
    def list_deets():
        """Dataframe of all projects in default location."""
        # not sure what the best "way around" is for this...
        df = pd.concat(
            [Library(p).config_df for p in Library.get_library_path_list()],
            axis=1).T.fillna('')
        # df = df[['name', 'description', 'bibtex_file', 'pdf_dir_name', 'text_dir_name', 'extractor', ]]
        df = df.reset_index(drop=True)
        return df

    def to_name_ex(self, name, strict=False):
        """Extend name to longest match using a Trie; in strict mode adds as key if missing."""
        if self._trie is None:
            authors = self.distinct('author')
            self._trie = Trie()
            for a in authors:
                self._trie.insert(a)
        if not self._trie.has_key(name) and strict:
            # print(f'{name} is not a key...adding')
            self._trie.insert(name)
        name_ex = self._trie.longest_unique_completion(name, strict)
        return name_ex

    def next_tag(self, name, year):
        """
        Return the next tag after name, year.

        Remembers incremental tags handed out.
        """
        return self.tag_allocator.get_tag(name, year)

    def reset_tag_allocator(self):
        """You want to remember new tags for each dry run but be
        able to accept them. Hence this is useful."""
        self._tag_allocator = None

    @property
    def tag_allocator(self):
        """Return the loaded key allocator for tag generation."""
        if self._tag_allocator is None:
            # force build of database
            # TODO: should database normalize on editor too??
            if self.database.empty:
                self._tag_allocator = TagAllocator([])
            else:
                d = self.database
                tags = set(d.tag)
                self._tag_allocator = TagAllocator(tags)
        return self._tag_allocator

    def get_new_documents(self, directory, meta, recursive):
        """
        Scan a directory for new PDF files and optionally extract metadata.

        Note ``new`` requires an open library for name completion and
        timezone. You should always be working with an open library
        and they are easy to complete.

        NOT USED??
        """
        if directory == '':
            directory = self.config.watched_dirs[0]
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError('Directory directory does not exist')
        if recursive:
            pdfs = directory.rglob('*.pdf')
        else:
            pdfs = directory.glob('*.pdf')
        pdfs = sorted(pdfs)
        dfs = pd.DataFrame({
            'Document': [Document(p, self) for p in pdfs],
            'file_name': [d.name for d in pdfs],
            'path': pdfs,
            'create': [
                pd.to_datetime(p.stat().st_ctime_ns, unit='ns').tz_localize('UTC').tz_convert(self.config.timezone)
                for p in pdfs]
        }).sort_values('create', ascending=False)
        dfs['n'] = range(1, len(dfs) + 1)
        dfs = dfs.reset_index(drop=True)
        if meta:
            dfs.Document.map(lambda x: x.add_meta_data())
            dfs['meta_author'] = dfs.Document.map(lambda md: md.meta_author)
            dfs['meta_subject'] = dfs.Document.map(lambda md: md.meta_subject)
            dfs['meta_title'] = dfs.Document.map(lambda md: md.meta_title)
            dfs['meta_author_ex'] = dfs.Document.map(lambda md: md.meta_author_ex)
            dfs['meta_crossref'] = dfs.Document.map(lambda md: md.meta_crossref)
        return dfs

    def run_ripgrep(self, pattern, args):
        """Execute and format ripgrep search against library full text extracts."""
        # figure library location and prefix and suffix search terms

        cmd = ["rg",
                    "--json",
                    "--stats",
                    "-C", "1",
                    "-g", '*.md',
                    "--encoding", "utf-8",
                    pattern,
                    *args,
                    self.text_dir_full_name
                ]
        logger.info("will run %s", cmd)
        # execute command
        try:
            proc = subprocess.Popen(cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    encoding='utf-8')
        except FileNotFoundError:
            return "FileNotFoundError", "[red]ripgrep (rg) not found on PATH[/red]"

        if proc.stdout is None:
            return "None", "[red]Failed to read rg output[/red]"

        return 0, proc

    def import_bibtex(
        self,
        bibtex_path: Path,
        imports_dir: Path | None = None,
        pdf_dir: Path | None = None,
        audit_mode: bool = False,
    ):
        """
        Import references and documents from a BibTeX file into this library.

        This delegates to the import_bibtex helper module, which reuses the
        Mendeley porting logic (Bib2df) and updates the underlying feather
        files and in-memory dataframes.
        """
        from . import import_bibtex as import_bibtex_mod

        bibtex_path = Path(bibtex_path)
        if imports_dir is not None:
            imports_dir = Path(imports_dir)
        if pdf_dir is not None:
            pdf_dir = Path(pdf_dir)

        result = import_bibtex_mod.run_import(
            bibtex_path=bibtex_path,
            library=self,
            imports_dir=imports_dir,
            pdf_dir=pdf_dir,
            audit_mode=audit_mode,
        )
        return result

    # def schedule(self, execute=False):
    #     """Set up the task schedule for the project."""
    #     schedule_time = self.config.get('schedule_time', '')
    #     if schedule_time == "":
    #         print('Scheduling not defined in config file. Exiting.')
    #     schedule_frequency = self.schedule_frequency
    #     task_name = f'file-db-task {self.project}'
    #     cmd = [
    #         "schtasks",
    #         "/Create",
    #         "/TN", task_name,
    #         "/TR", f'file-db index -c "{str(self.config_path)}"',
    #         "/SC", schedule_frequency,
    #         "/ST", schedule_time,
    #         "/F"  # force update if exists
    #     ]

    #     if execute:
    #         print('Executing:\n\n', ' '.join(cmd))
    #         subprocess.run(cmd, check=True)
    #     else:
    #         print('Would execute\n\n', ' '.join(cmd))
