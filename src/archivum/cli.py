"""Implement command line interface for archivum."""
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

# for uber loop
from uber_shell import UberShell  # type: ignore[import-untyped]
from rustfuzz import FuzzyMatcherMultiHi  # type: ignore[import-untyped]
from querexfuzz.core import querexfuzz_help  # type: ignore[import-untyped]

from .library import Library
from .document import Document  # type: ignore[import-untyped]
from . import DEFAULT_LIBRARY, EMPTY_LIBRARY, LIBRARIES_DIR, BASE_DIR, DOC_STORE_DIR
from .utilities import make_qd
from .config import Configurator
from .crossref import lookup_doi, search_by_title, search
from .bibtex import dict_to_bibtex, dict_to_bibtex_crossref
from .import_bibtex import Bib2df_Incremental


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
            return cls.no_library
        return cls.current

    @classmethod
    def clear(cls):  # noqa
        logger.debug("Library %s closed.", cls.current)
        cls.current = None
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
        """Fetch unique tags from the current library context."""
        if cls.candidates_titles is not None:
            return cls.candidates_titles, cls.matcher_titles
        if cls.current is None or cls.current == EMPTY_LIBRARY:
            return []
        else:
            cls.candidates_titles = cls.current.all_titles
            cls.matcher_titles = FuzzyMatcherMultiHi(cls.candidates_titles)
            return cls.candidates_tags, cls.matcher_titles

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
        logger.error("get prompt error", e, sep="\n")
        return HTML(f"ERR: <ansiyellow>{cmd} > </ansiyellow>")


# ========================================================================================
# ========================================================================================


# Helper
def _open_document(d):
    """Try to open document at path d."""
    # assume windows knows what to do
    p = Path(d)
    if not p.exists():
        logger.info("file %s not found", p.name)
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


# ========================================================================================
@entry.command()
@click.option(
    "-t",
    "--task",
    type=click.Choice(["sharding", "rebase", "missing"]),
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
@click.option(
    "--new-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="New root directory for the 'rebase' task.",
)
def validate(task, execute, new_root):
    """Audit and fix library structure."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return

    if execute:
        click.secho(f"EXECUTING: {task}...", fg="red", bold=True)
    else:
        click.secho(f"DRY RUN: {task} audit...", fg="cyan")

    report = lib.validate(task=task, execute=execute, new_root=new_root)

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
def edit(tag):
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

    # 2. Convert to BibTeX
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
    default="sharding",
    show_default=True,
    help="Actually execute.")
def delete(tag, execute):
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
def rename_library(old_name, new_name):
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
def copy_library(old_name, new_name):
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
def open(lib_name):
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
def explorer():
    """Open explorer to see files at the location of the open library, if any."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...nothing to save. Returning")
        return
    subprocess.run(f"start explorer {lib.config_path.absolute()}", shell=True)


# ========================================================================================
@entry.command()
def save():
    """Save the current library to disk."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...nothing to save. Returning")
        return
    lib.save()
    click.echo(f"{lib.name} saved")


# ========================================================================================
@entry.command()
def close():
    """
    Close the currently open library.

    This is a command line concept; the Library class has no close
    method. You just delete it. It does NOT track if it is dirty and
    needs to change.
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
def create(lib_name):
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
def history():
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
    session = PromptSession(completer=base_completer)

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
                        _open_document(d)
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

    Priority:
    1. DOI (if provided)
    2. Title (if provided without author/keywords)
    3. Generic Search (using provided keywords, title, author)
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
    "-x",
    "--execute",
    is_flag=True,
    show_default=True,
    help="Actually perform the import; otherwise, do a dry run and report stats.",
)
def import_bibtex(bibtex_path: Path, doc_dir: Path, add_hashes: bool, incremental: bool, verbose: int, execute: bool):
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
            click.echo(f"Error: Directory must contain exactly one .bib file. Found {len(bibs)} in {search_dir}")
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


