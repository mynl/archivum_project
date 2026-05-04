"""Implement command line interface for archivum."""
from collections import deque
import html
from importlib.resources import files
import json
import logging
import logging.config
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Union
import yaml

import click
from lark import ParseError
import pandas as pd
from pendulum import local_timezone
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import (
    FuzzyCompleter,
    WordCompleter,
    NestedCompleter,
    DynamicCompleter,
    PathCompleter,
    Completer,
    Completion,
)
from prompt_toolkit.formatted_text import HTML

# from prompt_toolkit.document import Document
from prompt_toolkit.application.current import get_app

from pydantic import ValidationError
from rich.console import Console
from rich.text import Text
from rich import print as rich_print
from rich.table import Table

# for uber loop
from uber_shell import UberShell  # type: ignore[import-untyped]
from rustfuzz import FuzzyMatcherMultiHi  # type: ignore[import-untyped]
from querexfuzz.core import querexfuzz_help  # type: ignore[import-untyped]

from .library import Library
from .document import Document  # type: ignore[import-untyped]
from . import DEFAULT_LIBRARY, EMPTY_LIBRARY, LIBRARIES_DIR, BASE_DIR, GLOBAL_CONFIG
from .utilities import make_qd
from .config import Configurator
from .crossref import lookup_doi, search_by_title, search
from .bibtex import dict_to_bibtex, dict_to_bibtex_crossref
from .import_bibtex import Bib2df_Incremental
from .quarto import QmdParser
from .rg_tools import RipgrepTools

# local constants
DEFAULT_NEW_DIR = str(Path.home() / "Downloads")
EMPTY_DF = pd.DataFrame([])

# for local display function
qd = make_qd(
    max_table_inch_width=18,
    max_string_length=-1,  # no string truncation
    max_rows=50,
    display_func=click.echo,
)

# logger
logger = logging.getLogger(__name__)

console = Console()


# ========================================================================================
# ========================================================================================
# Library context manager


class LibraryContext:
    """Singleton context manager for the global open Library instance."""

    current = None
    no_library = EMPTY_LIBRARY
    candidates_tags = None
    candidates_titles = None
    candidates_tag_titles = None
    matcher_tags = None
    matcher_titles = None
    matcher_tags_titles = None
    matcher_hashes = None

    @classmethod
    def set(cls, lib):  # noqa
        cls.current = lib
        logger.debug("Library set to: %s", lib)

    @classmethod
    def get(cls):  # noqa
        if cls.current is None:
            lib_name = os.environ.get('ARCHIVUM_LIBRARY')
            if lib_name:
                try:
                    # Avoid circular import if called during module init, 
                    # but it's already imported at top level anyway.
                    cls.current = Library(lib_name)
                    # lib.start_watcher() - maybe not here, let the caller decide or just do it?
                    # For web app, we want the watcher.
                    cls.current.start_watcher()
                    logger.debug("Auto-loaded library %s from ARCHIVUM_LIBRARY", lib_name)
                except Exception as e:
                    logger.error("Failed to auto-load library %s: %s", lib_name, e)
                    return cls.no_library
            else:
                return cls.no_library
        return cls.current

    @classmethod
    def clear(cls):  # noqa
        logger.debug("Library %s closed.", cls.current)
        cls.current = None
        cls.refresh()

    @classmethod
    def refresh(cls):
        """Clear the cached completions so they are rebuilt on next access."""
        cls.candidates_tags = None
        cls.candidates_titles = None
        cls.candidates_tag_titles = None
        cls.candidates_hashes = None
        cls.matcher_tags = None
        cls.matcher_titles = None
        cls.matcher_tags_titles = None
        cls.matcher_hashes = None

    @classmethod
    def get_library_tags(cls):
        """Fetch unique tags from the current library context."""
        if cls.candidates_tags is not None:
            return cls.candidates_tags, cls.matcher_tags
        if cls.current is None or cls.current == EMPTY_LIBRARY:
            return []
        else:
            cls.candidates_tags = cls.current.all_tags
            cls.matcher_tags = FuzzyMatcherMultiHi(cls.candidates_tags)
            return cls.candidates_tags, cls.matcher_tags

    @classmethod
    def get_library_titles(cls):
        """Fetch unique titles from the current library context."""
        if cls.candidates_titles is not None:
            return cls.candidates_titles, cls.matcher_titles
        if cls.current is None or cls.current == EMPTY_LIBRARY:
            return []
        else:
            cls.candidates_titles = cls.current.all_titles
            cls.matcher_titles = FuzzyMatcherMultiHi(cls.candidates_titles)
            return cls.candidates_titles, cls.matcher_titles

    @classmethod
    def get_library_tag_titles(cls):
        """Fetch unique tags from the current library context."""
        if cls.candidates_tag_titles is not None:
            return cls.candidates_tag_titles, cls.matcher_tag_titles
        if cls.current is None or cls.current == EMPTY_LIBRARY:
            return []
        else:
            cls.candidates_tag_titles = cls.current.all_tag_titles
            cls.matcher_tag_titles = FuzzyMatcherMultiHi(cls.candidates_tag_titles)
            return cls.candidates_tag_titles, cls.matcher_tag_titles

    @classmethod
    def get_library_hashes(cls):
        """Fetch unique hashes from the current library context."""
        if hasattr(cls, 'candidates_hashes') and cls.candidates_hashes is not None:
            return cls.candidates_hashes, cls.matcher_hashes
        if cls.current is None or cls.current == EMPTY_LIBRARY:
            return [], None
        else:
            cls.candidates_hashes = sorted(
                cls.current.doc_df["hash"].dropna().unique().astype(str).tolist()
            )
            # Filter out empty or 'Unknown'
            cls.candidates_hashes = [h for h in cls.candidates_hashes if h and h != 'Unknown']
            cls.matcher_hashes = FuzzyMatcherMultiHi(cls.candidates_hashes)
            return cls.candidates_hashes, cls.matcher_hashes


# ========================================================================================
# ========================================================================================
def get_prompt(cmd):
    """Make a prompt for REPL."""
    lib = LibraryContext.get()
    try:
        lib_name = lib.name
        return HTML(
            "<ansired>archivum </ansired>"
            f"<ansigreen>[{lib_name}] > </ansigreen>"
            f"<ansiyellow>{cmd} > </ansiyellow>"
        )
    except AttributeError as e:
        logger.error(f"get prompt error: {e}")
        return HTML(f"ERR: <ansiyellow>{cmd} > </ansiyellow>")


# ========================================================================================
# ========================================================================================


# Helper
def _open_document(d, lib=None):
    """Try to open document at path d."""
    # Resolve to absolute path if library provided
    if lib and not Path(d).is_absolute():
        p = lib.abspath(d)
    else:
        p = Path(d)

    if not p.exists():
        logger.info("file %s not found (at %s)", p.name, p)
        return
    try:
        # windows only
        os.startfile(p)
    except FileNotFoundError:
        logger.error("File not found %s", d)
    except PermissionError:
        logger.error("Permission denied %s", d)
    except OSError as e:
        logger.error("OS error while opening %s: %s", d, e)
    except Exception as e:
        logger.error("Unexpected error: %s", e)


# Completers
def make_query_completer_static(df):
    """Make nested query completer for df (eg ref_df or database)."""
    lib = LibraryContext.get()
    if lib.is_empty:
        libs = None
    else:
        libs = {line: None for line in lib.list()}
    cols = {col: None for col in df.columns}
    cols_with_values = {
        col: {
            "==": {"__value__": None},
            "<=": {"__value__": None},
            "<": {"__value__": None},
            ">": {"__value__": None},
            ">=": {"__value__": None},
        }
        for col in df.columns
    }

    # Placeholder - will override 'open' dynamically later
    return NestedCompleter.from_nested_dict(
        {
            "top": {},
            "recent": None,
            "verbose": None,
            "select": {"*": None, "-": cols, **cols},
            "where": cols_with_values,
            "order": cols,
            "sort": cols,
            "~": cols,
            "!": cols,
            "and": None,
            "open": libs,
            "o": None,
        }
    )


class RustFuzzyCompleter(Completer):
    def __init__(self, get_candidates_func):
        """Handle prompt_toolkit fuzzy matching using my Rust fzf-like matcher."""
        self.get_candidates = get_candidates_func

    def get_completions(self, document, complete_event):
        """Matcheroo."""

        # 1. Get the full input line from the global application state.
        # This bypasses NestedCompleter's slicing to show the actual buffer content.
        try:
            full_text = get_app().current_buffer.document.text_before_cursor
        except RuntimeError:
            # Fallback for unit tests or contexts without an active app
            logger.debug("Runtime error - defaulting to text before cursor")
            full_text = document.text_before_cursor

        logger.debug("Full command line context: |%s|", full_text)

        # 2. Determine the pattern.
        # document.text_before_cursor is relative to the NestedCompleter context.
        pattern = document.text_before_cursor
        word = document.get_word_before_cursor()
        replace_len = len(word)
        logger.debug("word and length %s, %s", word, replace_len)

        # FIX: If prompt_toolkit passes an empty string (common in some nested configs or
        # immediately after typing a command without a space), try to grab the last word
        # from the full text as a fallback pattern.
        if not pattern and full_text.strip() and not full_text.endswith(" "):
            # Logic: split full line and take the last segment as the fuzzy pattern
            parts = full_text.split()
            logger.debug("split to >> %s", parts)
            if parts:
                pattern = " ".join(parts[1:])
                logger.debug("Pattern inferred from full_text: |%s|", pattern)

        logger.debug("in get_completions with pattern = |%s|", pattern)

        candidates, matcher = self.get_candidates()

        # really is nothing there - delegate
        if not pattern:
            logger.debug("NOT PATTERN BRANCH")
            for cand in candidates or []:
                yield Completion(cand, start_position=-replace_len, display=cand)  # 0,
            return

        # match
        indices, scores, highlights = matcher.query(pattern, top_k=25)
        logger.debug("rustfuzz returns indices count = %s", len(indices))

        # Calculate safe start position
        # prompt_toolkit discards completions if start_position goes out of bounds
        # of the current document slice.
        start_pos = -len(pattern)
        if -start_pos > len(document.text_before_cursor):
            logger.debug(
                "Clamping start_position %d to %d",
                start_pos,
                -len(document.text_before_cursor),
            )
            start_pos = -len(document.text_before_cursor)

        for i, score, highlight_indices in zip(indices, scores, highlights):
            candidate_string = candidates[i]

            # Use the highlight indices to format the output (e.g., for HTML)
            highlight_set = set(highlight_indices)
            highlighted_html = "".join(
                f"<style bg='ansiyellow' fg='ansired'>{html.escape(char)}</style>"
                if i in highlight_set
                else html.escape(char)
                for i, char in enumerate(candidate_string)
            )

            # Merge adjacent marks
            highlighted_html = highlighted_html.replace(
                "</style><style bg='ansiyellow' fg='ansired'>", ""
            )

            if 0:
                highlighted_html = "".join(
                    f"<mark>{char}</mark>" if i in highlight_set else char
                    for i, char in enumerate(candidate_string)
                )

                # Merge adjacent marks
                highlighted_html = highlighted_html.replace("</mark><mark>", "")
            logger.info("RF YIELDING %s", highlighted_html)
            yield Completion(
                candidate_string,
                start_position=start_pos,
                display=HTML(highlighted_html),
            )


