"""
Manage config file and index database creation and updating.

Equivalent to and based on manager module in file_database.

Querying uses a file-database project-like combo regex-sql (querex) querier.
"""
import datetime as dt
from importlib.resources import files
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess

import pandas as pd
from IPython.display import display

from querexfuzz.core import Querexfuzz  # type: ignore[import-untyped]

from . import BASE_DIR, LIBRARIES_DIR, DEFAULT_LIBRARY, resolve_path
from .trie import Trie
from .utilities import TagAllocator
from .config import load_configuration
from .library_base import LibraryBase
from .bibtex import dict_to_bibtex
from .hasher import hash_many3 as hash_many
from .document import Document, extract_text_for_paths
from .enhancements import (
    enhance_ref_df,
    Ans,
    path_from_row,
    save_from_row,
    canonical_name_from_row
)

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

        self.config = load_configuration(self.config_path, **overrides)
        self.doc_store_path = resolve_path(self.config.doc_store_lib)
        self.doc_store_path.mkdir(parents=True, exist_ok=True)

        self.text_dir_path = resolve_path(self.config.full_text_lib)
        self.text_dir_path.mkdir(parents=True, exist_ok=True)
        self.text_dir_full_name = str(self.text_dir_path)

        self.debug_dir_path = resolve_path(str(self.config.debug_dir))
        self.debug_dir_path.mkdir(parents=True, exist_ok=True)

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

            doc_dir = self.doc_store_path

            def get_rel_parent(p: Path) -> str:
                if p.is_relative_to(doc_dir):
                    return str(p.relative_to(doc_dir).parent)
                return str(p.parent)  # Fallback: keep absolute parent

            # truncate path names to make more readable
            self._doc_df = self._doc_read_df.copy()
            if not self._doc_df.empty and "path" in self._doc_df.columns:
                self._doc_df["tpath"] = [
                    get_rel_parent(p) for p in map(Path, self._doc_df.path)
                ]
            else:
                self._doc_df["tpath"] = pd.Series(dtype=str)

            # set up querexfuzz
            config_file = (
                files("archivum.configurations") / "querexfuzz-doc-config.yaml"
            )
            qeng = Querexfuzz(config_path=config_file)
            self._doc_df = qeng.attach_to(
                self._doc_df,
                "querex",
            )
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

    def remove_reference(self, tag: str):
        """Remove a reference and its links from the library."""
        if tag not in self.ref_df.tag.values:
            logger.warning("Tag %s not found in ref_df", tag)
        self._ref_df = self.ref_df[self.ref_df.tag != tag]
        self._ref_doc_df = self.ref_doc_df[self.ref_doc_df.tag != tag]
        self.save()
        self.reset()

    def update_reference(self, old_tag: str, new_data: dict):
        """Update or add a reference. Handles tag changes."""
        print('Warning: UNTESTED')

        new_tag = new_data.get("tag")
        if not new_tag:
            raise ValueError("New data must contain a 'tag'")

        # 0. ensure data loaded
        ref_df = self.ref_df
        ref_doc_df = self.ref_doc_df

        # 1. Handle tag change or new tag
        if old_tag != new_tag:
            if new_tag in self.ref_df.tag.values:
                raise ValueError(f"Tag '{new_tag}' already exists in library.")
            if old_tag:
                # Update links in ref_doc_df
                self._ref_doc_df.loc[self._ref_doc_df.tag == old_tag, "tag"] = new_tag

        # 2. Prepare new row
        # Use existing row as base if updating
        if old_tag and old_tag in self.ref_df.tag.values:
            idx = self.ref_df.index[self.ref_df.tag == old_tag][0]
            row = self.ref_df.loc[idx].copy()
            for k, v in new_data.items():
                row[k] = v
            # If tag changed, we'll replace the old one
            self._ref_df = self.ref_df.drop(idx)
        else:
            # Adding new
            row = pd.Series(new_data)

        # 3. Restrict to configured columns, but ENSURE tag and type are kept
        keep_cols = set(self.config.ref_columns)
        keep_cols.add("tag")
        keep_cols.add("type")
        row = row[row.index.isin(keep_cols)]

        # 4. Append and save
        self._ref_df = pd.concat(
            [self.ref_df, row.to_frame().T], ignore_index=True
        )
        self.save()
        self.reset()

    def validate(self, task: str = "sharding", execute: bool = False, new_root: str = None):
        """
        Audit and fix library structure.
        Tasks: 'sharding', 'rebase', 'missing'
        """
        base_path = self.doc_store_path

        report = []

        def path_compare(l_str, r_str):
            """compare two strings as resolved paths."""
            if not l_str or not r_str:
                return False
            if str(l_str).lower() == str(r_str).lower():
                return True
            # physical check
            try:
                return os.path.samefile(l_str, r_str)
            except (OSError, ValueError):
                return False

        if task == "sharding":
            # 1. Join everything to see what we SHOULD have (unexploded authors)
            # Use ref_df directly to avoid author explosion in self.database
            db = (
                self.ref_doc_df.merge(self.ref_df, on="tag", how="inner")
            ).merge(self.doc_df, on="path", how="inner")

            if db.empty:
                return pd.DataFrame()

            for _, row in db.iterrows():
                if pd.isna(row.path) or not row.path:
                    continue

                # Calculate what the path should be
                expected = path_from_row(row, base_path)
                actual = row.path

                if not path_compare(actual, expected):
                    status = "Misplaced"
                    if not os.path.exists(actual):
                        status = "Missing"

                    report.append({
                        "tag": row.tag,
                        "current": actual,
                        "expected": expected,
                        "status": status
                    })

                    if execute and status == "Misplaced":
                        # Perform the "move" (hardlink + update)
                        success = save_from_row(row, base_path)
                        if success == 'ok':
                            # Update metadata
                            self._ref_doc_df.loc[self._ref_doc_df.tag == row.tag, "path"] = expected
                            self._doc_read_df.loc[self._doc_read_df.path == actual, "path"] = expected
                        else:
                            report[-1]["status"] = "Failed"

        elif task == "rebase":
            print('WARNING: not tested, setting execute to False')
            execute = False
            if not new_root:
                raise ValueError("rebase task requires new_root")

            new_root_path = Path(new_root)
            old_root_path = base_path

            for _, row in self.doc_df.iterrows():
                actual = row.path
                actual_p = Path(actual)
                if actual_p.is_relative_to(old_root_path):
                    rel = actual_p.relative_to(old_root_path)
                    expected = str((new_root_path / rel).as_posix())
                    report.append({
                        "current": actual,
                        "expected": expected,
                        "status": "Rebase"
                    })

                    if execute:
                        self._doc_read_df.loc[self._doc_read_df.path == actual, "path"] = expected
                        self._ref_doc_df.loc[self._ref_doc_df.path == actual, "path"] = expected

        elif task == "missing":
            for _, row in self.doc_df.iterrows():
                if not os.path.exists(row.path):
                    report.append({
                        "tag": "N/A",
                        "current": row.path,
                        "expected": "N/A",
                        "status": "Missing"
                    })
                    if execute:
                        # remove from indices
                        self._doc_read_df = self._doc_read_df[self._doc_read_df.path != row.path]
                        self._ref_doc_df = self._ref_doc_df[self._ref_doc_df.path != row.path]

        if execute and report:
            self.save()
            self.reset()

        return pd.DataFrame(report)

    def save(self):
        """Save config and all dataframes with aggressive safety checks."""
        # 1. ENSURE LOADED: Prevent lazy-load wiping by forcing properties to evaluate
        ref_to_save = self.ref_df
        _ = self.ref_doc_df
        _ = self.doc_df
        doc_to_save = self._doc_read_df
        ref_doc_to_save = self._ref_doc_df

        files_to_save = {
            "ref.feather": ref_to_save,
            "doc.feather": doc_to_save,
            "ref-doc.feather": ref_doc_to_save
        }

        # 2. BACKUP & VALIDATE
        backup_dir = self.config_path / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

        for filename, df in files_to_save.items():
            target_path = self.config_path / filename
            if target_path.exists():
                try:
                    # Check existing size
                    disk_df = pd.read_feather(target_path)
                    disk_len = len(disk_df)
                    mem_len = len(df)

                    # CRITICAL SAFETY: Never overwrite a populated file with an empty DF
                    if disk_len > 0 and mem_len == 0:
                        msg = f"CRITICAL: Wipe prevented! Attempted to save empty DF to {filename} (Disk has {disk_len} rows)."
                        logger.error(msg)
                        raise ValueError(msg)

                    # WARNING: Warn if count drops significantly
                    if mem_len < disk_len:
                        logger.warning(f"Row count drop in {filename}: {disk_len} -> {mem_len}")

                    # Create timestamped backup before overwrite
                    shutil.copy2(target_path, backup_dir / f"{target_path.stem}_{timestamp}.feather")
                except Exception as e:
                    if isinstance(e, ValueError) and "CRITICAL" in str(e):
                        raise
                    logger.warning(f"Backup failed for {filename}: {e}")

        # 3. ACTUAL SAVE
        # config.save handles its own backup
        self.config.save(self.config_path, backup=True)

        for filename, df in files_to_save.items():
            df.to_feather(self.config_path / filename)

        # reproduce the bibtex file
        self.write_bibtex()

        # 4. CLEANUP: Keep only last 10 backups
        for stem in ["ref", "doc", "ref-doc"]:
            backups = sorted(backup_dir.glob(f"{stem}_*.feather"))
            if len(backups) > 10:
                for b in backups[:-10]:
                    b.unlink()

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

    @staticmethod
    def rename_library(old_name: str, new_name: str):
        """Rename a library folder and update its internal name."""
        old_path = LIBRARIES_DIR / old_name.replace(" ", "-")
        if not old_path.exists():
            old_path = LIBRARIES_DIR / old_name

        if not old_path.exists():
            raise FileNotFoundError(f"Source library '{old_name}' not found at {old_path}")

        new_path = LIBRARIES_DIR / new_name.replace(" ", "-")
        if new_path.exists():
            raise FileExistsError(f"Destination library '{new_name}' already exists at {new_path}")

        # Perform move
        shutil.move(str(old_path), str(new_path))

        # Update internal name
        lib = Library(new_name)
        new_config = lib.config.model_copy(update={"name": new_name})
        new_config.save(new_path)
        logger.info(f"Library renamed from '{old_name}' to '{new_name}'")

    @staticmethod
    def copy_library(old_name: str, new_name: str):
        """Copy a library folder and update its internal name."""
        old_path = LIBRARIES_DIR / old_name.replace(" ", "-")
        if not old_path.exists():
            old_path = LIBRARIES_DIR / old_name

        if not old_path.exists():
            raise FileNotFoundError(f"Source library '{old_name}' not found at {old_path}")

        new_path = LIBRARIES_DIR / new_name.replace(" ", "-")
        if new_path.exists():
            raise FileExistsError(f"Destination library '{new_name}' already exists at {new_path}")

        # Perform copy
        shutil.copytree(str(old_path), str(new_path))

        # Update internal name
        lib = Library(new_name)
        new_config = lib.config.model_copy(update={"name": new_name})
        new_config.save(new_path)
        logger.info(f"Library copied from '{old_name}' to '{new_name}'")

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
        # Use ref_columns as the whitelist
        allowed_fields = self.config.ref_columns

        for _, row in self.ref_df.iterrows():
            ans.append(dict_to_bibtex(row, allowed_fields=allowed_fields))

        # Remove empty entries if any (dict_to_bibtex returns empty string on failure)
        ans = [i for i in ans if i]
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

    def extract_all_text(self, force: bool = False, workers: int = None, execute: bool = False):
        """
        Extract text for all documents in the library.
        If force=False, only extracts if the text file doesn't exist.
        If execute=False, does nothing but log what would be done.
        """
        if self.doc_df.empty:
            logger.info("Empty library! Cannot extract text.")
            return

        workers = workers or self.config.hash_workers
        
        # We need hashes for naming
        self.update_hashes()

        to_extract = []
        for _, row in self.doc_df.iterrows():
            p = Path(row.path)
            doc = Document(p)
            doc.hash = row.hash
            if force or not doc.text_exists(self.text_dir_path, self.config.extractor):
                to_extract.append(p)

        if not to_extract:
            logger.info("No text extracts to process.")
            return

        if not execute:
            logger.info(f"DRY RUN: Would extract text for {len(to_extract)} files.")
            print(f"DRY RUN: Would extract text for {len(to_extract)} files.")
            return

        logger.info(f"Extracting text for {len(to_extract)} files...")
        
        # Pass hashes for correct naming
        path_to_hash = {Path(row.path): row.hash for _, row in self.doc_df.iterrows()}
        
        results = extract_text_for_paths(
            to_extract,
            self.text_dir_path,
            extractor=self.config.extractor,
            workers=workers,
            hashes=path_to_hash,
        )

        # Handle errors
        failures = [(to_extract[i], err) for i, (ok, err) in enumerate(results) if not ok]
        if failures:
            error_file = self.debug_dir_path / "full-text-errors.md"
            
            with error_file.open("a", encoding="utf-8") as f:
                f.write(f"\n## Full Text Extraction Errors - {dt.datetime.now().isoformat()}\n")
                for p, err in failures:
                    f.write(f"- `{p}`: {err}\n")
            
            logger.warning(f"Logged {len(failures)} extraction errors to {error_file}")
            print(f"Logged {len(failures)} extraction errors to {error_file}")

    def get_text_info(self):
        """
        Get info about the text: number of docs, number with text files etc.,
        split by extension.
        """
        if self.doc_df.empty:
            return pd.DataFrame()

        # Update hashes if needed to ensure we can check text_exists
        self.update_hashes()

        results = []
        for _, row in self.doc_df.iterrows():
            p = Path(row.path)
            doc = Document(p)
            doc.hash = row.hash
            exists = doc.text_exists(self.text_dir_path, self.config.extractor)
            results.append({
                "path": row.path,
                "suffix": p.suffix.lower(),
                "has_text": exists
            })
        
        df = pd.DataFrame(results)
        
        # Summary
        summary = df.groupby("suffix")["has_text"].agg(["count", "sum"]).rename(
            columns={"count": "Total Docs", "sum": "With Text"}
        )
        summary["Missing"] = summary["Total Docs"] - summary["With Text"]
        return summary

    def clean_text_extracts(self, execute: bool = False):
        """
        Find (and delete if execute) text files with no corresponding document in the library.
        """
        if not self.text_dir_path.exists():
            return []

        # 1. Get all expected text paths
        expected_paths = set()
        for _, row in self.doc_df.iterrows():
            doc = Document(Path(row.path))
            doc.hash = row.hash
            expected_paths.add(str(doc.text_path(self.text_dir_path, self.config.extractor).absolute()))

        # 2. Find all actual text files
        actual_files = list(self.text_dir_path.rglob(f"*.{self.config.extractor}.md"))
        
        orphans = []
        for f in actual_files:
            if str(f.absolute()) not in expected_paths:
                orphans.append(f)

        if not execute:
            if orphans:
                print(f"DRY RUN: Found {len(orphans)} orphaned text files.")
            return orphans

        for f in orphans:
            logger.info(f"Deleting orphaned text file: {f}")
            f.unlink()
        
        return orphans

    def audit(self):
        """
        Perform a comprehensive structural audit of the library.
        Returns a dictionary of findings.
        """
        findings = {
            "missing_physical_files": [],
            "orphan_docs": [],
            "missing_docs": [],
            "broken_tag_links": [],
            "broken_path_links": [],
            "orphan_extracts": []
        }

        # 1. Missing Physical Files
        for _, row in self.doc_df.iterrows():
            if not os.path.exists(row.path):
                findings["missing_physical_files"].append(row.path)

        # 2. Orphan Docs (in doc.feather but not in ref-doc.feather)
        findings["orphan_docs"] = self.doc_df[~self.doc_df.path.isin(self.ref_doc_df.path)].path.tolist()

        # 3. Missing Docs (Tags with no linked documents)
        findings["missing_docs"] = self.ref_df[~self.ref_df.tag.isin(self.ref_doc_df.tag)].tag.tolist()

        # 4. Broken Links
        findings["broken_tag_links"] = self.ref_doc_df[~self.ref_doc_df.tag.isin(self.ref_df.tag)].tag.tolist()
        findings["broken_path_links"] = self.ref_doc_df[~self.ref_doc_df.path.isin(self.doc_df.path)].path.tolist()

        # 5. Orphan Text Extracts
        findings["orphan_extracts"] = self.clean_text_extracts(execute=False)

        return findings

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

    def initial_import(self, *, dir_name="", dir_iterable=None, errors_mapper=None, qd=display, update=False, incremental=False):
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
                self.initial_import_bibtex_file(bibtex_file, doc_dir, errors_mapper, qd, update, incremental=incremental)
            else:
                logger.warning('SKIPPING: No unique bibtex found for %s', doc_dir)
                continue

    def initial_import_bibtex_file(self, bibtex_file, doc_dir=None, errors_mapper=None, qd=display, update=True, incremental=False):
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
            incremental=incremental,
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
        dfa = pd.concat(ans)
        dfa.index = [i for i in dfa.key.map(lambda x: 1 if x=='created' else 0).cumsum()]
        dfa.index.name = 'step'
        dfa = dfa.set_index(['audit', 'key'], append=True).unstack(level='key').droplevel(0, 1)
        dfa = dfa.fillna(0)
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
        """Find all document files in provided dir_path."""
        if dir_path is None:
            raise ValueError("dir_path must be provided to find_docs")

        file_formats = self.config.file_formats
        dir_path = Path(dir_path)
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

    def audit_summary(self):
        """Create a dataframe summarizing the imports used to create the library."""
        lib_path = self.config_path
        # 1. Map: Old BibTeX Tag -> Normalized Tag
        audit_files = {}
        # rglob finds all tag-mapping files across all import batches
        for p in (lib_path / "import-audit").rglob("*audit-info.csv"):
            df = pd.read_csv(p, index_col='key', usecols=[1, 2])
            audit_files[p.name] = df
        return pd.concat(audit_files.values(),
                    keys=audit_files.keys()
                    ).unstack(1).droplevel(0, axis=1)

    def make_tag_mapper(self):
        """Make a tag mapping dictionary for library"""
        # Set this to your actual library path
        lib_path = self.config_path
        # 1. Map: Old BibTeX Tag -> Normalized Tag
        import_map = {}
        extra_map = {}
        # rglob finds all tag-mapping files across all import batches
        for p in (lib_path / "import-audit").rglob("*tag-mapping.csv"):
            ln = p.name.split('.')[0]
            if ln in ('library', 'books', 'book-scans'):
                d = import_map
            else:
                d = extra_map
            # print(p.parent.name, "-->", ln)
            df = pd.read_csv(p, index_col=0)
            # 'tag' is what was in your old .bib file
            # 'proposed_tag' is what Archivum initially assigned
            d.update(dict(zip(df['tag'], df['proposed_tag'])))

        # 2. Map: Normalized Tag -> Final Survivor Tag
        # We take the latest enhancement run as the source of truth
        enhance_files = sorted((lib_path / "enhance-audit").rglob("Ans-work_df.csv"))
        enhance_map = {}
        if enhance_files:
             latest_enhance = pd.read_csv(enhance_files[-1])
             # 'tag' here is the normalized one
             # 'source_id' is the survivor after deduplication
             enhance_map = dict(zip(latest_enhance['tag'], latest_enhance['source_id']))

        # 3. Final Chained Dict: Old Tag -> Final Survivor
        final_tag_map = {
             old: enhance_map.get(ported, ported)
                 for old, ported in import_map.items()
        }
        return final_tag_map

    def get_tag_info(self, tag: str) -> pd.DataFrame:
        """
        Collate all information about a tag from ref, doc, and ref-doc.
        Returns a 2-column DataFrame: [Field, Value]
        """
        if tag not in self.ref_df.tag.values:
            return pd.DataFrame()

        # 1. Metadata
        ref_row = self.ref_df[self.ref_df.tag == tag].iloc[0].dropna()
        info = []
        for k, v in ref_row.items():
            if v != "":
                info.append({"Field": k, "Value": str(v)})

        # 2. Files
        links = self.ref_doc_df[self.ref_doc_df.tag == tag]
        if not links.empty:
            docs = links.merge(self.doc_df, on="path", how="left")
            for i, (_, doc) in enumerate(docs.iterrows(), 1):
                pref = " [PREFERRED]" if doc.get("preferred") == 1 else ""
                h = f" (hash: {doc.hash[:12]})" if pd.notna(doc.get("hash")) else ""
                info.append(
                    {
                        "Field": f"Document {i}{pref}",
                        "Value": f"{doc.path}{h}",
                    }
                )
        else:
            info.append({"Field": "Documents", "Value": "[None Linked]"})

        return pd.DataFrame(info)