# ========================================================================================
@entry.command()
def config():
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
@entry.command(name="import-doc")
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
    show_default=True,
    help="Actually perform the final import after review.",
)
def import_doc(doc_path: Path, flag_duplicates: bool, delete_dupes: bool, verbose: int, execute: bool):
    """
    Prepare new documents for import.

    1. Checks for hash duplicates in the library.
    2. Extracts metadata from new files.
    3. Generates a .bib file for review in Sublime Text.
    4. Optionally imports the reviewed file.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot import.")
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
    survivors = doc_paths
    if flag_duplicates:
        click.echo(f"Checking {len(doc_paths)} files for duplicates...")
        from .hasher import hash_many3
        hashes = hash_many3(doc_paths, workers=lib.config.hash_workers)

        dupe_rows = []
        new_paths = []

        lib_docs = lib.doc_df
        lib_ref_docs = lib.ref_doc_df
        lib_refs = lib.ref_df

        for p in doc_paths:
            h = hashes.get(p)
            if h and h in lib_docs.hash.values:
                # Find matching info
                match_paths = lib_docs[lib_docs.hash == h].path.tolist()
                match_tags = lib_ref_docs[lib_ref_docs.path.isin(match_paths)].tag.tolist()
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

            survivors = new_paths
        else:
            click.echo("No duplicates found.")

    if not survivors:
        click.echo("No new documents to process.")
        return

    click.echo(f'Processing {len(survivors)} documents...')

    # 3. Process the Survivors
    bibs = [f"% import from {doc_path.absolute()}"]
    for p in sorted(survivors):
        if p.suffix.lower() not in {'.pdf', '.djvu'}:
            click.echo(f'WARNING: Non-standard format {p.suffix} for {p.name}')
        try:
            logger.info('gathering import info for %s', p)
            doc = Document(p)
            doc.process()
            bibs.append(doc.bibtex())
        except Exception as e:
            click.echo(f"Error extracting metadata for {p.name}: {e}")

    # 4. Generate Review File
    bib_str = "\n".join(bibs)
    p_review = doc_path if doc_path.is_dir() else doc_path.parent
    p_review = p_review / "bibtex-import.bib"
    p_review.write_text(bib_str, encoding='utf-8')

    click.echo(f"\nMetadata extracted. Review file created at: {p_review.name}")

    try:
        # Windows specific call to Sublime
        subprocess.run(f'"c:\\program files\\sublime text\\subl.exe" "{p_review}"', shell=False)
    except Exception:
        click.echo("Could not open Sublime Text automatically. Please review the .bib file manually.")

    # 5. Final Step
    if click.confirm("\nContinue to import reviewed references?", default=False):
        from .import_bibtex import Bib2df_Incremental
        b = Bib2df_Incremental(
            bibtex_file_path=p_review,
            doc_dir=doc_path if doc_path.is_dir() else doc_path.parent,
            reference_library=lib,
            incremental=True # Always incremental for doc-import
        )
        import_df = b.import_bibtex_file()
        qd(import_df)

        if execute:
            b.update_library(save=True)
            click.echo("Library updated.")
        else:
            click.echo("Dry run complete. Use --execute to commit changes.")


# ========================================================================================
@entry.command(context_settings={"ignore_unknown_options": True})
# @click.argument("pattern", type=str, required=True)
@click.option(
    "-n",
    default=10,
    type=int,
    show_default=True,
    help="Number of results to return, default=10, n=-1 returns all.",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def rg(args, n):
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
    args = args[1:]
    return_value, proc = lib.run_ripgrep(pattern, args)
    if return_value == "FileNotFoundError":
        console.print(proc)
    elif return_value == "None":
        console.print("[red]Failed to read rg output[/red]")
    elif return_value:
        console.print("OTHER MYSTERIOUS ERROR??")

    # otherwise all good - printout
    # for search and replace in resulting filenames
    prefix = str(lib.text_dir_full_name)
    print(lib)
    try:
        suffix = f".{lib.extractor}.md"
    except AttributeError:
        print("ATTRIBUTE ERROR...here is dir lib")
        print(dir(lib))
        print("RETURNING")
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
    doc_details = doc_links.merge(lib.doc_df, on='path', how='inner')

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
                qd(metadata.to_frame(name="value"))

                # Docs for this specific tag
                this_docs = doc_details[doc_details.tag == t]
                if not this_docs.empty:
                    click.secho(f"Linked Documents ({len(this_docs)}):", fg="cyan")
                    cols = ['name', 'create', 'hash', 'size']
                    this_docs_view = this_docs.copy()
                    this_docs_view['hash'] = this_docs_view['hash'].str[:12]
                    qd(this_docs_view[cols], show_index=True)

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
            # open_doc: prefer the 'preferred' one for each tag
            if 'preferred' in doc_details.columns:
                to_open = doc_details[doc_details.preferred == 1].path.unique().tolist()
                if not to_open:
                    to_open = doc_details.groupby('tag')['path'].first().unique().tolist()
            else:
                to_open = doc_details.groupby('tag')['path'].first().unique().tolist()

        # Apply Limit
        if len(to_open) > limit:
            click.secho(f"Warning: Found {len(to_open)} files, but limit is {limit}. Only opening first {limit}.", fg="yellow")
            to_open = to_open[:limit]

        for d in to_open:
            click.echo(f"Opening: {Path(d).name}")
            _open_document(d)

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

    # 1. Find Documents with this hash
    try:
        mask = lib.doc_df.hash.str.contains(hash_str, regex=True, na=False, case=False)
        doc_matches = lib.doc_df[mask]
    except Exception as e:
        click.echo(f"Regex error: {e}")
        return

    if doc_matches.empty:
        click.echo(f"No documents found with hash matching: {hash_str}")
        return

    # 2. Find Linked Tags
    paths = doc_matches.path.tolist()
    links = lib.ref_doc_df[lib.ref_doc_df.path.isin(paths)]
    tags = links.tag.unique().tolist()

    if not tags:
        click.echo(f"Found {len(doc_matches)} files but no references are linked to them.")
        if verbose > 0:
            qd(doc_matches[['name', 'hash', 'size']])
    else:
        # 3. Show Reference Info
        # Join doc_df hash back to refs for display
        refs = lib.ref_df[lib.ref_df.tag.isin(tags)].copy()

        # We need a mapping of tag -> short_hash
        tag_hash_map = links.merge(doc_matches[['path', 'hash']], on='path')
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
        for p in paths:
            click.echo(f"Opening: {Path(p).name}")
            _open_document(p)


# doc title opening ======experimental-----------------

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
        _open_document(d)


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
        _open_document(d)


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
    completers["edit"] = RustFuzzyCompleter(LibraryContext.get_library_tags)
    completers["delete"] = RustFuzzyCompleter(LibraryContext.get_library_tags)
    completers["title"] = RustFuzzyCompleter(LibraryContext.get_library_titles)
    completers["tt"] = RustFuzzyCompleter(LibraryContext.get_library_tag_titles)
    completers["hash"] = RustFuzzyCompleter(LibraryContext.get_library_hashes)

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
    completers["import-bibtex"] = PathCompleter(only_directories=False, expanduser=True)
    completers["import-doc"] = PathCompleter(only_directories=False, expanduser=True)

    # Register QT commands, exclude 'uber' to prevent recursion
    shell.register_click_group(entry, exclude=["uber"], completers=completers)

    def prompt_function():
        """Prompt uses breadcrumb chain from UBERSHELL_CHAIN plus current library."""
        lib = LibraryContext.get()
        chain = os.environ.get("UBERSHELL_CHAIN", shell.prompt_label)
        return HTML(
            f"<ansired>{chain} <ansigreen>[{lib.name}]</ansigreen> > </ansired>"
        )

    if lib_name == "" and auto_open:
        lib_name = DEFAULT_LIBRARY

    if lib_name != "":
        try:
            lib = Library(lib_name)
            LibraryContext.set(lib)
            logger.info(
                f"Opened {lib.config.name}, loaded {len(lib.ref_df):,d} references."
            )
        except Exception as e:
            logger.error("Open library error: %s", e)

    shell.start(prompt_function=prompt_function)


if __name__ == "__main__":
    # to facilitate performance logging
    # run python -m cProfile -o perf.prof -m archivum.cli
    # recent top 10 !/Boonen|Tsanakas|Wang, R/
    entry()