# ========================================================================================
# ========================================================================================
@click.group()
def entry():
    """CLI for managing bibliographic entries."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


@entry.command()
@click.option("-v", "--verbose", is_flag=True, help="Show full lists of problematic items.")
@click.option("-x", "--execute", is_flag=True, help="Actually perform fixes (e.g., cleaning orphans).")
def library_audit(verbose, execute):
    """
    \b
    Perform a comprehensive structural audit of the library:
    - Missing Files: References pointing to files that don't exist.
    - Orphan Docs: Document metadata without any reference linking to it.
    - Missing Docs: References with no linked documents.
    - Broken Links: ref-doc mappings with invalid tags or paths.
    - Orphan Extracts: Text extracts for documents no longer in library.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    click.secho(f"Auditing library: {lib.name}", fg="cyan", bold=True)

    findings = lib.audit()

    # 1. Missing Physical Files
    missing = findings["missing_physical_files"]
    if missing:
        click.secho(f"!! Found {len(missing)} missing physical files.", fg="red")
        if verbose:
            for f in missing: click.echo(f"  - {f}")
    else:
        click.echo("OK: All documented files exist.")

    # 2. Orphan Docs
    orphans = findings["docs_missing_ref"]
    if orphans:
        click.secho(f"!! Found {len(orphans)} document records with no reference linked.", fg="yellow")
        if verbose:
            for p in orphans: click.echo(f"  - {p}")
    else:
        click.echo("OK: All document records are linked to references.")

    # 3. Missing Docs
    refs_missing_doc = findings["refs_missing_doc"]
    if refs_missing_doc:
        click.secho(f"Found {len(refs_missing_doc)} references without a document.", fg="blue")
        if verbose:
            for t in refs_missing_doc:
                title = lib.ref_df[lib.ref_df.tag == t].title.iloc[0]
                click.echo(f"  - {t}: {title[:60]}...")
    else:
        click.echo("OK: All references have at least one document.")

    # 4. Broken Links
    broken_tags = findings["broken_tag_links"]
    if broken_tags:
        click.secho(f"!! Found {len(broken_tags)} broken tag links in ref-doc.", fg="red")
        if verbose:
            click.echo(f"  - Tags: {broken_tags}")

    broken_ids = findings["broken_id_links"]
    if broken_ids:
        click.secho(f"!! Found {len(broken_ids)} broken ID links in ref-doc (no matching document).", fg="red")
        if verbose:
            click.echo(f"  - Tags with broken links: {broken_ids}")

    # 5. Orphan Text Extracts
    orphan_txt = findings["orphan_extracts"]
    if orphan_txt:
        if execute:
            click.secho(f"Cleaning {len(orphan_txt)} orphaned text extracts...", fg="red", bold=True)
            lib.clean_text_extracts(execute=True)
        else:
            click.secho(f"!! Found {len(orphan_txt)} orphaned text extracts. Use -x to clean.", fg="yellow")
            if verbose:
                for f in orphan_txt: click.echo(f"  - {f}")
    else:
        click.echo("OK: No orphaned text extracts.")


@entry.command()
@click.argument("lib_name", type=str, default="")
@click.option("-p", "--port", default=9124, help="Port to run the server on.")
@click.option("-a", "--address", default="127.0.0.1", help="Host address to bind to (e.g. 0.0.0.0 for all interfaces).")
@click.option("-b", "--browser", "open_browser", is_flag=True, default=False, help="Open browser automatically.")
@click.option("-d", "--debug", is_flag=True, help="Run in Flask debug mode (includes reloader).")
def serve(lib_name, port, address, open_browser, debug):
    """Launch the web interface."""
    from .web import create_app
    import webbrowser
    from threading import Timer

    # Logic to open the library, similar to 'uber'
    if lib_name == "":
        lib_name = DEFAULT_LIBRARY

    try:
        # We need to set the library in the singleton so the app.run(debug=True)
        # fork can also access it if needed, though env var is safer for subprocesses.
        lib = Library(lib_name)
        LibraryContext.set(lib)
        lib.start_watcher()
        click.echo(f"Serving library: {lib.name}")
    except Exception as e:
        click.secho(f"Error opening library '{lib_name}': {e}", fg="red")
        return

    # Set environment variable so the Flask app and its subprocesses know which lib to use
    os.environ['ARCHIVUM_LIBRARY'] = lib.name
    # Also update GLOBAL_CONFIG so Library() calls in this process use it
    GLOBAL_CONFIG['default_library'] = lib.name

    app = create_app()
    # Use '127.0.0.1' for the browser link if binding to 0.0.0.0, else use address
    display_addr = "127.0.0.1" if address == "0.0.0.0" else address
    url = f"http://{display_addr}:{port}"
    if open_browser:
        # Give the server a moment to start before opening browser
        Timer(1.5, lambda: webbrowser.open(url)).start()

    click.echo(f"Starting Archivum Web at {url} (binding to {address})")
    try:
        # debug=True enables the auto-reloader
        app.run(host=address, port=port, debug=debug, use_reloader=debug)
    finally:

        # Ensure watcher is stopped on exit
        if not debug: # Reloader makes this tricky, but for standard run it's good
            lib.stop_watcher()


# ========================================================================================
@entry.command()
@click.option(
    "-t",
    "--task",
    type=click.Choice(["sharding", "orphans", "missing"]),
    default="sharding",
    show_default=True,
    help="Validation task to perform.",
)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    show_default=True,
    help="Actually perform the fixes; otherwise, do a dry run and report.",
)
def library_validate(task, execute):
    """
    Audit and fix library structure.

    \b
    Tasks:
    - sharding: verify files are in the correct hash-based folders.
    - orphans: verify orphan docs are correctly sharded.
    - missing: find documents in the index that don't exist on disk.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    if execute:
        click.secho(f"EXECUTING: {task}...", fg="red", bold=True)
    else:
        click.secho(f"DRY RUN: {task} audit...", fg="cyan")

    report = lib.validate(task=task, execute=execute)

    if report.empty:
        click.echo("No issues found.")
    else:
        qd(report)
        if not execute:
            click.secho(f"\nFound {len(report)} items to address. Use -x --execute to fix.", fg="yellow")
        else:
            click.secho(f"\nProcessed {len(report)} items.", fg="green")


# ========================================================================================
@entry.command()
@click.argument("tag", type=str)
@click.option(
    "-i",
    "--info",
    is_flag=True,
    help="Show all information about the tag before editing.",
)
def edit_tag(tag, info):
    """Edit a reference entry interactively."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    # 1. Find the reference
    row = lib.ref_df[lib.ref_df.tag == tag]
    if row.empty:
        click.echo(f"Reference '{tag}' not found.")
        return

    # 2. Show info if requested
    if info:
        click.secho(f"Information for Tag: {tag}", fg="cyan", bold=True)
        tag_info = lib.get_tag_info(tag)
        qd(tag_info)
        click.echo("") # spacer
        return 

    # 3. Convert to BibTeX
    bib_str = dict_to_bibtex(row.iloc[0])

    # 3. Edit in external editor
    edited_bib = click.edit(bib_str, extension=".bib")
    if edited_bib is None or edited_bib == bib_str:
        click.echo("No changes made.")
        return

    # 4. Parse the edited BibTeX
    new_data = Bib2df_Incremental.parse_line(edited_bib)
    if not new_data:
        click.echo("Error parsing edited BibTeX.")
        return

    # 5. Update the library
    try:
        lib.update_reference(tag, new_data)
        click.echo(f"Reference '{tag}' updated.")
    except Exception as e:
        click.echo(f"Error updating reference: {e}")


# ========================================================================================
@entry.command()
@click.argument("tag", type=str)
@click.option("-x", "--execute",
    is_flag=True,
    default=False,
    show_default=True,
    help="Actually execute.")
def delete_tag(tag, execute):
    """Delete a reference from the library."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    # always ask regardless
    if not click.confirm(f"Are you sure you want to delete '{tag}'?"):
        return

    if not execute:
        return

    try:
        lib.remove_reference(tag)
        click.echo(f"Reference '{tag}' deleted.")
    except Exception as e:
        click.echo(f"Error deleting reference: {e}")


# ========================================================================================
@entry.command()
@click.argument("old_name", type=str)
@click.argument("new_name", type=str)
def library_rename(old_name, new_name):
    """Rename a library folder and update its internal name."""
    click.echo('UNTESTED - sorry, not doing that...')
    return
    try:
        Library.rename_library(old_name, new_name)
        click.echo(f"Library '{old_name}' renamed to '{new_name}'.")
    except Exception as e:
        click.echo(f"Error renaming library: {e}")


# ========================================================================================
@entry.command()
@click.argument("old_name", type=str)
@click.argument("new_name", type=str)
def library_copy(old_name, new_name):
    """Copy a library folder and update its internal name."""
    click.echo('UNTESTED - sorry, not doing that...')
    return
    try:
        Library.copy_library(old_name, new_name)
        click.echo(f"Library '{old_name}' copied to '{new_name}'.")
    except Exception as e:
        click.echo(f"Error copying library: {e}")


# ========================================================================================
@entry.command()
@click.argument("lib_name", type=str)
def library_open(lib_name):
    """Open a library by name and set it as current."""
    try:
        lib = Library(lib_name)
        LibraryContext.set(lib)
        logger.info(
            f"Opened {lib.config.name}, loaded {len(lib.ref_df):,d} references."
        )
    except Exception as e:
        logger.error("Open library error: %s", e)


# ========================================================================================
@entry.command()
def library_locate():
    """Open explorer to see files at the location of the open library, if any."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...nothing to save. Returning")
        return
    subprocess.run(f"start explorer {lib.config_path.absolute()}", shell=True)


