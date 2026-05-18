"""
Manage config file and index database creation and updating.

Equivalent to and based on manager module in file_database.

Querying uses a file-database project-like combo regex-sql (querex) querier.
"""
from functools import cache
import datetime as dt
from importlib.resources import files
import json
import logging
import os
import re
import time
from pathlib import Path
import shutil
import subprocess
from typing import Union, List, Dict, Optional

import pandas as pd
from IPython.display import display
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from querexfuzz.core import Querexfuzz  # type: ignore[import-untyped]

from . import BASE_DIR, LIBRARIES_DIR, DEFAULT_LIBRARY, resolve_path
from .trie import Trie
from .utilities import TagAllocator
from .config import load_configuration
from .library_base import LibraryBase
from .bibtex import dict_to_bibtex, rows_to_bibtex
from .hasher import hash_many3 as hash_many
from .document import Document, extract_text_for_paths
from .enhancements import (
    enhance_ref_df,
    Ans,
    path_from_row,
    save_from_row,
    canonical_name_from_row,
    title_from_path
)

logger = logging.getLogger(__name__)


class LibraryImportBlocked(ValueError):
    """Raised when an import analysis says the web ingest should not proceed."""


class Library(LibraryBase):
    """Library specified by config yaml (archivum-config) file."""

    # base columns used by the app for quick output displays
    base_cols = ["tag", "type", "author", "title", "year", "journal"]

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

        self.exports_dir_path = self.config_path / "exports"
        self.exports_dir_path.mkdir(parents=True, exist_ok=True)

        # State for auto-reload
        self.needs_reload = False
        self._ignore_until = 0.0
        self._last_mtimes: Dict[str, float] = {}
        self._observer: Optional[Observer] = None

        # Initialize mtimes for core files
        for f in ["ref.feather", "doc.feather", "ref-doc.feather", "read.feather"]:
            p = self.config_path / f
            if p.exists():
                self._last_mtimes[f] = p.stat().st_mtime
            else:
                self._last_mtimes[f] = 0.0

        self.reset()

    class LibraryChangeHandler(FileSystemEventHandler):
        def __init__(self, library):
            self.library = library
            self.core_files = {"ref.feather", "doc.feather", "ref-doc.feather"}

        def on_modified(self, event):
            if event.is_directory:
                return

            filename = os.path.basename(event.src_path)
            if filename not in self.core_files:
                return

            # Loop prevention
            if time.time() < self.library._ignore_until:
                return

            # State validation
            try:
                current_mtime = os.path.getmtime(event.src_path)
                last_mtime = self.library._last_mtimes.get(filename, 0.0)

                # Dropbox/Sync Settling: mtime might change slightly without data change,
                # but usually we look for a significant forward move.
                if current_mtime > last_mtime + 0.1: # 100ms tolerance
                    logger.info(f"External change detected in {filename}")
                    self.library.needs_reload = True
                    # We don't update last_mtimes here; reset() will do that.
            except OSError:
                pass

    def start_watcher(self):
        """Start the background filesystem watcher."""
        if self._observer:
            return

        self._observer = Observer()
        handler = self.LibraryChangeHandler(self)
        self._observer.schedule(handler, str(self.config_path), recursive=False)
        self._observer.start()
        logger.debug(f"Started watcher for {self.config_path}")

    def stop_watcher(self):
        """Stop the background filesystem watcher."""
        if self._observer:
            self._observer.stop()
            # We don't necessarily want to join here if it's called during a fast swap,
            # but for cleanup it's good.
            # self._observer.join()
            self._observer = None
            logger.debug(f"Stopped watcher for {self.config_path}")

    def _cleanup_exports(self, days: int = 7):
        """Remove export files older than specified days."""
        try:
            cutoff = time.time() - (days * 86400)
            for p in self.exports_dir_path.iterdir():
                if p.is_file() and p.stat().st_mtime < cutoff:
                    try:
                        p.unlink()
                        logger.debug(f"Deleted old export: {p.name}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {p.name}: {e}")
        except Exception as e:
            logger.error(f"Error during exports cleanup: {e}")

    def __repr__(self):
        """Create simple string representation."""
        return f"Library({self.config.name})"

    def abspath(self, p: Union[str, Path]) -> Path:
        """Resolve a library-relative path to an absolute path with caching."""
        if not p or pd.isna(p):
            return Path()

        # Use a helper to make it cacheable (Path objects are hashable)
        return self._resolve_cached(str(p))

    @cache
    def _resolve_cached(self, p_str: str) -> Path:
        p = Path(p_str)
        if p.is_absolute() or p.anchor in ('\\', '/'):
            return p
        return self.doc_store_path / p

    def textpath(self, p: str) -> Path:
        """Return full text path from doc_df path. Does not check existence."""
        return (self.text_dir_path / p).resolve().with_suffix(f".{self.config.extractor}.md")

    def open_document(self, path: Union[str, Path]):
        """Try to open document at path (rel or abs)."""
        p = self.abspath(path)

        if not p.exists():
            logger.info("file %s not found (at %s)", p.name, p)
            return

        try:
            viewer = self.config.pdf_viewer_command
            if viewer and Path(viewer).exists():
                # Use specified viewer
                subprocess.run([viewer, str(p)], check=False)
            else:
                # Fallback to system default (Windows focus)
                os.startfile(p)
        except Exception as e:
            logger.error("Error while opening %s: %s", p, e)

    def link_document(self, tag: str, file_hash: str, version: int = 0):
        """Manually link a tag to a specific (hash, version)."""
        # 1. Validation
        if tag not in self.ref_df.tag.values:
            raise ValueError(f"Tag '{tag}' not found in references.")

        doc_match = self._doc_df[(self._doc_df.hash == file_hash) & (self._doc_df.version == version)]
        if doc_match.empty:
            raise ValueError(f"Document with hash {file_hash[:12]} and version {version} not found.")

        # 2. Check for existing link
        existing = self.ref_doc_df[(self.ref_doc_df.tag == tag) &
                                   (self.ref_doc_df.hash == file_hash) &
                                   (self.ref_doc_df.version == version)]
        if not existing.empty:
            logger.info("Link already exists.")
            return False

        # 3. Create link
        new_link = pd.DataFrame([{"tag": tag, "hash": file_hash, "version": version}])
        self._ref_doc_df = pd.concat([self.ref_doc_df, new_link], ignore_index=True)

        self.save()
        return True

    def reset(self):
        """Reset all cache variables."""
        self._last_query = None
        self._last_unrestricted = 0
        self._last_query_title = ""
        self._last_query_expr = ""
        self._config_df = pd.DataFrame()
        self._doc_df = pd.DataFrame()
        self._ref_df = pd.DataFrame()
        self._read_df = pd.DataFrame()
        self._ref_doc_df = pd.DataFrame()
        # fully blown up docs x refs x authors
        self._full_df = pd.DataFrame()

        # Update mtimes so we don't immediately trigger another reload
        for f in ["ref.feather", "doc.feather", "ref-doc.feather", "read.feather"]:
            p = self.config_path / f
            if p.exists():
                self._last_mtimes[f] = p.stat().st_mtime

        self.needs_reload = False
        self._database = pd.DataFrame()
        self._trie = None
        self._tag_allocator = None
        self.is_dirty = False
        self.is_empty = False
        self._tag_cache = None
        self._title_cache = None
        self._tag_title_cache = None

    def get_status_info(self) -> Dict:
        """Return a dictionary containing status information for the library."""
        import datetime
        
        core_files = ["ref.feather", "doc.feather", "ref-doc.feather", "read.feather"]
        file_status = []
        
        for f in core_files:
            p = self.config_path / f
            try:
                disk_mtime = p.stat().st_mtime if p.exists() else 0.0
            except OSError:
                disk_mtime = 0.0
            last_mtime = self._last_mtimes.get(f, 0.0)
            
            # Format to precision of 1 second for display
            disk_dt = datetime.datetime.fromtimestamp(disk_mtime) if disk_mtime else None
            last_dt = datetime.datetime.fromtimestamp(last_mtime) if last_mtime else None
            
            status = "MATCH"
            if not disk_mtime or not last_mtime:
                status = "MISSING"
            elif abs(disk_mtime - last_mtime) > 0.1:
                status = "OUT OF SYNC"

            file_status.append({
                "File": f,
                "Disk MTime": disk_dt.strftime('%Y-%m-%d %H:%M:%S') if disk_dt else "N/A",
                "Last Sync": last_dt.strftime('%Y-%m-%d %H:%M:%S') if last_dt else "N/A",
                "Status": status
            })
            
        return {
            "name": self.name,
            "path": str(self.config_path),
            "needs_reload": self.needs_reload,
            "watcher_active": self._observer is not None,
            "is_empty": self.is_empty,
            "files": file_status
        }

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
                self._doc_df = pd.read_feather(self.config_path / "doc.feather")
            except FileNotFoundError:
                return self._doc_df

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
                self._ref_doc_df = pd.read_feather(self.config_path / "ref-doc.feather")
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
    def read_df(self):
        """Return the read history df, loading if needed."""
        if self._read_df.empty:
            p = self.config_path / "read.feather"
            if p.exists():
                try:
                    self._read_df = pd.read_feather(p)
                except Exception as e:
                    logger.error(f"Error loading read.feather: {e}")
                    self._read_df = pd.DataFrame(columns=['hash', 'last_read', 'read_count', 'last_caller'])
            else:
                self._read_df = pd.DataFrame(columns=['hash', 'last_read', 'read_count', 'last_caller'])
        return self._read_df

    def record_read(self, file_hash: str, caller: str = ""):
        """Record a read event for a specific file hash."""
        if not file_hash:
            return

        df = self.read_df
        now = pd.Timestamp.now()
        
        if file_hash in df['hash'].values:
            idx = df[df['hash'] == file_hash].index[0]
            df.at[idx, 'last_read'] = now
            df.at[idx, 'read_count'] = int(df.at[idx, 'read_count']) + 1
            df.at[idx, 'last_caller'] = caller
        else:
            new_row = pd.DataFrame([{
                'hash': file_hash,
                'last_read': now,
                'read_count': 1,
                'last_caller': caller
            }])
            self._read_df = pd.concat([df, new_row], ignore_index=True)

        # Trigger internal reset for database merge next time
        self._database = pd.DataFrame()
        self.save()

    @property
    def database(self):
        """Merged database, with exploded authors and read history."""
        if self._database.empty:
            if self.ref_df.empty:
                return self._database

            # Use hash and version for joining
            merged = (
                self.ref_doc_df.merge(self.ref_df, on="tag", how="right")
            ).merge(self.doc_df, on=["hash", "version"], how="left")
            
            # Join with read history
            if not self.read_df.empty:
                merged = merged.merge(self.read_df, on="hash", how="left")
                if 'read_count' in merged.columns:
                    merged['read_count'] = merged['read_count'].fillna(0).astype(int)

            for c in ["node", "links", "size"]:
                if c in merged.columns:
                    merged[c] = merged[c].fillna(0)
            
            # Note: We don't truncate hash here because querex might need it for filtering,
            # and truncating before merging or using it in query logic is risky.
            # But the display code usually handles the truncation.
            
            self._database = merged
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

        # ensure loaded
        _ = self.doc_df
        _ = self.ref_df
        _ = self.ref_doc_df

        # Append and Deduplicate
        ref_out = pd.concat([self._ref_df, ref_add], ignore_index=True).drop_duplicates(subset=['tag'], keep='last')
        doc_out = pd.concat([self._doc_df, doc_add], ignore_index=True).drop_duplicates(subset=['hash', 'version'], keep='last')
        ref_doc_out = pd.concat([self._ref_doc_df, ref_doc_add], ignore_index=True).drop_duplicates(subset=['tag', 'hash', 'version'], keep='last')

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
        self._doc_df = doc_out
        self._ref_doc_df = ref_doc_out

        # save
        self.save()

        logger.info("saved library and invalidated cache")

    def remove_reference(self, tag: str):
        """Remove a reference and its links from the library."""
        if tag not in self.ref_df.tag.values:
            logger.warning("Tag %s not found in ref_df", tag)
        self._ref_df = self.ref_df[self.ref_df.tag != tag]
        self._ref_doc_df = self.ref_doc_df[self.ref_doc_df.tag != tag]
        self.save()

    def update_reference(self, old_tag: str, new_data: dict):
        """Update or add a reference. Handles tag changes."""
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

    def validate(self, task: str = "sharding", execute: bool = False):
        """
        Audit and fix library structure.
        Tasks: 'sharding', 'orphans', 'missing'
        """
        # ensure data is loaded
        _ = self.doc_df
        _ = self.ref_df
        _ = self.ref_doc_df

        base_path = self.doc_store_path

        report = []

        def path_compare(l_str, r_str):
            """Compare two strings as resolved paths, handling drive letters and normalization."""
            if not l_str or not r_str:
                return "MISSING"

            # Resolve both to absolute paths for robust comparison
            abs_l = self.abspath(l_str)
            abs_r = self.abspath(r_str)

            # Lexical check on absolute paths
            l_norm = str(abs_l).lower().replace('\\', '/')
            r_norm = str(abs_r).lower().replace('\\', '/')

            if l_norm == r_norm:
                return "MATCH"

            # Physical check for aliasing (same file, different path/drive mapping)
            try:
                if os.path.samefile(abs_l, abs_r):
                    return "ALIASED"
            except (OSError, ValueError, FileNotFoundError):
                pass

            if not abs_l.exists():
                return "MISSING"

            return "MISPLACED"

        if task == "sharding":
            # 1. Join everything to see what we SHOULD have (unexploded authors)
            # Use hash and version for joining
            db = (
                self.ref_doc_df.merge(self.ref_df, on="tag", how="inner")
            ).merge(self.doc_df, on=["hash", "version"], how="inner")

            if db.empty:
                return pd.DataFrame()

            for _, row in db.iterrows():
                if pd.isna(row.path) or not row.path:
                    continue

                # Calculate what the path should be
                expected = path_from_row(row, base_path)
                actual = row.path

                compare_status = path_compare(actual, expected)

                # If actual matches expected, we are good.
                # If not, we check if it's aliased or misplaced.

                if compare_status != "MATCH":
                    status = "Misplaced"
                    if compare_status == "MISSING":
                        status = "Missing"
                    elif compare_status == "ALIASED":
                        status = "Aliased"

                    report.append({
                        "tag": row.tag,
                        "current": actual,
                        "expected": expected,
                        "status": status
                    })

                    if execute:
                        # Ensure we store the relative path in the DB
                        rel_expected = str(Path(expected).relative_to(base_path).as_posix())
                        if status == "Aliased":
                            # Update path in doc_df
                            self._doc_df.loc[(self._doc_df.hash == row.hash) & (self._doc_df.version == row.version), "path"] = rel_expected
                        elif status == "Misplaced":
                            # Perform the "move" (hardlink + update)
                            success = save_from_row(row, base_path)
                            if success == 'ok':
                                self._doc_df.loc[(self._doc_df.hash == row.hash) & (self._doc_df.version == row.version), "path"] = rel_expected
                            else:
                                report[-1]["status"] = "Failed"

        elif task == "orphans":
            id_cols = ['hash', 'version']
            doc_ids = self._doc_df[id_cols].drop_duplicates()
            ref_doc_ids = self.ref_doc_df[id_cols].drop_duplicates()

            # Find (hash, version) pairs in doc_df that are NOT in ref_doc_df
            orphan_ids = doc_ids.merge(ref_doc_ids, on=id_cols, how='left', indicator=True)
            orphan_ids = orphan_ids[orphan_ids._merge == 'left_only'].drop(columns=['_merge'])

            # Get the full orphan records
            orphans = orphan_ids.merge(self._doc_df, on=id_cols, how='inner').copy()
            
            if orphans.empty:
                return pd.DataFrame()

            # Prepare orphan metadata for naming. 
            # We try to extract info from the existing filename if it looks sharded.
            def get_orphan_meta(row):
                stem = Path(row.path).stem
                parts = stem.split('_')
                if len(parts) >= 4 and len(parts[0]) >= 8 and parts[1].isdigit():
                    return pd.Series({
                        'author': parts[2],
                        'year': parts[1],
                        'title': parts[3]
                    })
                return pd.Series({
                    'author': 'Unknown',
                    'year': '9999',
                    'title': title_from_path(row.path)
                })

            orphans[['author', 'year', 'title']] = orphans.apply(get_orphan_meta, axis=1)
            orphans['tag'] = 'ORPHAN'

            for _, row in orphans.iterrows():
                expected = path_from_row(row, base_path)
                actual = row.path
                compare_status = path_compare(actual, expected)

                if compare_status != "MATCH":
                    status = "Misplaced"
                    if compare_status == "MISSING":
                        status = "Missing"
                    elif compare_status == "ALIASED":
                        status = "Aliased"

                    report.append({
                        "tag": "ORPHAN",
                        "current": actual,
                        "expected": expected,
                        "status": status
                    })

                    if execute:
                        rel_expected = str(Path(expected).relative_to(base_path).as_posix())
                        if status == "Aliased":
                            self._doc_df.loc[(self._doc_df.hash == row.hash) & (self._doc_df.version == row.version), "path"] = rel_expected
                        elif status == "Misplaced":
                            success = save_from_row(row, base_path)
                            if success == 'ok':
                                self._doc_df.loc[(self._doc_df.hash == row.hash) & (self._doc_df.version == row.version), "path"] = rel_expected
                            else:
                                report[-1]["status"] = "Failed"

        elif task == "missing":
            for _, row in self._doc_df.iterrows():
                if not self.abspath(row.path).exists():
                    report.append({
                        "tag": "N/A",
                        "current": row.path,
                        "expected": "N/A",
                        "status": "Missing"
                    })
                    if execute:
                        # remove from indices
                        self._doc_df = self._doc_df[self._doc_df.path != row.path]
                        self._ref_doc_df = self._ref_doc_df[self._ref_doc_df.path != row.path]

        if execute and report:
            self.save()

        return pd.DataFrame(report)

    def save(self):
        """Save config and all dataframes with aggressive safety checks."""
        # 1. ENSURE LOADED: Prevent lazy-load wiping by forcing properties to evaluate
        ref_to_save = self.ref_df
        ref_doc_df = self.ref_doc_df
        doc_df = self.doc_df

        # 1b. RELATIVIZE PATHS for portability
        doc_dir = self.doc_store_path
        
        def relativize(p):
            pp = Path(p)
            try:
                if pp.is_relative_to(doc_dir):
                    return str(pp.relative_to(doc_dir).as_posix())
            except ValueError:
                pass
            return p

        doc_to_save = doc_df.copy()
        if not doc_to_save.empty and "path" in doc_to_save.columns:
            doc_to_save['path'] = doc_to_save['path'].apply(relativize)
            # drop transient columns
            for col in ['tpath', 'querex']:
                if col in doc_to_save.columns:
                    doc_to_save = doc_to_save.drop(columns=[col])

        ref_doc_to_save = ref_doc_df.copy()
        if not ref_doc_to_save.empty and "path" in ref_doc_to_save.columns:
            ref_doc_to_save['path'] = ref_doc_to_save['path'].apply(relativize)
            if 'querex' in ref_doc_to_save.columns:
                ref_doc_to_save = ref_doc_to_save.drop(columns=['querex'])

        files_to_save = {
            "ref.feather": ref_to_save,
            "doc.feather": doc_to_save,
            "ref-doc.feather": ref_doc_to_save,
            "read.feather": self.read_df
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
        # Loop prevention: set a small window where we ignore filesystem events
        self._ignore_until = time.time() + 2.0

        # config.save handles its own backup
        self.config.save(self.config_path, backup=True)

        for filename, df in files_to_save.items():
            df.to_feather(self.config_path / filename)

        # refresh everything (and update last_mtimes)
        self.reset()

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
        # Execute ripgrep from within the text directory to get relative paths
        # This avoids Windows drive letter colons in the output, making parsing easier.
        cmd = [
            "rg",
            "--line-buffered",
            "--stats",
            "-C",
            "1",
            "--encoding",
            "utf-8",
            *args,
            pattern,
            ".",
        ]
        logger.info("will run %s in %s", cmd, self.text_dir_full_name)
        # execute command
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=self.text_dir_full_name
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
        # Use ref_columns as the whitelist
        allowed_fields = self.config.ref_columns
        txt = rows_to_bibtex(self.ref_df, allowed_fields=allowed_fields)
        entry_count = txt.count("\n@") + (1 if txt else 0)

        # backup existing
        if bibtex_path.exists():
            backup = bibtex_path.with_suffix(".bak")
            if backup.exists():
                backup.unlink()
            backup.hardlink_to(bibtex_path)

        # write out
        bibtex_path.write_text(txt, encoding="utf-8")
        logger.info("Wrote %s bibtex entries to %s", entry_count, bibtex_path)

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
        """Update hashes, save and reset."""
        # ensure loaded
        doc_df = self.doc_df
        if doc_df.empty:
            logger.info("Empty library! Cannot hash.")
            return

        if "hash" not in self._doc_df:
            self._doc_df["hash"] = ""

        missing = self._doc_df.query("hash == '' or hash == 'TBD'")
        if len(missing) == 0:
            logger.info("No missing caches, exiting.")
            return

        logger.info(f"Updating {len(missing)} hashes")
        missing_docs = missing.path.values
        hashes = hash_many(missing_docs, workers=self.config.hash_workers)
        # hashes returns dict path->hash, so lookup on path
        self._doc_df.hash = self._doc_df.path.map(lambda x: hashes.get(x, ""))
        # save everything
        self.save()

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
        skipped_non_pdf = 0
        for _, row in self.doc_df.iterrows():
            p = self.abspath(row.path)
            if p.suffix.lower() != ".pdf":
                skipped_non_pdf += 1
                continue
            doc = Document(p)
            doc.hash = row.hash
            if force or not doc.text_exists(self.text_dir_path, self.config.extractor):
                to_extract.append(p)

        if skipped_non_pdf > 0:
            logger.info(f"Skipped {skipped_non_pdf} non-PDF files (only PDFs are supported).")

        if not to_extract:
            logger.info("No text extracts to process.")
            return

        if not execute:
            logger.info(f"DRY RUN: Would extract text for {len(to_extract)} files.")
            print(f"DRY RUN: Would extract text for {len(to_extract)} files.")
            return

        logger.info(f"Extracting text for {len(to_extract)} files...")
        
        # Pass hashes for correct naming
        path_to_hash = {self.abspath(row.path): row.hash for _, row in self.doc_df.iterrows()}
        
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
            p = self.abspath(row.path)
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
            doc = Document(self.abspath(row.path))
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

        These three should all be empty::
          missing_physical_files: check all files in doc_df actually exist.
          broken_tag_links: tag in ref_doc but no actual ref.
          broken_id_links: (hash, version) in ref_doc but no actual doc.

        These may be longer:
          refs_missing_doc: a ref withand no doc. Can't locate an doc (afile). Expected.
          docs_missing_ref: docs in doc_df with no reference; eg old versions of papers

        orphan_extracts: an actual extract exists that is not expected based on doc_df.
        """
        findings = {
            "missing_physical_files": [],
            "docs_missing_ref": [],
            "refs_missing_doc": [],
            "broken_tag_links": [],
            "broken_id_links": [],
            "orphan_extracts": []
        }

        # 1. Missing Physical Files
        for _, row in self.doc_df.iterrows():
            if not self.abspath(row.path).exists():
                findings["missing_physical_files"].append(row.path)

        # 2. Orphan Docs (in doc.feather but not in ref-doc.feather)
        # Identify by (hash, version)
        id_cols = ['hash', 'version']
        doc_ids = self.doc_df[id_cols].drop_duplicates()
        ref_doc_ids = self.ref_doc_df[id_cols].drop_duplicates()

        # Merge to find orphans
        orphans = doc_ids.merge(ref_doc_ids, on=id_cols, how='left', indicator=True)
        orphans = orphans[orphans._merge == 'left_only'].drop(columns=['_merge'])

        # Map back to paths for reporting
        orphan_paths = orphans.merge(self.doc_df, on=id_cols, how='inner').path.tolist()
        findings["docs_missing_ref"] = orphan_paths

        # 3. Missing Docs (Tags with no linked documents)
        findings["refs_missing_doc"] = self.ref_df[~self.ref_df.tag.isin(self.ref_doc_df.tag)].tag.tolist()

        # 4. Broken Links
        # Broken Tags (Tag in ref-doc not in ref)
        findings["broken_tag_links"] = self.ref_doc_df[~self.ref_doc_df.tag.isin(self.ref_df.tag)].tag.tolist()

        # Broken IDs (Identity in ref-doc not in doc)
        broken_ids = self.ref_doc_df.merge(self.doc_df[id_cols], on=id_cols, how='left', indicator=True)
        findings["broken_id_links"] = broken_ids[broken_ids._merge == 'left_only'].tag.tolist()

        # 5. Orphan Text Extracts
        findings["orphan_extracts"] = self.clean_text_extracts(execute=False)

        # 6. Metadata Quality
        if not self.ref_df.empty:
            findings["metadata_quality"] = {
                "total": len(self.ref_df),
                "missing_doi": int(self.ref_df.doi.isna().sum() + (self.ref_df.doi == "").sum()),
                "missing_year": int(self.ref_df.year.isna().sum() + (self.ref_df.year == "").sum()),
            }
            # Journal/Booktitle source check
            sources = self.ref_df.get('journal', pd.Series(dtype=str)).fillna("") + \
                      self.ref_df.get('booktitle', pd.Series(dtype=str)).fillna("")
            findings["metadata_quality"]["missing_source"] = int((sources == "").sum())
        else:
            findings["metadata_quality"] = {"total": 0, "missing_doi": 0, "missing_year": 0, "missing_source": 0}

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

    def import_staged_document(
        self,
        bibtex_text: str,
        staged_document_path: Union[str, Path],
        *,
        known_hash: str | None = None,
        source_label: str = "web-ingest",
        extract_text: bool = True,
    ):
        """
        Import one staged document through the same incremental BibTeX path as the CLI.

        The edited BibTeX is the metadata source. This helper only injects the
        staged document as a Mendeley-style ``file`` field so Bib2df_Incremental
        can perform its normal author, tag, duplicate, sharding, and audit work.
        """
        importer = self._make_staged_document_importer(
            bibtex_text,
            staged_document_path,
            known_hash=known_hash,
            source_label=source_label,
            write_audit=True,
        )

        analysis = importer.import_analysis()
        self._raise_for_blocked_import(analysis, fallback_tag=self._first_bibtex_tag(bibtex_text))

        if importer.ported_df.empty:
            raise LibraryImportBlocked("Import did not produce any new entries.")

        importer.update_library(save=True)

        if extract_text:
            new_paths = [
                self.abspath(p)
                for p in importer.doc_df.path.values
                if str(p).lower().endswith(".pdf")
            ]
            if new_paths:
                path_to_hash = {
                    self.abspath(row.path): row.hash
                    for _, row in importer.doc_df.iterrows()
                }
                extract_text_for_paths(
                    new_paths,
                    self.text_dir_path,
                    extractor=self.config.extractor,
                    workers=self.config.hash_workers,
                    hashes=path_to_hash,
                )

        return importer

    def preview_staged_document_import(
        self,
        bibtex_text: str,
        staged_document_path: Union[str, Path],
        *,
        known_hash: str | None = None,
        source_label: str = "web-ingest-preview",
    ) -> dict:
        """Return the BibTeX and analysis that the real staged import would produce."""
        importer = self._make_staged_document_importer(
            bibtex_text,
            staged_document_path,
            known_hash=known_hash,
            source_label=source_label,
            write_audit=False,
        )
        analysis = importer.import_analysis()
        blocked_message = self._blocked_import_message(
            analysis,
            fallback_tag=self._first_bibtex_tag(bibtex_text),
        )

        if importer.ref_df.empty:
            preview_bibtex = bibtex_text
            final_tag = self._first_bibtex_tag(bibtex_text)
        else:
            preview_row = importer.ref_df.iloc[0]
            preview_bibtex = dict_to_bibtex(preview_row)
            final_tag = str(preview_row.tag)

        return {
            "bibtex": preview_bibtex,
            "tag": final_tag,
            "analysis": analysis,
            "blocked": blocked_message is not None,
            "blocked_message": blocked_message or "",
        }

    @staticmethod
    def _first_bibtex_tag(bibtex_text: str) -> str:
        from .bibtex import bibtex_to_dict

        entries = bibtex_to_dict(bibtex_text)
        if not entries:
            return ""
        return str(next(iter(entries.keys())))

    def _make_staged_document_importer(
        self,
        bibtex_text: str,
        staged_document_path: Union[str, Path],
        *,
        known_hash: str | None,
        source_label: str,
        write_audit: bool,
    ):
        from .bibtex import bibtex_to_dict
        from .import_bibtex import Bib2df_Incremental

        staged_document_path = Path(staged_document_path)
        if not staged_document_path.exists() or not staged_document_path.is_file():
            raise FileNotFoundError(f"Staged document not found: {staged_document_path}")

        entries = bibtex_to_dict(bibtex_text)
        if len(entries) != 1:
            raise ValueError("Expected exactly one BibTeX entry.")

        tag, data = next(iter(entries.items()))
        data = data.copy()
        data["tag"] = tag
        staged_abs = staged_document_path.resolve()
        suffix = staged_abs.suffix.lstrip(".") or "pdf"
        data["file"] = f":{staged_abs.as_posix()}:{suffix}"

        staging_dir = self.config_path / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag) or "entry"
        review_bib = staging_dir / f"{source_label}-{safe_tag}.bib"
        review_text = dict_to_bibtex(data)
        review_text = re.sub(
            r"(?m)^\s*file\s*=\s*\{.*\},\s*$",
            f"  file = {{{data['file']}}},",
            review_text,
        )
        review_bib.write_text(review_text, encoding="utf-8")

        importer = Bib2df_Incremental(
            bibtex_file_path=review_bib,
            doc_dir=staged_abs.parent,
            reference_library=self,
            add_hashes=True,
            incremental=True,
            fillna=True,
            write_audit=write_audit,
        )
        importer.import_bibtex_file()

        if known_hash:
            self._apply_known_hash(importer, staged_abs, known_hash)

        return importer

    @staticmethod
    def _apply_known_hash(importer, staged_abs: Path, known_hash: str):
        doc_df = importer.doc_df
        if doc_df.empty or "path" not in doc_df:
            return
        doc_match = doc_df.path.map(lambda p: Path(p) == staged_abs)
        if doc_match.any():
            importer._doc_df.loc[doc_match, "hash"] = known_hash

    @classmethod
    def _raise_for_blocked_import(cls, analysis: pd.DataFrame, fallback_tag: str):
        blocked_message = cls._blocked_import_message(analysis, fallback_tag=fallback_tag)
        if blocked_message:
            raise LibraryImportBlocked(blocked_message)

    @staticmethod
    def _blocked_import_message(analysis: pd.DataFrame, *, fallback_tag: str) -> str | None:
        blocked_actions = {"SKIP (Dupe)", "Merge/Warn"}
        if not analysis.empty:
            blocked = analysis[analysis["action"].isin(blocked_actions)]
            if not blocked.empty:
                row = blocked.iloc[0]
                parts = [
                    f"Import blocked: {row.get('action', 'duplicate warning')}",
                    f"tag {row.get('tag', fallback_tag)}",
                ]
                if row.get("title"):
                    parts.append(f"title {row.get('title')}")
                return "; ".join(parts)
        return None

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
        dfa['new_entries'] = dfa.ported_entries.astype(int) + dfa.net_entries.astype(int)
        dfa['cum_entries'] = dfa.new_entries.cumsum()
        dfa = dfa.drop(columns=['net_entries', 'ported_entries'])
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

    def find(self, path: Union[str, Path]):
        """Hash a file and return the hash and any matching records."""
        from .hasher import blake3b_hash
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        
        h = blake3b_hash(p)
        
        # Look for matches in the current library
        matches = self.doc_df[self.doc_df.hash == h]
        
        return h, matches

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

    # replaced with history
    # def audit_summary(self):
    #     """Create a dataframe summarizing the imports used to create the library."""
    #     lib_path = self.config_path
    #     # 1. Map: Old BibTeX Tag -> Normalized Tag
    #     audit_files = {}
    #     # rglob finds all tag-mapping files across all import batches
    #     for p in (lib_path / "import-audit").rglob("*audit-info.csv"):
    #         df = pd.read_csv(p, index_col='key', usecols=[1, 2])
    #         audit_files[p.name] = df
    #     return pd.concat(audit_files.values(),
    #                 keys=audit_files.keys()
    #                 ).unstack(1).droplevel(0, axis=1)

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
            # Join on hash and version
            docs = links.merge(self.doc_df, on=["hash", "version"], how="left")
            for i, (_, doc) in enumerate(docs.iterrows(), 1):
                h = f" (hash: {doc.hash[:12]})" if pd.notna(doc.get("hash")) else ""
                info.append(
                    {
                        "Field": f"Document {i}",
                        "Value": f"{doc.path}{h}",
                    }
                )
        else:
            info.append({"Field": "Documents", "Value": "[None Linked]"})

        return pd.DataFrame(info)
