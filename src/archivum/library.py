"""
Manage config file and index database creation and updating.

Equivalent to and based on manager module in file_database.

Querying uses a file-database project-like combo regex-sql (querex) querier.
"""
import datetime as dt
from importlib.resources import files
import json
import logging
from pathlib import Path
import subprocess

import pandas as pd
from IPython.display import display

from querexfuzz.core import Querexfuzz  # type: ignore[import-untyped]

from . import BASE_DIR, LIBRARIES_DIR, DEFAULT_LIBRARY
from .trie import Trie
from .utilities import TagAllocator
from .config import load_configuration
from .library_base import LibraryBase
from .bibtex import dict_to_bibtex
from .hasher import hash_many3 as hash_many
from .enhancements import enhance_ref_df, Ans

logger = logging.getLogger(__name__)


class Library(LibraryBase):
    """Library specified by config yaml (archivum-config) file."""

    # base columns used by the app for quick output displays
    base_cols = ["tag", "type", "author", "title", "year", "journal", "file"]

    def __init__(self, library_dir_name: str = "", **overrides):
        """
        Load YAML config from library name. Combines site, library
        and overrides.

        The archivum-config suffix optional and added if missing.
        If not found in current directory, looks in local (eg. for default config).
        """
        library_dir_name = library_dir_name or DEFAULT_LIBRARY
        logger.debug("library_dir_name = %s", library_dir_name)

        # figure config path and load
        self.config_path = LIBRARIES_DIR / library_dir_name
        if not self.config_path.exists():
            # one other idea
            self.config_path = LIBRARIES_DIR / library_dir_name.replace(" ", "-")
        if not self.config_path.exists():
            raise FileNotFoundError('Cannot find library directory.')
        # if not self.config_path.exists():
        #     # one other idea
        #     self.config_path = LIBRARIES_DIR / library_dir_name.replace(" ", "-")
        #     if not self.config_path.exists():
        #         raise FileNotFoundError(
        #             "Library directory does not exist. Create first."
        #         )
        # try:
        #     raw = yaml.safe_load(
        #         (self.config_path / "config.yaml").read_text(encoding="utf-8")
        #     )
        #     base_config = Configurator.model_validate(raw)
        # except FileNotFoundError:
        #     raise
        # except (ValidationError, OSError) as e:
        #     raise ValueError(f"Failed to load config {self.config_path}") from e

        # # access through config
        # # update and validate; need to merge to avoid repeated args
        # # merged = dict(base_config.model_dump(), **overrides)
        # merged = base_config.model_dump() | overrides

        self.config = load_configuration(self.config_path, **overrides)
        self.text_dir_path = BASE_DIR / self.config.text_dir_name
        self.text_dir_full_name = str(self.text_dir_path)
        self.reset()

    def __repr__(self):
        """Create simple string representation."""
        return f"Library({self.config.name})"

    def reset(self):
        """Reset all cache variables."""
        self._last_query = None
        self._last_unrestricted = 0
        self._last_query_title = ""
        self._last_query_expr = ""
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
        self._tag_cache = None
        self._title_cache = None
        self._tag_title_cache = None

    @property
    def name(self):
        return self.config.name if self.config else "~~no name~~"

    @property
    def config_df(self):
        if self._config_df.empty:
            self._config_df = pd.Series(self.config.model_dump()).to_frame("value")
            self._config_df.index.name = "key"
        return self._config_df

    @property
    def doc_df(self):
        """Return the document df, loading if needed."""
        if self._doc_df.empty:
            try:
                self._doc_read_df = pd.read_feather(self.config_path / "doc.feather") #, dtype_backend="pyarrow")
            except FileNotFoundError:
                return self._doc_df
            doc_dir = Path(self.config.doc_dir_name)

            def get_rel_parent(p: Path) -> str:
                if p.is_relative_to(doc_dir):
                    return str(p.relative_to(doc_dir).parent)
                return str(p.parent)  # Fallback: keep absolute parent

            # truncate path names to make more readable
            self._doc_df = self._doc_read_df.copy()
            self._doc_df["tpath"] = [
                get_rel_parent(p) for p in map(Path, self._doc_df.path)
            ]

            # set up querexfuzz
            config_file = (
                files("archivum.configurations") / "querexfuzz-doc-config.yaml"
            )
            qeng = Querexfuzz(config_path=config_file)
            self._doc_df = qeng.attach_to(
                self._doc_df,
                "querex",
            )
            # older version
            # set base cols
            # base_cols = ["name", "create", "size", "tpath"]
            # querex = partial(
            #     querex_work, base_cols=base_cols, bang_field="name", recent_field="mod"
            # )
            # self._doc_df.querex = MethodType(querex, self._doc_df)
        return self._doc_df

    @property
    def ref_df(self):
        """Return the document df, loading if needed."""
        if self._ref_df.empty:
            try:
                self._ref_df = pd.read_feather(self.config_path / "ref.feather") #, dtype_backend="pyarrow")
            except FileNotFoundError:
                return self._ref_df

            config_file = (
                files("archivum.configurations") / "querexfuzz-ref-config.yaml"
            )
            qeng = Querexfuzz(config_path=config_file)
            self._ref_df = qeng.attach_to(
                self._ref_df,
                "querex",
            )

            # set base cols
            # base_cols = ["tag", "author", "title", "journal"]
            # querex = partial(
            #     querex_work,
            #     base_cols=base_cols,
            #     bang_field="author",
            #     recent_field="year",
            # )
            # self._ref_df.querex = MethodType(querex, self._ref_df)
        return self._ref_df

    @property
    def ref_doc_df(self):
        """Return the document df, loading if needed."""
        if self._ref_doc_df.empty:
            try:
                self._ref_doc_df = pd.read_feather(self.config_path / "ref-doc.feather") #, dtype_backend="pyarrow")
            except FileNotFoundError:
                return self._ref_doc_df

            config_file = (
                files("archivum.configurations") / "querexfuzz-ref-doc-config.yaml"
            )
            qeng = Querexfuzz(config_path=config_file)
            self._ref_doc_df = qeng.attach_to(
                self._ref_doc_df,
                "querex",
            )

            # set base cols
            # base_cols = ["tag", "path"]
            # querex = partial(
            #     querex_work, base_cols=base_cols, bang_field="path", recent_field="tag"
            # )
            # self._ref_doc_df.querex = MethodType(querex, self._ref_doc_df)
        return self._ref_doc_df

    @property
    def database(self):
        """Merged database, with exploded authors."""
        if self._database.empty:
            if self.ref_df.empty:
                return self._database
            exploded_authors = self.ref_df.assign(
                author=self.ref_df.author.str.split(" and ")
            ).explode("author", ignore_index=True)
            self._database = (
                self.ref_doc_df.merge(exploded_authors, on="tag", how="right")
            ).merge(self.doc_df, on="path", how="left")
            for c in ["node", "links", "size"]:
                self._database[c] = self._database[c].fillna(0)
            # self._database.fillna("")

            config_file = (
                files("archivum.configurations") / "querexfuzz-database-config.yaml"
            )
            qeng = Querexfuzz(config_path=config_file)
            self._database = qeng.attach_to(
                self._database,
                "querex",
            )

            # # set base cols
            # base_cols = ["tag", "author", "title", "journal", "create"]
            # querex = partial(
            #     querex_work,
            #     base_cols=base_cols,
            #     bang_field="author",
            #     recent_field="mod",
            # )
            # self._database.querex = MethodType(querex, self._database)
        return self._database

    def update(self, importer):
        """
        Update internal database and save.

        Invalidate all caches to force clean re-load.

        Called by the import routine, after figuring what needs to be added.

        importer is an import_bibtex.Bib2df_Incremental object.
        """
        # extract additions
        ref_add = importer.ref_df.copy()
        doc_add = importer.doc_df
        ref_doc_add = importer.ref_doc_df

        # avoid proliferation of spurious columns
        ref_cols = ref_add.columns
        keep_cols = self.config.ref_columns
        keep_cols = [i for i in keep_cols if i in ref_cols]
        ref_add = ref_add[keep_cols]

        len_ref_add = len(ref_add)
        len_doc_add = len(doc_add)
        len_ref_doc_add = len(ref_doc_add)

        logger.info(f"Appending {len(ref_add) = } references")
        logger.info(f"Appending {len(doc_add) = } documents")
        logger.info(f"Appending {len(ref_doc_add) = } ref-doc mappings")

        pre_ref = len(self.ref_df)
        pre_doc = len(self.doc_df)
        pre_ref_doc = len(self.ref_doc_df)

        # Append to existing dataframes.
        ref_out = pd.concat([self.ref_df, ref_add], ignore_index=True)
        doc_out = pd.concat([self._doc_read_df, doc_add], ignore_index=True)
        ref_doc_out = pd.concat([self.ref_doc_df, ref_doc_add], ignore_index=True)

        post_ref = len(ref_out)
        post_doc = len(doc_out)
        post_ref_doc = len(ref_doc_out)

        print(
            f"{pre_ref = } + {len_ref_add = } = {pre_ref+ len_ref_add} vs {post_ref = }"
        )
        print(
            f"{pre_doc = } + {len_doc_add = } = {pre_doc+ len_doc_add} vs {post_doc = }"
        )
        print(
            f"{pre_ref_doc = } + {len_ref_doc_add = } = {pre_ref_doc+ len_ref_doc_add} vs {post_ref_doc = }"
        )

        # make these the reference object
        self._ref_df = ref_out
        self._doc_read_df = doc_out
        self._ref_doc_df = ref_doc_out

        # save
        self.save()

        # invalidate to force cache refresh
        self.reset()
        logger.info("saved library and invalidated cache")

    def save(self):
        """Save config and all dataframes."""
        # config.save handles the
        self.config.save(self.config_path, backup=True)
        self._ref_df.to_feather(self.config_path / "ref.feather")
        self._doc_read_df.to_feather(self.config_path / "doc.feather")
        self._ref_doc_df.to_feather(self.config_path / "ref-doc.feather")
        # reproduce the bibtex file
        self.write_bibtex()

    # def querex(self, expr):
    #     """Run ``expr`` through the querex on database."""
    #     self._last_query_expr = expr
    #     try:
    #         self._last_query = self.database.querex(expr)
    #         self._last_unrestricted = getattr(self.database, "qx_unrestricted_len", -1)
    #     except ValueError:
    #         return None
    #     return self._last_query

    def distinct(self, c):
        """Return distinct occurrences of col c."""
        # database is fully exploded so this is OK:
        if self.database.empty:
            return []
        return sorted(set([i for i in self.database[c] if i != ""]))

    @staticmethod
    def get_library_path_list():
        """Get a list of available libraries (no suffix) as list of Paths (see also ``list``)."""
        return [f for f in LIBRARIES_DIR.glob("*") if f.is_dir()]

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
            [Library(p).config_df for p in Library.get_library_path_list()], axis=1
        ).T.fillna("")
        # df = df[['name', 'description', 'bibtex_file', 'doc_dir_name', 'text_dir_name', 'extractor', ]]
        df = df.reset_index(drop=True)
        return df

    def to_name_ex(self, name, strict=False):
        """Extend name to longest match using a Trie; in strict mode adds as key if missing."""
        if self._trie is None:
            authors = self.distinct("author")
            self._trie = Trie()
            for a in authors:
                self._trie.insert(a)
        if not self._trie.has_key(name) and strict:
            # print(f'{name} is not a key...adding')
            self._trie.insert(name)
        name_ex = self._trie.longest_unique_completion(name, strict)
        return name_ex

    @property
    def all_tags(self):
        if self._tag_cache is None:
            if self.ref_doc_df.empty:
                self._tag_cache = []
            else:
                self._tag_cache = sorted(
                    self.ref_doc_df["tag"].dropna().unique().astype(str).tolist()
                )
        return self._tag_cache

    @property
    def all_titles(self):
        if self._title_cache is None:
            if self.ref_df.empty:
                self._title_cache = []
            else:
                self._title_cache = sorted(
                    self.ref_df["title"].dropna().unique().astype(str).tolist()
                )
        return self._title_cache

    @property
    def all_tag_titles(self):
        if self._tag_title_cache is None:
            if self.ref_df.empty:
                self._tag_title_cache = []
            else:
                tt = [
                    f"{tg}-{ttl}"
                    for tg, ttl in zip(self.ref_df["tag"], self.ref_df["title"])
                ]
                self._tag_title_cache = sorted(tt)
        return self._tag_title_cache

    def next_tag(self, name, year):
        """
        Return the next tag after name, year.

        Remembers incremental tags handed out.
        """
        # TODO Here somewhere, put Casualty Actuarial Society -> CAS etc.
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

    def run_ripgrep(self, pattern, args):
        """Execute and format ripgrep search against library full text extracts."""
        # figure library location and prefix and suffix search terms

        cmd = [
            "rg",
            "--json",
            "--stats",
            "-C",
            "1",
            "-g",
            "*.md",
            "--encoding",
            "utf-8",
            pattern,
            *args,
            self.text_dir_full_name,
        ]
        logger.info("will run %s", cmd)
        # execute command
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            return "FileNotFoundError", "[red]ripgrep (rg) not found on PATH[/red]"

        if proc.stdout is None:
            return "None", "[red]Failed to read rg output[/red]"

        return 0, proc

    def write_bibtex(self):
        """
        Write out bibtex file of the library.

        Lives in library/LIB_NAME/lib-name.bib with a symlink to config location.
        """
        bibtex_path = self.config_path / "bibtex.bib"
        bibtex_path = Path(bibtex_path).absolute()

        # make the text for the bibtex file
        ans = []
        drop_cols = [i for i in ["version", "arc-source"] if i in self.ref_df]
        for r, row in self.ref_df.drop(columns=drop_cols).iterrows():
            ans.append(dict_to_bibtex(row))
        txt = "\n\n".join(ans)

        # backup existing
        if bibtex_path.exists():
            backup = bibtex_path.with_suffix(".bak")
            if backup.exists():
                backup.unlink()
            backup.hardlink_to(bibtex_path)

        # write out
        bibtex_path.write_text(txt, encoding="utf-8")
        logger.info("Wrote %s bibtex entries to %s", len(ans), bibtex_path)

        # create a link to the config location...but remember the version in the
        # library folder is king.
        if self.config.bibtex_file:
            # link there
            p = Path(self.config.bibtex_file).absolute()
            if p.exists() and not p.is_symlink():
                p.unlink()
            if p.exists(follow_symlinks=False):
                if p.readlink().absolute() == bibtex_path:
                    return # link already there
                else:
                    p.unlink()
            p.symlink_to(bibtex_path)

    def update_hashes(self):
        """Update _doc_read_df hashes, save and reset."""
        if self._doc_read_df.empty:
            logger.info("Empty library! Cannot hash.")
            return

        if "hash" not in self._doc_read_df:
            self._doc_read_df["hash"] = ""

        missing = self._doc_read_df.query("hash == '' or hash == 'TBD'")
        if len(missing) == 0:
            logger.info("No missing caches, exiting.")
            return

        logger.info(f"Updating {len(missing)} hashes")
        missing_docs = missing.path.values
        hashes = hash_many(missing_docs, workers=self.config.hash_workers)
        # hashes returns dict path->hash, so lookup on path
        self._doc_read_df.hash = self._doc_read_df.path.map(lambda x: hashes.get(x, ""))
        # save everything
        self.save()
        # invalidate caches
        self.reset()

    def reset_library(self):
        """
        Reset a library back to empty state.

        USE WITH CARE!

        Deletes all data files and the bibtex link if it exists.
        """
        assert self.name.lower() != "uber library", "Sorry, not deleting the uber library."

        for p in self.config_path.rglob('*'):
            if p.suffix != '.yaml' and not p.is_dir():
                p.unlink()
        for audit_path in [
            self.config_path / "import-audit",
            self.config_path / "enhance-audit"]:
            for p in audit_path.glob('*'):
                if p.is_dir():
                    p.rmdir()
        if audit_path.exists():
            audit_path.rmdir()
        bt_link = Path(self.config.bibtex_file)
        if bt_link.is_symlink() and bt_link.exists(follow_symlinks=False):
            bt_link.unlink()
        # clear local caches
        self.reset()

    def initial_import(self, *, dir_name="", dir_iterable=None, errors_mapper=None, qd=display, update=False):
        """
        Iterate import dir_name or iterate over if iterable. Find
        ! bibtex file - error if the bibtex file is not unique.

        E.g. uber library created from

        """
        dir_iterable = [dir_name] if dir_name != '' else dir_iterable

        def find_bibtex(dir_name):
            """Utility: find the (!) bibtex file in a directory."""
            f = Path(dir_name)
            bibs = list(f.glob('*.bib'))
            if len(bibs) == 1:
                return bibs[0]
            else:
                print("ERROR", f.name, bibs)
                return None

        for doc_dir in dir_iterable:
            doc_dir = Path(doc_dir)
            bibtex_file = find_bibtex(doc_dir)
            if bibtex_file is not None:
                # print(bibtex_file, doc_dir)
                self.initial_import_bibtex_file(bibtex_file, doc_dir, errors_mapper, qd, update)
            else:
                logger.warning('SKIPPING: No unique bibtex found for %s', doc_dir)
                continue

    def initial_import_bibtex_file(self, bibtex_file, doc_dir=None, errors_mapper=None, qd=display, update=True):
        """
        Import a single bibtex file into library.

        Use in prod when you know the bibtex will work to recreate from scratch.
        """
        from . import_bibtex import Bib2df_Incremental
        bibtex_file_path = Path(bibtex_file)
        print("-" * 80 + f"\nImporting: {bibtex_file_path}\n" + '-' * 80)
        assert bibtex_file_path.exists()
        if doc_dir is None:
            doc_dir = bibtex_file_path.parent
        else:
            doc_dir = Path(doc_dir)
            assert doc_dir.exists()

        # create importer object
        b = Bib2df_Incremental(
            bibtex_file_path=bibtex_file_path,
            doc_dir=doc_dir,
            reference_library=self,
            errors_mapper=errors_mapper,
            fillna=True,
            qd=qd,
        )
        # import and report
        import_df = b.import_bibtex_file()
        qd(import_df, caption="Import stats")
        # generally too much info
        # qd(b.import_analysis())
        qd(b.stats(), caption="Current library stats")
        if update:
            # actually update
            b.update_library()
            qd(self.stats(), caption="Updated library stats")

    def history(self):
        """The history of how self was built from the audit files."""
        ans = []
        for f in (self.config_path / 'import-audit').glob('*'):
            for f in f.glob('*'):
                if f.name.find('audit-info') > 0:
                    df = pd.read_csv(f, index_col=0)
                    df['audit'] = f.stem.split('.')[0]
                    ans.append(df)
        dfa = pd.concat(ans).set_index('audit', append=True).drop(columns='key').unstack(1).droplevel(0,1)
        dfa.index = df['key'].values
        dfa = dfa.sort_values('created', axis=1)
        dfa = dfa.T
        dfa.index.name = 'Import'
        dfa.raw_entries = dfa.raw_entries.astype(int)
        dfa.ported_entries = dfa.ported_entries.astype(int)
        dfa['cum_entries'] = dfa.ported_entries.cumsum()
        return dfa

    @classmethod
    def list_stats(cls):
        """Combine stats df for all libraries."""
        ans = []
        libs = [d for d in LIBRARIES_DIR.glob('*') if d.is_dir()]
        for nm in libs:
            lib = cls(nm.name)
            ans.append(lib.stats())
        df = pd.concat(ans, axis=1, keys=[d.name for d in libs], names=['library', 'metric']).fillna(0)
        df = df.astype(int)
        return df

    def find_docs(self, dir_path=None):
        """Find all document files per the config or in provided dir_path."""
        file_formats = self.config.file_formats
        dir_path = (Path(self.config.doc_dir_name)
                    if dir_path is None else Path(dir_path))
        docs = list()
        for ff in file_formats:
            docs.extend(f for f in dir_path.rglob(ff) if f.is_file())
        return docs

    def enhance_refs(self, update=False):
        """
        Run the enhancement process on references only, sort out duplicates etc.

        Designed as a one-time run on initial import. Thereafter the import
        process itself guards against duplicates. It only addresses references and
        makes no change to docs. See enhance_docs for the corresponding doc version.
        """
        ans = enhance_ref_df(self)
        timestamp = dt.datetime.now().strftime("%Y-%m-%d_at_%H-%M-%S")
        p = self.config_path / "enhance-audit" / timestamp
        p.mkdir(parents=True, exist_ok=True)
        try:
            self.save_enhance_audit(ans, p, "Ans")
        except Exception as e:
            logger.warning('Error savings enhance audit, %s', e)
        if update:
            if ans.ref_doc_df is None:
                raise ValueError('Not updating with no ref doc df')

            self._ref_df = ans.ans_df
            self._ref_doc_df = ans.ref_doc_df
            self.save()
            self.reset()
        return ans

    def save_enhance_audit(self, obj, base_path, name):
        """Save object as CSV if pandas, else JSON to the enhance-audit folder."""
        if isinstance(obj, Ans):
            for f in obj._fields:
                objobj = getattr(obj, f)
                if objobj is not None:
                    self.save_enhance_audit(objobj, base_path, f'{name}-{f}')
            return

        if isinstance(obj, (pd.DataFrame, pd.Series)):
            path = base_path / f'{name}.csv'
            obj.to_csv(path, encoding="utf-8")
        elif isinstance(obj, nx.Graph):
            path = base_path / f'{name}.json'
            with path.open("w", encoding="utf-8") as f:
                json.dump(nx.readwrite.json_graph.node_link_data(obj), f, indent=4)
        else:
            path = base_path / f'{name}.json'
            with path.open("w", encoding="utf-8") as f:
                try:
                    json.dump(obj, f, indent=4)
                except TypeError:
                    logger.warning('Object of type %s cannot be saved to json', type(obj))
        logger.info(f"Audit: {type(obj).__name__} saved to {path.name}.")

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