# ========================================================================================
@entry.command()
def library_save():
    """Save the current library to disk."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...nothing to save. Returning")
        return
    lib.save()
    click.echo(f"{lib.name} saved")


# ========================================================================================
@entry.command()
def library_close():
    """
    Close the currently open library.

    This is a command line concept; the Library class has no close
    method. You just delete it. It does NOT track if it is dirty and
    needs to change. You do not want misc. save on close behavior
    because it could be open on multiple machines. Collisions are
    not tracked.
    """
    lib = LibraryContext.get()
    nm = lib.name
    if lib.is_empty:
        click.secho("No library open; ignoring.")
        return
    logger.info("Closing library %s", lib)
    LibraryContext.clear()
    click.secho(f"Library {nm} closed.")


# ========================================================================================
@entry.command()
@click.argument("lib_name", nargs=-1)
def library_create(lib_name):
    """
    Create and open a new library. SEE ALSO THE CONFIG VERSION

    Interactively create a YAML config file for a new library
    called lib_name. Save config. Then open and return the library.

    Library must not already exist.
    """
    click.echo('UNTESTED - sorry, not doing that...')
    return
    lib_name = ' '.join(lib_name)
    lib_dir_name = lib_name.replace(" ", "-")

    # sort the file out
    lib_path = LIBRARIES_DIR / lib_dir_name
    if lib_path.exists():
        click.echo(f"Error: Library file {lib_path} already exists.")
        click.echo("Pick another name. Returning, no library created.")
        return
    else:
        lib_path.mkdir(parents=True)
    click.secho("=== Library Config Creator ===", fg="cyan")
    click.secho(f"Creating Library {lib_name} at {lib_path}")

    def local_prompt(x):
        """Make the prompt string."""
        return f"[{lib_name}] {x} > "

    # fyi = but can't pass into prompt
    # tablefmt_completer = FuzzyCompleter(
    #     WordCompleter(
    #         [
    #             "mixed_grid",
    #             "simple_grid",
    #             "outline",
    #             "simple_outline",
    #             "mixed_outline",
    #             "rst",
    #         ],
    #         ignore_case=True,
    #     )
    # )

    click.echo('Entering loop')

    while True:
        config = {
            "name": lib_name,
            "description": click.prompt(local_prompt("Description")),
            "ref_columns": [
                "tag",
                "type",
                "title",
                "year",
                "author",
                "journal",
                "volume",
                "number",
                "month",
                "pages",
                "booktitle",
                "editor",
                "edition",
                "chapter",
                "doi",
                "isbn",
                "publisher",
                "institution",
                "address",
                "url",
                "mendeley-tags",
                "arc-citations",
                "arc-source",
            ],
            # TODO - MAGIC STRING
            "bibtex_file": click.prompt(
                local_prompt("BibTeX File"),
                default=f"\\S\\Telos\\biblio\\{lib_dir_name}-test.bib",
            ),
            "full_text": True,
            "hash_workers": click.prompt(
                local_prompt("Number of hash workers"), default=8, type=int
            ),
            "tablefmt": click.prompt(local_prompt("Table format"), default="mixed_grid"),
        }
        try:
            con = Configurator(**config)
            break
        except ValidationError as e:
            logger.error("configuration error %s", e)
            click.secho("Error in config, no file written. Adjust!")
            # todo  - a quit option!

    # con must be valid
    con.save(lib_path)
    click.secho(f"\nConfig written to {lib_path}", fg="green")
    click.echo(f"Use 'open {lib_name}' to open")


# ========================================================================================
@entry.command(name="list")  # can't use list as Python fun, it is a built in
@click.option(
    "-d",
    "--details",
    is_flag=True,
    show_default=True,
    help="Show detailed information about each library.",
)
def list_libraries(details):
    """List all available libraries."""
    logger.debug("Listing libraries...")
    # TODO: Implement listing logic
    if details:
        logger.debug("Detailed information.")
        df = Library.list_deets()
        qd(df)
    else:
        logger.debug("Basic information.")
        lib_list = Library.list()
        lib_list.insert(0, "Library")
        qd(lib_list)


# ========================================================================================
@entry.command(name="status")
def library_status():
    """Display library auto-reload status and file modification times."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open.")
        return
    
    info = lib.get_status_info()
    
    click.secho(f"Library: {info['name']}", fg="cyan", bold=True)
    click.echo(f"Path:    {info['path']}")
    
    reload_status = "YES" if info['needs_reload'] else "NO"
    reload_color = "yellow" if info['needs_reload'] else "green"
    click.echo("Needs Reload: ", nl=False)
    click.secho(reload_status, fg=reload_color, bold=True)
    
    watcher_status = "ACTIVE" if info['watcher_active'] else "INACTIVE"
    watcher_color = "green" if info['watcher_active'] else "red"
    click.echo("Watcher:      ", nl=False)
    click.secho(watcher_status, fg=watcher_color)
    
    click.echo("\nFile Status:")
    df = pd.DataFrame(info['files'])
    qd(df)


# ========================================================================================
@entry.command()
def stats():
    """Display library stats library."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return
    logger.debug("Library stats %s", lib)
    qd(lib.stats().reset_index(drop=False))


# ========================================================================================
@entry.command()
def list_stats():
    """Display library stats for all libraries."""
    qd(Library.list_stats(), show_index=True, vrule_widths=(1, 0, 0))


# ========================================================================================
@entry.command()
def library_history():
    """Display library history based on imports."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return
    qd(lib.history())


# ========================================================================================
@entry.command()
@click.option(
    "-f",
    "--field",
    type=str,
    default="",
    show_default=True,
    help="Show distinct values of field in each library field.",
)
def get_distinct_values(field):
    """Display number of distinct values in each library field."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't know where to look for files. Returning")
        return
    field = field.strip()
    logger.debug("Distinct values for field %s", field)
    if field == "":
        df = lib.distinct_values_by_field().reset_index(drop=False)
        df.index.name = "field"
        df = df.sort_values(["distinct"], ascending=[False])
        qd(df)
    elif field in lib.database:
        df = lib.distinct_value_counts(field).reset_index(drop=False)
        qd(df)
    else:
        click.echo(f"Field {field} not found in library database.")


# ========================================================================================
def decorate_results(lib, df):
    """Add rich formatting to a dataframe for display."""
    res = df.copy()

    # Ensure it's sorted by tag if present
    if "tag" in res.columns:
        res = res.sort_values("tag")

    # Clickable hash
    if "hash" in res.columns:

        def make_link(row):
            h = str(row["hash"])[:6]
            if "path" in res.columns and pd.notna(row["path"]) and row["path"]:
                try:
                    path_uri = Path(lib.abspath(row["path"])).as_uri()
                    return f"[link={path_uri}][blue]{h}[/blue][/link]"
                except Exception:
                    return f"[blue]{h}[/blue]"
            return f"HH{h}"

        res["hash"] = res.apply(make_link, axis=1)

    # Author
    if "author" in res.columns:

        def trim_author(s):
            if not isinstance(s, str) or not s:
                return ""
            author_bits = [i.split(",")[0].strip("{}") for i in s.split(" and ")]
            if len(author_bits) > 2:
                *first, last = author_bits
                name = ", ".join(first) + f", and {last}"
            elif len(author_bits) == 2:
                name = f"{author_bits[0]} and {author_bits[1]}"
            else:
                name = author_bits[0] if author_bits else ""
            return f"[yellow]{name}[/yellow]"

        res["author"] = res["author"].map(trim_author)

    # Type
    if "type" in res.columns:
        res["type"] = res["type"].map(
            lambda x: "[red]bk[/red]" if x == "book" else "  "
        )

    # Journal/Publisher -> Source
    if "journal" in res.columns or "publisher" in res.columns:

        def format_source(row):
            source = str(row.get("journal") or row.get("publisher") or "").strip("{}")
            if not source or source == "nan":
                return ""
            source = " ".join(source.split()[:4])
            return f"[i]{source}[/i]"

        res["source"] = res.apply(format_source, axis=1)

    return res


def _display_results(lib, result, table=False):
    """Internal helper to display query results in tabular or list format."""
    if result.empty:
        click.echo("No results found.")
        return

    # 1. Decorate for display
    decorated = decorate_results(lib, result)

    if table:
        # Table output using Rich Table
        rich_table = Table(show_header=True, header_style="bold magenta", box=None)

        # Decide which columns to show
        preferred_cols = ["tag", "type", "author", "title", "year", "hash", "source"]
        cols_to_show = [c for c in preferred_cols if c in decorated.columns]

        for col in cols_to_show:
            rich_table.add_column(col)

        for _, row in decorated.iterrows():
            rich_table.add_row(*[str(row.get(c, "")) for c in cols_to_show])

        console.print(rich_table)
    else:
        # List (Compact) output - former format_biblio logic
        mx_tag = (
            decorated.tag.str.len().max() if "tag" in decorated.columns else 10
        )
        mx_tag = min(20, mx_tag + 1)
        tag_fmt = f"{{:<{mx_tag}}}"

        for _, row in decorated.iterrows():
            bits = []
            if "hash" in row:
                bits.append(row["hash"])
            if "type" in row:
                bits.append(row["type"])
            if "title" in row:
                bits.append(f'"{str(row["title"]).strip("{}")}"')
            if "author" in row:
                bits.append(row["author"])
            if "source" in row:
                bits.append(row["source"])

            line = ""
            if "tag" in row:
                line = tag_fmt.format(row["tag"])

            line += " " + " ".join(bits)
            console.print(line)
        console.print("")  # spacing


# ========================================================================================
@entry.command()
@click.argument("expr", nargs=-1)
@click.option(
    "-d",
    "--database",
    type=click.Choice(["database", "doc", "ref", "ref-doc"]),
    default="database",
    show_default=True,
    help='Database (dataframe) to process. Must be "database" (default), "doc", "ref", or "ref-doc".',
)
@click.option(
    "-t",
    "--table",
    is_flag=True,
    default=False,
    show_default=True,
    help="Output results in a tabular format (default is compact).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    help="Output results to a .qmd file.",
)
@click.option(
    "-a",
    "--abstract",
    is_flag=True,
    default=False,
    show_default=True,
    help="Include abstracts in the .qmd output.",
)
@click.pass_context
def q(
    ctx,
    expr: tuple,
    database: str,
    table: bool,
    output: Path | None,
    abstract: bool,
):
    """Execute a single query and return immediately."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't know what to query. Returning")
        return
    if database == "ref":
        df = lib.ref_df
    elif database == "doc":
        df = lib.doc_df
    elif database == "ref-doc":
        df = lib.ref_doc_df
    else:
        df = lib.database

    if getattr(df, "querex", None) is None:
        click.echo(f"querex not attached to {database}, exiting.")
        return

    expr_joined = " ".join(expr)
    if not expr_joined:
        click.echo("No expression provided.")
        return

    # Automatically ensure metadata is selected for reference-style databases
    if database in ("database", "ref") and "select" not in expr_joined.lower():
        expr_str = "select path, hash, type, * " + expr_joined
    else:
        expr_str = expr_joined

    try:
        result = df.querex(expr_str)

        # Defensive check: ensure we have a DataFrame
        if not isinstance(result, pd.DataFrame):
            click.echo(f"Query did not return a table (returned {type(result)}).")
            return

        if output:
            from .quarto import generate_qmd_report

            out_path = Path(output)
            if not out_path.suffix:
                out_path = out_path.with_suffix(".qmd")

            generate_qmd_report(
                lib, result, out_path, include_abstract=abstract, query=expr_str
            )
            click.echo(f"Summary of {len(result)} results written to {out_path}")
        else:
            _display_results(lib, result, table=table)

    except Exception as e:
        click.echo(f"[Error] {e}")


