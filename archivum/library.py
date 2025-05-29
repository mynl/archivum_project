"""
Manage config file and index database creation and updating.

Equivalent to and based on manager module in file_database.

Querying uses a file-database project-like combo regex-sql (querex) querier.
"""

from datetime import datetime
from functools import partial
from pathlib import Path
import re
import subprocess
import time
from types import MethodType

import yaml
import pandas as pd

from . import BASE_DIR, APP_NAME
from . trie import Trie
from . querex import querex_work, querex_help as querex_help_work
from . hasher import hash_many
from . utilities import TagAllocator
from . logger_shim import LoggerShim, LogLevel


# logger
logger = LoggerShim(use_click=False, name=__name__)


class Library():
    """Library specified by config yaml (archivum-config) file."""

    # base columns used by the app for quick output displays
    base_cols = ['tag', 'type', 'author', 'title', 'year', 'journal', 'file']

    def __init__(self, config_file, debug=False):
        """
        Load YAML config from file.

        The archivum-config suffix optional and added if missing.
        If not found in current directory, looks in local (eg. for default config).
        """
        self.debug = False
        self.config_path = Path(config_file)
        if not self.config_path.exists():
            self.config_path = BASE_DIR / f'{config_file}.{APP_NAME}-config'
        logger.info('config_path = %s', self.config_path)
        assert self.config_path.exists()
        with self.config_path.open() as f:
            self._config = yaml.safe_load(f)
        self._last_query = None
        self._last_unrestricted = 0
        self._last_query_title = ''
        self._last_query_expr = ''
        self._config_df = pd.DataFrame([])
        self._doc_df = pd.DataFrame([])
        self._ref_df = pd.DataFrame([])
        self._ref_doc_df = pd.DataFrame([])
        # fully blown up docs x refs x authors
        self._database = pd.DataFrame([])
        self._trie = None
        self._tag_allocator = None
        self.is_dirty = False
        self.is_empty = False

    def close(self):
        """Close library."""
        logger.todo('Library.close()')
        pass

    def save(self):
        """Save library if necessary."""
        logger.todo('Library.save()')

    @property
    def doc_df(self):
        """Return the document df, loading if needed."""
        if self._doc_df.empty:

            self._doc_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-doc-feather'))
            pdf_dir = self.pdf_dir
            self._doc_df['tpath'] = [
                str(Path(i).relative_to(pdf_dir).parent)
                for i in self._doc_df.path]
            # set base cols
            base_cols = ['name', 'create', 'size', 'tpath']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='name',
                             recent_field='mod',
                             debug=self.debug)
            self._doc_df.querex = MethodType(querex, self._doc_df)
        return self._doc_df

    @property
    def ref_df(self):
        """Return the document df, loading if needed."""
        if self._ref_df.empty:
            self._ref_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-feather'))
            # set base cols
            base_cols = ['tag', 'type', 'year', 'author', 'title']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='author',
                             recent_field='year',
                             debug=self.debug)
            self._ref_df.querex = MethodType(querex, self._ref_df)
        return self._ref_df

    @property
    def ref_doc_df(self):
        """Return the document df, loading if needed."""
        if self._ref_doc_df.empty:
            self._ref_doc_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-doc-feather'))
            # set base cols
            base_cols = ['tag', 'path']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='path',
                             recent_field='tag',
                             debug=self.debug)
            self._ref_doc_df.querex = MethodType(querex, self._ref_doc_df)
        return self._ref_doc_df

    @property
    def database(self):
        """Merged database, with exploded authors."""
        if self._database.empty:
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
            base_cols = ['tag', 'type', 'year', 'author', 'title', 'create', 'tpath']
            querex = partial(querex_work,
                             base_cols=base_cols,
                             bang_field='author',
                             recent_field='mod',
                             debug=self.debug)
            self._database.querex = MethodType(querex, self._database)
        return self._database

    def save(self):
        """Save dictionary to yaml."""
        backup = self.config_path.with_suffix(f'.{APP_NAME}-config-bak')
        if backup.exists():
            backup.unlink()
        backup.hardlink_to(self.config_path)
        self.config_path.unlink()
        with self.config_path.open("w") as f:
            yaml.safe_dump(self._config, f,
                           sort_keys=False,                # preserve input order
                           default_flow_style=False,       # block structure
                           width=100,
                           indent=2
                           )
        self._ref_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-feather'))
        self._doc_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-doc-feather'))
        self._ref_doc_df = pd.read_feather(self.config_path.with_suffix(f'.{APP_NAME}-ref-doc-feather'))

    def __getattr__(self, name):
        """Provide access to config yaml dictionary."""
        if name in self._config:
            return self._config[name]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}")

    def __getitem__(self, name):
        """Access to values of config dictionary."""
        return self._config[name]

    def __repr__(self):
        """Create simple string representation."""
        return f'Library({self.config_path.name})'

    @property
    def config(self):
        """Return the config yaml dictionary."""
        return self._config

    @property
    def config_df(self):
        if self._config_df.empty:
            self._config_df = pd.Series(self.config).to_frame('value')
            self._config_df.index.name = 'key'
        return self._config_df

    def set_attributes(self, **kwargs):
        """Set new attributes of config yaml dictionary."""
        for k, v in kwargs.items():
            self._config[k] = v

    def querex(self, expr, debug=False):
        """Run ``expr`` through the querier."""
        self._last_query_expr = expr
        try:
            self._last_query = self.database.querex(expr, debug)
            self._last_unrestricted = unrestricted_len
        except ValueError:
            return None
        self._last_query_title = f'<strong>QUERY</strong>: <code>{expr}</code>, showing {len(self._last_query)} of {self._last_unrestricted} results.'
        return self._last_query

    @staticmethod
    def querex_help():
        """Print help for query syntax."""
        return querex_help_work()

    def distinct(self, c):
        """Return distinct occurrences of col c."""
        # database is fully exploded so this is OK:
        return sorted(set([i for i in self.database[c] if i != '']))
        # if c == 'author':
        #     return sorted(
        #         set(author.strip() for s in self.database.author.dropna() for author in s.split(" and "))
        #     )
        # else:
        #     return sorted(set([i for i in self.database[c] if i != '']))

    def no_file(self):
        """Entries with no files listed."""
        return self.df.loc[self.df.file == '', self.base_cols]

    @staticmethod
    def get_library_list():
        """Get a list of available libraries as Path objects."""
        return list(BASE_DIR.glob(f'*.{APP_NAME}-config'))

    @staticmethod
    def list():
        """List projects in the default location."""
        # TODO
        return '\n'.join(f.name for f in Library.get_library_list())

    @staticmethod
    def list_deets():
        """Dataframe of all projects in default location."""
        # not sure what the best "way around" is for this...
        df = pd.concat(
            [Library(p).config_df for p in Library.get_library_list()],
            axis=1).T.fillna('')
        df = df[['name', 'description', 'bibtex_file', 'pdf_dir', 'text_dir_name', 'extractor', 'watched_dirs', ]]
        df = df.reset_index(drop=False)
        logger.info(str(df))
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

    def distinct_values_by_field(self):
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

    def distinct_value_counts(self, field):
        """Return the top 20 distinct value counts for field."""
        return (None
                if field not in self.database else
                    self.database[field]
                        .value_counts()
                        .to_frame('count')
                        .sort_values('count', ascending=False)
                        .head(20)
                )

    def next_tag(self, name, year):
        """Return the next tag RELATIVE TO THE CURRENT DATA after name, year."""
        try:
            base_tag = f'{name}{year}'
            m = [i for i in self.distinct('tag') if re.search(f'^{base_tag}', i)]
            if m:
                s = m[-1]
                if s[-1].isdigit():
                    # haven't gotten to letters yet
                    return s + 'a'
                s = s[:-1] + chr(ord(s[-1]) + 1)
                return s
            else:
                # nothing close
                return base_tag
        except IndexError as e:
            logger.error('ERROR in next tag ', e)

    @property
    def tag_allocator(self):
        """Return the loaded key allocator for tag generation."""
        if self._tag_allocator is None:
            # force build of database
            # TODO: should database normalize on editor too??
            d = self.database
            names = set(d.author)
            self._tag_allocator = TagAllocator(names)
        return self._tag_allocator

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

    # def duplicates(self, keep=False) -> pd.DataFrame:
    #     """
    #     Return rows that point share the same hash (i.e., duplicate content).

    #     keep = 'first', 'last', False: keep first, last or all duplicates
    #     """
    #     df = self.database
    #     return df[df.duplicated("hash", keep=keep)].sort_values("hash")