@entry.command()
@click.option(
    "-t",
    "--table",
    is_flag=True,
    default=False,
    show_default=True,
    help="Output in table format",
)
@click.argument("expr", type=str, nargs=-1)
@click.pass_context
def f(ctx, table, expr):
    """
    Find expr by tag or query if expr is a full query statement.

    Alias for 'q recent top 50 tag ~ expr' (unless expr is already a full statement).
    """
    expr_joined = " ".join(expr)
    if not expr_joined:
        click.echo("No expression provided.")
        return

    # If it's not already a full query statement, wrap it
    if expr_joined[0] != "!" and expr_joined.find("~") == -1:
        query_expr = f"recent top 50 tag ~ {expr_joined}"
    else:
        # already a query, but we still want the defaults if not specified
        query_expr = expr_joined
        if "top" not in query_expr.lower():
            query_expr = "top 50 " + query_expr
        if "recent" not in query_expr.lower():
            query_expr = "recent " + query_expr

    # Forward to q
    ctx.invoke(q, expr=tuple(query_expr.split()), table=table)


# ========================================================================================
@entry.command()
@click.argument(
    "start",
    nargs=-1,
    default=(),  # with nargs=-1 the default needs to be an empty tuple, it gets converted to a string below
    required=False,
)
@click.option(
    "-d",
    "--database",
    # Define the allowed choices as a list of strings
    type=click.Choice(["database", "doc", "ref", "ref-doc"]),
    default="database",
    show_default=True,
    help='Database (dataframe) to process. Must be "database" (default), "doc", "ref", or "ref-doc".',
)
def query(start: str, database: str):
    """Interactive REPL to run multiple queries on the file index with fuzzy completion."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't what to query. Returning")
        return
    if database == "ref":
        df = lib.ref_df
    elif database == "doc":
        df = lib.doc_df
    elif database == "ref-doc":
        df = lib.ref_doc_df
    else:
        df = lib.database

    if getattr(df, 'querex', None) is None:
        click.echo(f'querex not attached to {database}, exiting.')
        return

    click.echo('Available columns: ' + ', '.join(df.columns))
    result = EMPTY_DF
    base_completer = make_query_completer_static(df)

    def tag_branch():
        tag_values = sorted({str(tag) for tag in result["tag"].dropna().unique()})
        return FuzzyCompleter(WordCompleter(tag_values, sentence=True))

    # Inject dynamic fuzzy completer into 'open' and 'o'
    base_completer.options["open"] = DynamicCompleter(tag_branch)
    base_completer.options["o"] = DynamicCompleter(tag_branch)

    from prompt_toolkit.history import FileHistory
    history_file = BASE_DIR / "query_history.txt"
    session = PromptSession(
        completer=base_completer,
        history=FileHistory(str(history_file))
    )

    # sort out start = initial query
    start = ' '.join(start)

    while True:
        try:
            expr = start or session.prompt(get_prompt(f"query::{database}"))
            start = ""
            pipe = False
            if expr.lower() in {"exit", "x", ".."}:
                break
            elif expr == "?":
                click.echo(querexfuzz_help())
                continue
            elif expr == "cls":
                # clear screen
                os.system("cls")
                continue
            elif expr.find(">") >= 0:
                # contains a pipe
                expr, pipe = expr.split(">")
                pipe = pipe.strip()
            elif expr.startswith("o ") or expr.startswith("open "):
                # open files
                if result.empty:
                    click.echo("No existing query! Run query first")
                    continue
                # open file mode, start with o n
                try:
                    # o or open
                    if expr.startswith("o "):
                        expr = expr[1:].strip()
                    elif expr.startswith("open "):
                        expr = expr[5:].strip()
                    logger.info(f"{expr=}")
                    tags = result.loc[result.tag.str.contains(expr, regex=True, case=False), "tag"]
                    tags = sorted(set(tags.values))
                    docs = lib.ref_doc_df.query("tag in @tags").path.values
                    logger.info(f"{docs=}")
                    print(f"Trying to open {docs=}")
                    logger.info(f"Trying to open {docs=}")
                    for d in docs:
                        lib.open_document(d)
                except Exception:
                    raise
                continue

            # if here, run query work
            try:
                # set as ref_df or database above...
                result = df.querex(expr)
            except ParseError as e:
                logger.error("Parsing error")
                logger.error(e)
            else:
                try:
                    qd(result)
                except Exception as e:
                    print(f"greater_tables related exception from qd\n{e}")
                if pipe:
                    click.echo(f"Found pipe clause {pipe=} TODO: deal with this!")
        except Exception as e:
            click.echo(f"[Error] {e}")


# ========================================================================================
@entry.command()
@click.option("--author", "-a", help="Author name", show_default=True)
@click.option("--title", "-t", help="Title of work", show_default=True)
@click.option("--doi", "-d", help="DOI string", show_default=True)
@click.option("--raw", "-r", is_flag=True, help="Show raw output.", show_default=True)
@click.option("--keywords", "-k", help="Search keywords", show_default=True)
def crossref(
    author: Union[str, None],
    title: Union[str, None],
    doi: Union[str, None],
    keywords: Union[str, None],
    raw: bool,
) -> None:
    """
    Fetch metadata from Crossref and output BibTeX.

    \b
    Priority:
    1. DOI (if provided)
    2. Title (if provided without author/keywords)
    3. Generic Search (using keywords, title, author)
    """
    result = None

    if doi:
        result = lookup_doi(doi)
    elif title and not author and not keywords:
        result = search_by_title(title)
    else:
        # 'keywords' maps to the 'query' param in generic search
        items = search(query=keywords, title=title, author=author, rows=1)
        if items:
            result = items[0]

    if raw:
        click.echo(result)

    if result:
        bibtex = dict_to_bibtex_crossref(result)
        click.echo(bibtex)
    else:
        click.echo("No results found.", err=True)


# ========================================================================================
@entry.command(name="import-bibtex")
@click.argument(
    "bibtex_path",
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
@click.option(
    "-p",
    "--doc-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing docs referenced in the BibTeX file; "
    "defaults to the BibTeX file's directory.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    default=0,
    show_default=True,
    help="Increase verbosity: -v (Summary), -vv (Guardian Table), -vvv (Full Diagnostics).",
)
@click.option(
    "-h/-nh",
    "--add-hashes/--no-hashes",
    "add_hashes",
    is_flag=True,
    default=True,
    show_default=True,
    help="Hash input pdf files (Default: True).",
)
@click.option(
    "-i/-ni",
    "--incremental/--no-incremental",
    "incremental",
    is_flag=True,
    default=True,
    show_default=True,
    help="Guardian mode: Hash, check for duplicates in library, and shard/organize immediately (Default: True).",
)
@click.option(
    "-t/-nt",
    "--extract-text/--no-extract-text",
    "extract_text_flag",
    is_flag=True,
    default=True,
    show_default=True,
    help="Auto-run text extraction on imported files (Default: True).",
)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    show_default=True,
    help="Actually perform the import; otherwise, do a dry run and report stats.",
)
def import_bibtex(bibtex_path: Path, doc_dir: Path, add_hashes: bool, incremental: bool, verbose: int, execute: bool, extract_text_flag: bool):
    """
    Import new references from a BibTeX file into the current library.

    bibtex_path can be a path to a specific bibtex file or a directory
    containing a single bibtex file.
    """
    if execute:
        logger.info("Execution enabled: changes will be applied.")
    else:
        logger.info("Dry run mode: no changes applied.")

    # Directory mode: find the unique bibtex file
    if bibtex_path.is_dir():
        search_dir = bibtex_path
        bibs = list(search_dir.glob("*.bib"))
        if len(bibs) == 1:
            bibtex_file = bibs[0]
            if doc_dir is None:
                doc_dir = search_dir
            bibtex_path = bibtex_file
            logger.info(f"Found unique BibTeX file: {bibtex_path.name}")
        else:
            click.echo(f"Error: If bibtex_path not specified, directory must contain exactly one .bib file. Found {len(bibs)} in {search_dir}")
            return

    # File mode or resolved directory mode
    if doc_dir is None:
        doc_dir = bibtex_path.parent
        logger.info(f"PDF directory not specified; defaulting to {doc_dir}")

    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot import.")
        return

    # create importer
    b = Bib2df_Incremental(
            bibtex_file_path=bibtex_path,
            doc_dir=doc_dir,
            reference_library=lib,
            add_hashes=add_hashes,
            incremental=incremental
        )
    # do the import
    import_df = b.import_bibtex_file()

    # Default / -v: summary table
    qd(import_df)

    # -vv: summary + Guardian Analysis table (Always run on full set)
    if verbose == 2:
        qd(b.import_analysis())

    # -vvv: summary + Full diagnostic table
    if verbose >= 3:
        qd(b.import_analysis_full())

    if execute:
        click.echo(f"Updating with {len(b.ported_df)} entries.")
        b.update_library(save=True)
        if extract_text_flag:
            # Only extract text for the NEWLY imported documents
            # b.doc_df contains the document records for this import
            new_paths = [lib.abspath(p) for p in b.doc_df.path.values if p.lower().endswith(".pdf")]
            if new_paths:
                click.echo(f"Running text extraction for {len(new_paths)} new PDF(s)...")
                # We can use the hashes from the importer to avoid re-hashing
                path_to_hash = {lib.abspath(row.path): row.hash for _, row in b.doc_df.iterrows()}
                from .document import extract_text_for_paths
                extract_text_for_paths(
                    new_paths,
                    lib.text_dir_path,
                    extractor=lib.config.extractor,
                    workers=lib.config.hash_workers,
                    hashes=path_to_hash
                )
            else:
                click.echo("No new PDF documents to extract text from.")


# ========================================================================================
@entry.command()
def library_config():
    """
    Dump the current library config file.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot import.")
        return
    c = lib.config.model_dump()
    rich_print(c)


# ========================================================================================
def _enhance_bibtex_file(bibtex_file: Path, arxiv: bool = True):
    """Refactored core logic of stage-enhance for use in stage-docs."""
    # We use Bib2df_Incremental just for its parsing logic
    importer = Bib2df_Incremental(
        bibtex_file_path=bibtex_file, doc_dir=None, reference_library=None
    )

    # Read the file and split into chunks
    txt = bibtex_file.read_text(encoding="utf-8")
    chunks = re.split(r"(?m)^\s*@", txt)

    new_entries = []
    if chunks[0].strip():
        new_entries.append(chunks[0].strip())

    enhanced_count = 0
    for chunk in chunks[1:]:
        if not chunk.strip():
            continue

        # parse_line handles the @ optionally
        data = importer.parse_line("@" + chunk)
        if not data:
            new_entries.append("@" + chunk)
            continue

        if arxiv and data.get("archivePrefix") == "arXiv" and not data.get("journal"):
            title = data.get("title", "").strip("{} ")
            if title:
                click.echo(f"Searching Crossref for: {title[:60]}... ", nl=False)
                res = search_by_title(title)
                if res:

                    def get_list_safe(key: str) -> str:
                        val = res.get(key)
                        if isinstance(val, list) and val:
                            return str(val[0])
                        return str(val) if val else ""

                    journal = get_list_safe("container-title")
                    if journal:
                        data["journal"] = journal
                        if volume := res.get("volume"):
                            data["volume"] = volume
                        if issue := res.get("issue"):
                            data["number"] = issue
                        if page := res.get("page"):
                            data["pages"] = page
                        if doi := res.get("DOI"):
                            data["doi"] = doi
                        if publisher := res.get("publisher"):
                            data["publisher"] = publisher

                        # Date extraction
                        date_parts = (
                            res.get("published-print", {}).get("date-parts")
                            or res.get("published-online", {}).get("date-parts")
                            or res.get("created", {}).get("date-parts")
                        )
                        if date_parts and date_parts[0]:
                            data["year"] = str(date_parts[0][0])

                        click.secho("Found", fg="green")
                        enhanced_count += 1
                    else:
                        click.echo("Found but no journal")
                else:
                    click.echo("Not found")

        new_entries.append(dict_to_bibtex(data))

    # Write back
    new_txt = "\n\n".join(new_entries)
    bibtex_file.write_text(new_txt, encoding="utf-8")
    return enhanced_count


@entry.command(name="stage-docs")
@click.argument(
    "doc_path",
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
@click.option(
    "-f/-nf",
    "--flag-duplicates/--no-duplicates",
    "flag_duplicates",
    is_flag=True,
    default=True,
    show_default=True,
    help="Check for hash duplicates in library before processing.",
)
@click.option(
    "-d",
    "--delete",
    "delete_dupes",
    is_flag=True,
    default=False,
    show_default=True,
    help="Delete duplicates from source folder if found.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    default=0,
    show_default=True,
    help="Increase verbosity.",
)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    default=False,
    show_default=True,
    help="Actually perform the import after review.",
)
@click.option(
    "-h",
    "--enhance",
    is_flag=True,
    default=True,
    show_default=True,
    help="Automatically enhance arXiv entries via Crossref.",
)
@click.pass_context
def stage_docs(ctx, doc_path: Path, flag_duplicates: bool, delete_dupes: bool, verbose: int, execute: bool, enhance: bool):
    """
    Prepare new documents for import by staging metadata in a BibTeX file.

    \b
    1. Checks for hash duplicates in the library.
    2. Extracts metadata from new files.
    3. Generates a .bib file for review in Sublime Text.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot stage documents.")
        return

    # 1. Find the files
    if doc_path.is_dir():
        doc_paths = lib.find_docs(doc_path)
    else:
        doc_paths = [doc_path]

    if not doc_paths:
        click.echo(f"No documents found in {doc_path}")
        return

    # 2. Pre-check for Duplicates
    click.echo(f"Hashing {len(doc_paths)} files...")
    from .hasher import hash_many3
    hashes = hash_many3(doc_paths, workers=lib.config.hash_workers)

    survivors = doc_paths
    if flag_duplicates:
        click.echo(f"Checking for duplicates in library...")
        dupe_rows = []
        new_paths = []

        lib_docs = lib.doc_df
        lib_ref_docs = lib.ref_doc_df
        lib_refs = lib.ref_df

        for p in doc_paths:
            h = hashes.get(p)
            if h and h in lib_docs.hash.values:
                # Find matching info - use hash directly in ref_doc_df
                match_tags = lib_ref_docs[lib_ref_docs.hash == h].tag.tolist()
                tag = match_tags[0] if match_tags else "Unknown"
                title = lib_refs[lib_refs.tag == tag].title.iloc[0] if tag != "Unknown" else "N/A"

                dupe_rows.append({
                    "Staging File": p.name,
                    "Hash": h[:12],
                    "Match Tag": tag,
                    "Match Title": title[:50]
                })
            else:
                new_paths.append(p)

        if dupe_rows:
            click.secho(f"\nFound {len(dupe_rows)} duplicates already in library:", fg="yellow")
            qd(pd.DataFrame(dupe_rows))

            if delete_dupes:
                if click.confirm(f"\nDelete these {len(dupe_rows)} duplicates from source?", default=False):
                    for p in doc_paths:
                        if p not in new_paths:
                            p.unlink()
                    click.echo("Duplicates deleted.")
            else:
                click.secho("\nRun again with -d to delete these duplicates from source.", fg="cyan")

            survivors = new_paths
        else:
            click.echo("No duplicates found.")

    if not survivors:
        click.echo("No new documents to process.")
        return

    click.echo(f'Processing {len(survivors)} documents...')

    # 3. Process the Survivors
    sorted_survivors = sorted(survivors)
    first_hash = hashes[sorted_survivors[0]]
    n_survivors = len(survivors)

    bibs = [f"% import from {doc_path.absolute()}"]
    actual_survivors = []
    for p in sorted_survivors:
        h = hashes[p]
        # Rename! prepend 6 chars of hash
        if not p.name.startswith(h[:6]):
            new_name = f"{h[:6]}-{p.name}"
            new_p = p.parent / new_name
            if not new_p.exists():
                p.rename(new_p)
                p = new_p
            else:
                p = new_p

        if p.suffix.lower() not in {'.pdf', '.djvu', '.epub'}:
            click.echo(f'WARNING: Non-standard format {p.suffix} for {p.name}')
        try:
            logger.info('gathering import info for %s', p)
            doc = Document(p)
            doc.process()
            bibs.append(doc.bibtex())
            actual_survivors.append(p)
        except Exception as e:
            click.echo(f"Error extracting metadata for {p.name}: {e}")

    # 4. Generate Review File
    bib_str = "\n".join(bibs)
    p_review_dir = doc_path if doc_path.is_dir() else doc_path.parent
    bib_name = f"{first_hash[:6]}-{n_survivors:02d}-import.bib"
    p_review = p_review_dir / bib_name
    p_review.write_text(bib_str, encoding='utf-8')

    click.secho(f"\nMetadata extracted. Review file created at: {p_review.name}", fg="green")

    # 5. Enhance if requested
    if enhance:
        click.echo("Running automatic enhancement...")
        _enhance_bibtex_file(p_review, arxiv=True)

    # 6. Open Editor for review (always)
    try:
        editor = lib.config.editor_command
        # Note: we assume the editor supports -w for waiting if it's a CLI wrapper like subl or code
        subprocess.run([editor, "-w", str(p_review)], check=False)
    except Exception as e:
        logger.debug(f"Editor launch error: {e}")
        click.echo(f"Could not open editor ({editor}) automatically. Please review the .bib file manually.")

    if execute:
        if click.confirm(f"Continue to import {p_review.name} with {len(actual_survivors)} entries?", default=False):
            ctx.invoke(
                import_bibtex,
                bibtex_path=p_review,
                doc_dir=p_review.parent,
                execute=True,
                add_hashes=True,
                incremental=True,
                verbose=0,
                extract_text_flag=True
            )
    else:
        click.echo("\nRun the following command to complete the import after review:")
        click.secho(f"archivum import-bibtex {p_review.name} -x", fg="cyan", bold=True)


# ========================================================================================
@entry.command(name="stage-enhance")
@click.argument(
    "bibtex_file",
    type=click.Path(exists=True, path_type=Path),
    required=False,
)
@click.option(
    "-a",
    "--arxiv",
    is_flag=True,
    default=True,
    show_default=True,
    help="Perform arXiv-to-Crossref enhancement for preprints.",
)
def stage_enhance(bibtex_file: Path, arxiv: bool):
    """
    Enhance a staged BibTeX file with metadata from Crossref.

    \b
    1. Identifies entries with 'archivePrefix = {arXiv}' but no 'journal'.
    2. Searches Crossref by title to find published versions.
    3. Updates journal, volume, year, and DOI while keeping arXiv info.
    4. Backs up the original file as .bak.
    """
    if bibtex_file is None:
        bibtex_file = Path(".")

    if bibtex_file.is_dir():
        bib_files = list(bibtex_file.glob("*.bib"))
        if len(bib_files) == 1:
            bibtex_file = bib_files[0]
        elif not bib_files:
            click.echo(f"No .bib files found in {bibtex_file}")
            return
        else:
            click.echo(f"Multiple .bib files found in {bibtex_file}. Please specify one.")
            for f in bib_files:
                click.echo(f"  - {f.name}")
            return

    if not bibtex_file.suffix.lower() == ".bib":
        click.echo(f"File {bibtex_file} is not a BibTeX file.")
        return

    import shutil

    backup_path = bibtex_file.with_suffix(bibtex_file.suffix + ".bak")
    shutil.copy2(bibtex_file, backup_path)
    click.echo(f"Backup created at {backup_path.name}")

    # We use _enhance_bibtex_file helper
    enhanced_count = _enhance_bibtex_file(bibtex_file, arxiv=arxiv)

    click.secho(
        f"\nDone! Enhanced {enhanced_count} entries.", fg="green", bold=True
    )

    lib = LibraryContext.get()
    try:
        editor = lib.config.editor_command if not lib.is_empty else "subl"
        # Open both files for comparison
        subprocess.run([editor, str(backup_path), str(bibtex_file)], check=False)
    except Exception as e:
        logger.debug(f"Editor launch error: {e}")
        click.echo(f"Could not open editor automatically. Files for review: {bibtex_file.name}, {backup_path.name}")


@entry.command(name="extract-text")
@click.option(
    "-m",
    "--missing",
    is_flag=True,
    help="Print which docs are missing their text.",
)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    help="Actually perform the extraction/cleaning work.",
)
@click.option(
    "-c",
    "--clean",
    is_flag=True,
    help="Find (and delete if -x) text files with no corresponding document.",
)
@click.option(
    "-i",
    "--info",
    is_flag=True,
    help="Print info about the text: number of docs, number with text files etc.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force re-extraction even if the text file already exists.",
)
@click.option(
    "-w",
    "--workers",
    type=int,
    default=8,
    help="Number of worker threads to use.",
)
def extract_text(missing, execute, clean, info, force, workers):
    """Manage text extraction for documents in the library."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    if info:
        click.secho(f"Text extraction info for {lib.name}:", fg="cyan", bold=True)
        summary = lib.get_text_info()
        if summary.empty:
            click.echo("No documents in library.")
        else:
            qd(summary.reset_index())

    if missing:
        click.secho(f"Documents missing text extracts in {lib.name}:", fg="yellow", bold=True)
        # We can reuse get_text_info logic or just find them
        lib.update_hashes()
        missing_docs = []
        for _, row in lib.doc_df.iterrows():
            p = Path(row.path)
            doc = Document(p)
            doc.hash = row.hash
            if not doc.text_exists(lib.text_dir_path, lib.config.extractor):
                missing_docs.append({
                    "tag": row.get('tag', 'N/A'),
                    "name": p.name,
                    "suffix": p.suffix.lower()
                })

        if not missing_docs:
            click.echo("No missing text extracts.")
        else:
            qd(pd.DataFrame(missing_docs))

    if clean:
        if execute:
            click.secho("Cleaning orphaned text extracts...", fg="red", bold=True)
        else:
            click.secho("Auditing orphaned text extracts (DRY RUN)...", fg="cyan")
        orphans = lib.clean_text_extracts(execute=execute)
        if not orphans:
            click.echo("No orphaned text files found.")
        else:
            if not execute:
                click.echo(f"Found {len(orphans)} orphaned files. Use -x to delete.")

    # Always attempt extraction only if -x is set and no other "pure report" flag was requested
    # OR if they specifically want to execute the missing ones.
    if execute:
        # If -c was set, it's already handled above
        # If -m or -i were set, we still might want to run extraction
        # User said: "-x actually do work (default False, safeguard)"
        click.echo(f"Starting full-text indexing for library: {lib.name}")
        lib.extract_all_text(force=force, workers=workers, execute=True)
    elif not (info or missing or clean):
        # If just 'extract-text' with no flags, show info as default?
        click.echo("No action specified. Use -i for info, -m for missing, -c for clean, or -x to execute extraction.")


# ========================================================================================
@entry.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=True, path_type=Path))
def find_doc(path):
    """Hash a document (or directory) and find matching records in the library."""
    lib = LibraryContext.get()

    # 1. Collect files
    if path.is_dir():
        if lib.is_empty:
            click.echo("No library open. Cannot scan directory for matches.")
            return
        files = lib.find_docs(path)
        if not files:
            click.echo(f"No documents found in {path}")
            return
        click.echo(f"Scanning {len(files)} files in {path}...")
    else:
        files = [path]

    if lib.is_empty:
        # Fallback to just hashing if no library is open (only for single file)
        from .hasher import blake3b_hash
        h = blake3b_hash(path)
        click.echo(f"Hash: {h[:12]}")
        return

    all_results = []
    for p in files:
        try:
            h, matches = lib.find(p)
            if not matches.empty:
                # We need to join with ref_doc and ref to get tags and titles
                res = matches.merge(lib.ref_doc_df, on=['hash', 'version'], how='left')
                res = res.merge(lib.ref_df, on='tag', how='left')
                res.insert(0, 'filename', p.name)
                all_results.append(res)
            elif not path.is_dir():
                click.echo(f"Hash: {h[:12]}")
                click.secho("No matching records found in library.", fg="yellow")
        except Exception as e:
            click.echo(f"Error processing {p.name}: {e}")

    if all_results:
        final_df = pd.concat(all_results, ignore_index=True)
        if path.is_dir():
            click.secho(f"\nFound matches for {final_df.filename.nunique()} files:", fg="green")
            # user asked for filename as first column
            cols = ['filename', 'tag', 'author', 'title', 'year']
            # only keep columns that exist
            cols = [c for c in cols if c in final_df.columns]
            qd(final_df[cols])
        else:
            # behave as before for single file
            click.echo(f"Hash: {all_results[0].hash.iloc[0][:12]}")
            click.secho(f"Found {len(all_results[0])} matching records:", fg="green")
            qd(all_results[0][['tag', 'author', 'title', 'year', 'path']])
    elif path.is_dir():
        click.echo("No matching records found for any files in directory.")


@entry.command(
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    }
)
@click.pass_context
def rg(ctx):
    """Run ripgrep with all arguments passed through unchanged."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo(
            "No library open...don't know where to look for text files. Returning"
        )
        return

    raw_args = list(ctx.args)
    if not raw_args:
        click.echo("Missing ripgrep arguments!", err=True)
        return

    tools = RipgrepTools(
        console=console,
        text_dir=Path(lib.text_dir_full_name),
        extractor=getattr(lib.config, "extractor", None),
    )
    tools.run_and_present(raw_args)


@entry.command(context_settings={"ignore_unknown_options": True})
# @click.argument("pattern", type=str, required=True)
@click.option(
    "-n",
    default=-1,
    type=int,
    show_default=True,
    help="Number of results to return, n=-1 (default) returns all.",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def rg_old(args, n):
    """Run ripgrep (rg) with given pattern and args against text extracts from pdfs."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo(
            "No library open...don't know where to look for text files. Returning"
        )
        return
    # library runs the query, cli prints it out
    if not args:
        click.echo("Missing pattern!", err=True)
    pattern = args[0]
    args = ["--json"] + list(args[1:])
    return_value, proc = lib.run_ripgrep(pattern, args)
    if return_value == "FileNotFoundError":
        console.print(proc)
    elif return_value == "None":
        console.print("[red]Failed to read rg output[/red]")
    elif return_value:
        console.print("OTHER MYSTERIOUS ERROR??")

    prefix = str(lib.text_dir_full_name)
    print(lib)
    try:
        suffix = f".{lib.config.extractor}.md"
    except AttributeError:
        print("ATTRIBUTE ERROR...RETURNING")
        return
    last_file = ""
    fc = 0
    for line in proc.stdout:
        try:
            result = json.loads(line)
            if result.get("type") == "match":
                n -= 1
                if n == 0:
                    break
                file = result["data"]["path"]["text"]
                new_file = file != last_file
                if new_file:
                    console.print("")  # between files
                    # new file
                    fc = 0
                    last_file = file
                styled = Text()
                if new_file:
                    file = file.replace(prefix, "").replace(suffix, "")
                    styled.append(f"{file}\n", style="bold cyan")
                fc += 1
                line_num = result["data"]["line_number"]
                line_text = result["data"]["lines"]["text"].rstrip()
                text = Text(line_text, style="blue")
                # color matches
                for sub in result["data"].get("submatches", []):
                    start = sub["start"]
                    end = sub["end"]
                    text.stylize("bold red", start, end)

                styled.append(f"[m{fc:02d}@.{line_num:05d}]: ", style="bold cyan")
                styled.append(text)
                styled.append("\n")
                console.print(styled, end="")
        except json.JSONDecodeError:
            console.print("ERROR " + line.strip(), style="dim")

    return


# tags opening docs ------------------------
@entry.command()
@click.argument("tag_regex", type=str)
@click.option(
    "-i",
    "--information",
    is_flag=True,
    default=True,
    show_default=True,
    help="Show information about matching entries.",
)
@click.option(
    "-o",
    "--open",
    "open_doc",
    is_flag=True,
    help="Open preferred document(s) for the matched tags.",
)
@click.option(
    "-a",
    "--all-docs",
    is_flag=True,
    default=False,
    help="Open all documents associated with the matched tags.",
)
@click.option(
    "-l",
    "--limit",
    type=int,
    default=5,
    show_default=True,
    help="Maximum number of documents to open.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    default=0,
    show_default=True,
    help="Verbosity: none (Basic), -v (BibTeX + Docs), -vv (Full Stats).",
)
@click.option(
    "-f",
    "--file",
    "show_file",
    is_flag=True,
    help="Show the file name associated with a tag instead of the title.",
)
def tag(tag_regex, information, open_doc, all_docs, limit, verbose, show_file):
    """Get documents or information by tag (supports Regex)."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    # 1. Find References using Regex
    try:
        mask = lib.ref_df.tag.str.contains(tag_regex, regex=True, na=False, case=False)
        ref_matches = lib.ref_df[mask]
    except Exception as e:
        click.echo(f"Regex error: {e}")
        return

    if ref_matches.empty:
        click.echo(f"No references found matching: {tag_regex}")
        return

    matched_tags = ref_matches.tag.tolist()
    if len(ref_matches) > 1:
        click.secho(f"Found {len(ref_matches)} references matching regex.", fg="cyan")

    # 2. Find Associated Documents
    doc_links = lib.ref_doc_df[lib.ref_doc_df.tag.isin(matched_tags)]
    doc_details = doc_links.merge(lib.doc_df, on=['hash', 'version'], how='inner')

    # 3. Show Information
    if information:
        # Default: Basic
        if verbose == 0:
            # Join with details for summary
            if show_file:
                summary = ref_matches[['tag']].copy()
                # map name
                name_map = doc_details.groupby('tag')['name'].first().to_dict()
                summary['filename'] = summary['tag'].map(lambda x: name_map.get(x, "No Doc"))
            else:
                summary = ref_matches[['tag', 'title']].copy()

            # map hash
            hash_map = doc_details.groupby('tag')['hash'].first().str[:12].to_dict()
            summary['hash'] = summary['tag'].map(lambda x: hash_map.get(x, "No Doc"))
            qd(summary)

        # -v: BibTeX + Docs
        elif verbose == 1:
            for t in matched_tags:
                ref = ref_matches[ref_matches.tag == t]
                click.secho(f"\n--- Reference Metadata [{t}] ---", fg="cyan")
                metadata = ref.iloc[0].dropna()
                metadata = metadata[metadata != ""]
                qd(metadata.to_frame(name="value"), show_index=True)

                # Docs for this specific tag
                this_docs = doc_details[doc_details.tag == t]
                if not this_docs.empty:
                    click.secho(f"Linked Documents ({len(this_docs)}):", fg="cyan")
                    cols = ['name', 'create', 'hash', 'size']
                    this_docs_view = this_docs.copy()
                    this_docs_view['hash'] = this_docs_view['hash'].str[:12]
                    qd(this_docs_view[cols])

        # -vv: Full Stats
        else:
            click.secho(f"\n--- Full Records for Matches ---", fg="magenta")
            qd(ref_matches.T)
            click.secho(f"\n--- Full Document Details ---", fg="magenta")
            qd(doc_details)

    # 4. Open Document
    if open_doc or all_docs:
        if doc_details.empty:
            click.echo("No documents found to open.")
            return

        if all_docs:
            to_open = doc_details.path.unique().tolist()
        else:
            # Smart Open Logic:
            # 1. Try exact tag match first (case-insensitive)
            exact_match = ref_matches[ref_matches.tag.str.lower() == tag_regex.lower()]

            if not exact_match.empty:
                # Open only the exact match
                target_tag = exact_match.tag.iloc[0]
                to_open = doc_details[doc_details.tag == target_tag].path.unique().tolist()
            else:
                # 2. No exact match, handle regex results
                all_matched_tags = doc_details.tag.unique().tolist()
                target_tag = all_matched_tags[0]
                to_open = doc_details[doc_details.tag == target_tag].path.unique().tolist()

                if len(all_matched_tags) > 1:
                    # Warn about other matches
                    others = all_matched_tags[1:]
                    click.secho(f"Warning: Multiple matches found for '{tag_regex}'.", fg="yellow")
                    click.secho(f"Opening first match: {target_tag}", fg="green")

                    # Group by tag to get the first filename for each other tag
                    other_files = doc_details[doc_details.tag.isin(others)].groupby('tag')['name'].first().to_dict()
                    click.echo("Other matching documents:")
                    for other_tag, other_file in other_files.items():
                        click.echo(f"  - {other_tag}: {other_file}")

        # Apply Limit
        if len(to_open) > limit:
            click.secho(f"Warning: Found {len(to_open)} files, but limit is {limit}. Only opening first {limit}.", fg="yellow")
            to_open = to_open[:limit]

        for d in to_open:
            click.echo(f"Opening: {Path(d).name}")
            lib.open_document(d)

@entry.command()
@click.argument("hash_str", type=str)
@click.option(
    "-o",
    "--open",
    "open_doc",
    is_flag=True,
    help="Open the document associated with the hash.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    default=0,
    show_default=True,
    help="Verbosity: none (Basic), -v (Detailed Ref Info).",
)
def hash(hash_str, open_doc, verbose):
    """Get references and information by file hash (supports Regex)."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    # 1. Find Documents with this hash (anchored to start)
    try:
        mask = lib.doc_df.hash.str.match(hash_str, na=False, case=False)
        doc_matches = lib.doc_df[mask]
    except Exception as e:
        click.echo(f"Regex error: {e}")
        return

    if doc_matches.empty:
        click.echo(f"No documents found with hash matching: {hash_str}")
        return

    # 2. Find Linked Tags
    hashes = doc_matches.hash.tolist()
    links = lib.ref_doc_df[lib.ref_doc_df.hash.isin(hashes)]
    tags = links.tag.unique().tolist()

    if not tags:
        click.secho(f"\nFound {len(doc_matches)} files but NO references are linked to them (Orphans).", fg="yellow", bold=True)
        # Always show detailed info if no ref is found
        doc_matches_view = doc_matches.copy()
        doc_matches_view['hash'] = doc_matches_view['hash'].str[:12]
        qd(doc_matches_view[['name', 'hash', 'size', 'suffix', 'mod']])
    else:
        # 3. Show Reference Info
        # Join doc_df hash back to refs for display
        refs = lib.ref_df[lib.ref_df.tag.isin(tags)].copy()

        # We need a mapping of tag -> short_hash
        tag_hash_map = links.merge(doc_matches[['hash', 'version']], on=['hash', 'version'])
        tag_hash_map = tag_hash_map.groupby('tag')['hash'].first().str[:12].to_dict()

        refs['hash_12'] = refs['tag'].map(tag_hash_map)

        click.secho(f"Found {len(tags)} references linked to hash {hash_str[:12]}...", fg="cyan")

        if verbose == 0:
            qd(refs[['tag', 'author', 'title', 'year', 'hash_12']])
        else:
            for _, row in refs.iterrows():
                click.secho(f"\n--- {row.tag} ---", fg="yellow")
                metadata = row.dropna()
                metadata = metadata[metadata != ""]
                qd(metadata.to_frame(name="value"), show_index=True)

    # 4. Open
    if open_doc:
        for n, row in doc_matches.iterrows():
            p = Path(row.path)
            click.echo(f"Opening: {Path(p).name}")
            lib.open_document(p)


@entry.command(name="link-tag-hash")
@click.argument("tag", type=str)
@click.argument("file_hash", type=str)
@click.option("-v", "--version", type=int, default=0, help="Version number of the hash (default 0).")
def link_tag_hash(tag, file_hash, version):
    """Manually link an existing tag reference to an existing document by hash and version."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open.")
        return

    try:
        # Support partial hash if unique
        if len(file_hash) < 64:
            matches = lib.doc_df[lib.doc_df.hash.str.startswith(file_hash.upper())]
            if len(matches) == 0:
                click.echo(f"No document found starting with hash {file_hash}")
                return
            elif len(matches.hash.unique()) > 1:
                click.echo(f"Ambiguous hash {file_hash}, multiple matches found.")
                return
            file_hash = matches.hash.iloc[0]

        if lib.link_document(tag, file_hash, version):
            click.secho(f"Successfully linked {tag} to {file_hash[:12]} (v{version})", fg="green")
        else:
            click.echo("Link operation skipped (already exists).")
    except Exception as e:
        click.echo(f"Error: {e}")


@entry.command(name="link-doc")
@click.argument("hash_str", type=str)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    show_default=True,
    help="Actually perform the import after editing.",
)
def link_hash(hash_str, execute):
    """
    \b
    Create a new reference for a document orphan.

    1. Finds unique document by hash.
    2. Runs discovery to extract metadata.
    3. Opens generated BibTeX in Sublime Text for editing.
    4. Imports the edited reference back into the library.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    # 1. Find the Document
    mask = lib.doc_df.hash.str.match(hash_str, na=False, case=False)
    matches = lib.doc_df[mask]

    if matches.empty:
        click.echo(f"No document found with hash starting with: {hash_str}")
        return

    if len(matches) > 1:
        click.secho(f"Ambiguous hash! Found {len(matches)} matches:", fg="red")
        matches_view = matches.copy()
        matches_view['hash'] = matches_view['hash'].str[:12]
        qd(matches_view[['name', 'hash', 'size']])
        return

    row = matches.iloc[0]
    doc_path = lib.abspath(row.path)
    click.secho(f"Found document: {doc_path.name}", fg="cyan")

    # Check if already linked
    existing_links = lib.ref_doc_df[
        (lib.ref_doc_df.hash == row.hash) & (lib.ref_doc_df.version == row.version)
    ]
    if not existing_links.empty:
        tags = existing_links.tag.unique()
        click.secho(f"Warning: This document is already linked to: {', '.join(tags)}", fg="yellow")
        if not click.confirm("Do you want to create another reference for it?", default=False):
            return

    # 2. Run Discovery
    from .document import Document
    doc = Document(doc_path)
    doc.hash = row.hash

    click.echo("Running metadata discovery...")
    doc.process()

    bib_text = doc.bibtex()
    if not bib_text:
        # Fallback if discovery failed completely
        from .utilities import generate_tag
        tag = generate_tag(doc.bib.get('author', 'Unknown'), doc.bib.get('year', '2099'))
        bib_text = f"@article{{{tag},\n  title = {{{doc_path.stem}}},\n  author = {{Unknown}},\n  year = {{2099}},\n  file = {{{doc.bibtex().split('file = {')[-1].split('}')[0] if 'file = {' in doc.bibtex() else ''}}}\n}}"

    # 3. Open in Editor
    temp_bib = lib.debug_dir_path / f"link-doc-{row.hash[:8]}.bib"
    temp_bib.parent.mkdir(parents=True, exist_ok=True)
    temp_bib.write_text(bib_text, encoding="utf-8")

    click.echo(f"Opening BibTeX in editor: {temp_bib.name}")
    try:
        # Using configured editor command
        editor = lib.config.editor_command
        subprocess.run([editor, "-w", str(temp_bib)], check=True)
    except Exception as e:
        click.echo(f"Error opening editor ({lib.config.editor_command}): {e}")
        return

    # 4. Import
    if click.confirm("\nImport the edited BibTeX entry?", default=True):
        from .import_bibtex import Bib2df_Incremental

        # We initialize with doc_dir=None to prevent a directory scan.
        # Instead, we manually provide the doc_df for ONLY the file we are linking.
        importer = Bib2df_Incremental(
            bibtex_file_path=temp_bib,
            doc_dir=None,
            reference_library=lib,
            add_hashes=True, # Verify hash matches
            incremental=True
        )
        # Pre-populate with ONLY the document record we found
        importer._doc_df = pd.DataFrame([row])

        import_df = importer.import_bibtex_file()
        qd(import_df)

        if execute:
            click.echo(f"Updating library with new reference.")
            importer.update_library(save=True)
            # No need to extract text as it's already an orphan in the library
        else:
            click.secho("Dry run complete. Use -x to apply changes.", fg="yellow")

    # Clean up temp file
    if temp_bib.exists():
        temp_bib.unlink()


# ========================================================================================

# from prompt_toolkit.completion import Completer, Completion, FuzzyCompleter, WordCompleter, DynamicCompleter

# # 2. Define the Custom Completer
# class AllTitlesCompleter(Completer):
#     """
#     Yields ALL titles unconditionally.
#     Allows FuzzyCompleter to handle the filtering/sorting logic entirely,
#     enabling matching across spaces (e.g. 'risk ins' -> 'Risk Insurance').
#     """
#     def get_completions(self, document, complete_event):
#         titles = get_library_titles()
#         # We pass the full text before cursor to ensure we are matching
#         # against the entire buffer if needed, but FuzzyCompleter usually
#         # handles the filtering. We just yield everything.
#         for title in titles:
#             yield Completion(title, start_position=0)


@entry.command()
@click.argument("title", nargs=-1, required=True, type=str)
@click.option(
    "-a", "--all-docs", is_flag=True, show_default=True, help="Open all docs if more than one match."
)
def title(title, all_docs):
    """Open a document by its title with fuzzy search (no spaces!)."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo(
            "No library open...don't know where to look for text files. Returning"
        )
        return
    if lib.ref_df.empty:
        click.echo("No referenced documents. Returning")
        return

    # title comes in as a tuple: ('My', 'Paper', 'Title')
    title = " ".join(title)
    df = lib.ref_df.query("title == @title")
    if len(df) == 0:
        click.echo("No matching documents found to %s", title)
    if not all_docs:
        df = df.iloc[:1]
    if all_docs and len(df) > 5:
        click.echo("Found %s docs, just opening first 5", len(df))
        df = df.iloc[:5]
    doc_tags = df.tag.to_list()  # noqa
    df2 = lib.ref_doc_df.query("tag in @doc_tags")
    for d in df2.path:
        lib.open_document(d)


@entry.command()
@click.argument("title", nargs=-1, required=True, type=str)
@click.option(
    "-a", "--all-docs", is_flag=True, show_default=True, help="Open all docs if more than one match."
)
def tt(title, all_docs):
    """Open a document by its tag and title with fuzzy search (no spaces!)."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo(
            "No library open...don't know where to look for text files. Returning"
        )
        return
    if lib.ref_df.empty:
        click.echo("No referenced documents. Returning")
        return

    # title comes in as a tuple: ('My', 'Paper', 'Title')
    title = " ".join(title)
    tag, title = title.split("-")
    df = lib.ref_df.query("title == @title and tag == @tag")
    if len(df) == 0:
        click.echo("No matching documents found to %s", title)
    if not all_docs:
        df = df.iloc[:1]
    if all_docs and len(df) > 5:
        click.echo("Found %s docs, just opening first 5", len(df))
        df = df.iloc[:5]
    doc_tags = df.tag.to_list()  # noqa
    df2 = lib.ref_doc_df.query("tag in @doc_tags")
    for d in df2.path:
        lib.open_document(d)


@entry.command()
@click.argument(
    "qmd_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o", "--output",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    help="Alternative output BibTeX file name."
)
def qmd_bibtex(qmd_file, output):
    """Extract citations from a QMD file and create a BibTeX file from library matches."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open... cannot match citations. Returning")
        return

    qmd_path = Path(qmd_file)
    if output:
        out_file = Path(output)
    else:
        out_file = qmd_path.with_suffix(".bib")

    qmd = QmdParser(qmd_path)
    count = qmd.generate_bibtex(lib, out_file)

    if count > 0:
        click.echo(f"Successfully wrote {count} references to {out_file}")
    else:
        click.echo("No matching citations found in the library.")


@entry.command()
@click.argument(
    "qmd_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "out_path",
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "-a", "--abstract", is_flag=True, default=False, show_default=True,
    help="Include abstract from text extract."
)
@click.option("-x", "--execute", is_flag=True, default=False, show_default=True,
    help="Actually execute."
)
def qmd_ref_summary(qmd_file, out_path, abstract, execute):
    """
    Produce qmd summary of qmd_file in directory out_path.

    Makes links to all pdf files.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo(
            "No library open...don't know where to look for text files. Returning"
        )
        return
    qmd = QmdParser(qmd_file)
    ans = qmd.ref_summary(out_path, lib, abstract=abstract, execute=execute)


# =================================================
# Uber using Gemini new technology Nov 2025
@entry.command()
@click.option("-l", "--lib-name", type=str, default="", show_default=True, help="Open library.")
@click.option(
    "-a",
    "--auto-open",
    is_flag=True,
    show_default=True,
    help="If true, auto open the default library.",
)
@click.option("-d", "--debug", is_flag=True, show_default=True)
def uber(lib_name="", auto_open=True, debug=False):
    """QT Standalone Shell. Optionally open library."""
    shell = UberShell("archivum", debug)

    # logging
    if debug:
        logger_config = "logging-debug-file.yaml"

        with (files("archivum.configurations") / logger_config).open("r") as f:
            cfg = yaml.safe_load(f)
        logging.config.dictConfig(cfg)
        test_logger = logging.getLogger("archivum.TEST")
        test_logger.debug("Uber logger test @ DEBUG")
        test_logger.info("Uber logger test @ INFO")
        test_logger.warning("Uber logger test @ WARNING")
        test_logger.error("Uber logger test @ ERROR")

    # figure completers
    completers = {}
    completers["open"] = DynamicCompleter(lambda: WordCompleter(Library.list()))
    # completers['tag'] = FuzzyCompleter(WordCompleter(LibraryContext.get_library_tags, ignore_case=True))
    # completers['title'] = FuzzyCompleter(WordCompleter(LibraryContext.get_library_titles, ignore_case=True,
    #                                         sentence=True, WORD=False, match_middle=True))
    # completers['tt'] = FuzzyCompleter(WordCompleter(LibraryContext.get_library_tag_titles, ignore_case=True,
    #                                         sentence=True, WORD=False, match_middle=True))
    # completers['title'] = FuzzyCompleter(AllTitlesCompleter())
    completers["tag"] = RustFuzzyCompleter(LibraryContext.get_library_tags)
    completers["f"] = RustFuzzyCompleter(LibraryContext.get_library_tags)
    completers["edit-tag"] = RustFuzzyCompleter(LibraryContext.get_library_tags)
    completers["delete-tag"] = RustFuzzyCompleter(LibraryContext.get_library_tags)
    completers["title"] = RustFuzzyCompleter(LibraryContext.get_library_titles)
    completers["tt"] = RustFuzzyCompleter(LibraryContext.get_library_tag_titles)
    completers["hash"] = RustFuzzyCompleter(LibraryContext.get_library_hashes)
    completers["link-doc"] = RustFuzzyCompleter(LibraryContext.get_library_hashes)

    query_completer = WordCompleter(
        ["-d", "--database", "doc", "ref", "ref-doc", "database"],
        ignore_case=True,
        sentence=False,  # Typically set to False for shell-style commands/options
    )

    database_choices = WordCompleter(["doc", "ref", "ref-doc", "database"])
    query_completer = NestedCompleter.from_nested_dict(
        {
            "-d": database_choices,
            "--database": database_choices,
            # Fallback (None): If the user doesn't type -d or --database,
            # fall back to the generic completer for the 'start' argument.
            # You should replace 'None' with a completer for the 'start' argument,
            # for example, a FuzzyCompleter of common query terms or column names.
            # For simplicity, we use None to allow free typing for now.
            # None: None,
        }
    )
    completers["query"] = query_completer
    completers["q"] = query_completer
    completers["import-bibtex"] = PathCompleter(only_directories=False, expanduser=True)
    completers["stage-docs"] = PathCompleter(only_directories=False, expanduser=True)
    completers["stage-enhance"] = PathCompleter(only_directories=False, expanduser=True)
    completers["qmd-bibtex"] = PathCompleter(only_directories=False, expanduser=True)
    completers["qmd-ref-summary"] = PathCompleter(only_directories=False, expanduser=True)

    # Register QT commands, exclude 'uber' to prevent recursion
    shell.register_click_group(entry, exclude=["uber"], completers=completers)

    def prompt_function():
        """Prompt uses breadcrumb chain from UBERSHELL_CHAIN plus current library."""
        lib = LibraryContext.get()
        
        # Check for external changes before rendering the prompt
        if not lib.is_empty and lib.needs_reload:
            click.secho("\n[External changes detected. Reloading library...]", fg="yellow", bold=True)
            lib.reset()
            LibraryContext.refresh()
            # Note: RustFuzzyCompleter uses get_candidates_func which calls 
            # LibraryContext.get_library_tags etc. Since we just cleared the 
            # candidates in refresh(), they will be rebuilt on next tab press.

        chain = os.environ.get("UBERSHELL_CHAIN", shell.prompt_label)
        return HTML(
            f"<ansired>{chain} <ansigreen>[{lib.name}]</ansigreen> > </ansired>"
        )

    if lib_name == "" and auto_open:
        lib_name = DEFAULT_LIBRARY

    lib = None
    if lib_name != "":
        try:
            lib = Library(lib_name)
            LibraryContext.set(lib)
            lib.start_watcher()
            logger.info(
                f"Opened {lib.config.name}, loaded {len(lib.ref_df):,d} references."
            )
        except Exception as e:
            logger.error("Open library error: %s", e)

    try:
        shell.start(prompt_function=prompt_function)
    finally:
        # Ensure watcher is stopped on exit
        current_lib = LibraryContext.get()
        if not current_lib.is_empty:
            current_lib.stop_watcher()


if __name__ == "__main__":
    # to facilitate performance logging
    # run python -m cProfile -o perf.prof -m archivum.cli
    # recent top 10 !/Boonen|Tsanakas|Wang, R/
    entry()
