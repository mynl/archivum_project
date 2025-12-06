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

# for uber loop
from uber_shell import UberShell  # type: ignore[import-untyped]
from rustfuzz import FuzzyMatcherMultiHi  # type: ignore[import-untyped]
from querexfuzz.core import querexfuzz_help  # type: ignore[import-untyped]

from .library import Library
from .document import Document  # type: ignore[import-untyped]
from . import DEFAULT_LIBRARY, EMPTY_LIBRARY, LIBRARIES_DIR, BASE_DIR
from .utilities import make_qd
from .config import Configurator
from .crossref import lookup_doi, search_by_title, search
from .bibtex import dict_to_bibtex_crossref
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
        cls.matcher_tags = None
        cls.matcher_titles = None
        cls.matcher_tags_titles = None

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
    if lib.is_empty:
        click.secho("No library open; ignoring.")
        return
    logger.info("Closing library %s", lib)
    lib = LibraryContext.get()
    LibraryContext.clear()


# ========================================================================================
# @entry.command()
# @click.argument('other_lib_name', type=str)
# def merge(other_lib_name):
#     """Merge another library into the current library."""
#     lib = LibraryContext.get()
#     if lib.is_empty:
#         return

#     logger.info("Merging %s into %s", other_lib_name, lib)
#     logger.todo('Implement merge!')
#     # TODO: Implement merge logic
#     # try:
#     #     other = Library(other_lib_name)
#     # except Exception as e:
#     #     logger.error(e)
#     # else:
#     #     logger.todo('PERFORM MERGE!!')


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
            "pdf_dir_name": click.prompt(local_prompt("pdf dir name"),
                default="\\S\\Library"
            ),
            "full_text": "true",
            "text_dir_name": click.prompt(
                local_prompt("Full text Directory"), default=str(BASE_DIR / "pdf-full-text")
            ),
            "file_formats": ["*.pdf"],
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
@entry.command()
@click.option(
    "-d",
    "--details",
    is_flag=True,
    help="Show detailed information about each library.",
)
def list(details):
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
@click.option(
    "-f",
    "--field",
    type=str,
    default="",
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
                    tags = result.loc[result.tag.str.contains(expr, regex=True), "tag"]
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
@click.option("--author", "-a", help="Author name")
@click.option("--title", "-t", help="Title of work")
@click.option("--doi", "-d", help="DOI string")
@click.option("--raw", "-r", is_flag=True, help="Show raw output.")
@click.option("--keywords", "-k", help="Search keywords")
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
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-p",
    "--pdf-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing PDFs referenced in the BibTeX file; "
    "defaults to the library's pdf_dir_name.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity. Specify -vv or -vvv for more detail.",
)
@click.option(
    "-h",
    "--add-hashes",
    is_flag=True,
    help="Hash input pdf files.",
)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    help="Actually perform the import; otherwise, do a dry run and report stats.",
)
def import_bibtex(bibtex_path: Path, pdf_dir: Path, add_hashes: bool, verbose: int, execute: bool):
    """
    Import new references from a BibTeX file into the current library.
    """
    if execute:
        logger.info("Execution enabled: changes will be applied.")
    else:
        logger.info("Dry run mode: no changes applied.")

    if verbose == 0:
        click.echo("Running silently.")
    elif verbose == 1:
        click.echo("Running with standard verbosity (v).")
    elif verbose == 2:
        click.echo("Running with high verbosity (vv).")
    elif verbose >= 3:
        click.echo("Running with maximum verbosity (vvv or more).")

    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot import.")
        return

    # create importer
    b = Bib2df_Incremental(
        bibtex_file_path=bibtex_path, pdf_dir=pdf_dir, reference_library=lib,
        add_hashes=add_hashes
    )
    # do the import
    import_df = b.import_bibtex_file()

    if verbose > 0:
        qd(import_df)
    if verbose > 1:
        qd(b.import_analysis())

    if execute:
        click.echo(f"Updating with {len(b.ported_df)} entries.")
        b.update_library(save=True)


# ========================================================================================
@entry.command(name="import-doc")
@click.argument(
    "doc_path",
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
@click.option(
    "-r",
    "--recursive",
    is_flag=True,
    show_default=True,
    help="Search for pdfs recursively when DOC_PATH is a directory.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity. Specify -vv or -vvv for more detail.",
)
@click.option(
    "-x",
    "--execute",
    is_flag=True,
    help="Actually perform the import; otherwise, do a dry run and report stats.",
)
def import_doc(doc_path: Path, recursive: bool, verbose: int, execute: bool):
    """
    Explore importing new documents into the current library. Process
    is to write a temporary bibtex file and open it for editing.
    You can then import that when you are happy. It includes filenames.

    If doc_path is a directory, [r]glob all pdf files in it.
    """
    if execute:
        logger.info("Execution enabled: changes will be applied.")
    else:
        logger.info("Dry run mode: no changes applied.")

    if verbose == 0:
        click.echo("Running silently.")
    elif verbose == 1:
        click.echo("Running with standard verbosity (v).")
    elif verbose == 2:
        click.echo("Running with high verbosity (vv).")
    elif verbose >= 3:
        click.echo("Running with maximum verbosity (vvv or more).")

    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot import.")
        return

    # find the files
    if doc_path.is_dir():
        if recursive:
            doc_paths = list(doc_path.rglob("*.pdf"))
        else:
            doc_paths = list(doc_path.glob("*.pdf"))
        logger.info("Found %s files", len(doc_paths))
    else:
        doc_paths = [doc_path]

    docs = []
    bibs = []
    for p in doc_paths:
        try:
            doc = Document(p)
            doc.process()
            docs.append(doc)
            blob = doc.bibtex()
            bibs.append(blob)
        except Exception as e:
            click.echo(f"Error: {e}")

    # for the time being...
    click.echo("\n".join(bibs))

    # # create importer
    # b = Bib2df_Incremental(
    #     bibtex_file_path=xx, pdf_dir=pdf_dir, reference_library=lib
    # )
    # # do the import
    # import_df = b.import_bibtex_file()

    # if verbose > 0:
    #     qd(import_df)
    # if verbose > 1:
    #     qd(b.import_analysis())

    # if execute:
    #     click.echo("Updating with {len(b.ported_df)} entries.")
    #     b.update_library(save=True)


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
@click.argument("tag", type=str)
@click.option(
    "-a", "--all-docs", is_flag=True, help="Open all docs if more than one match."
)
def tag(tag, all_docs):
    """Open a document by its tag."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo(
            "No library open...don't know where to look for text files. Returning"
        )
        return
    if lib.ref_doc_df.empty:
        click.echo("No referenced documents. Returning")
        return

    df = lib.ref_doc_df.query("tag == @tag")
    if len(df) == 0:
        click.echo("No matching documents found to %s", tag)
    if not all_docs:
        df = df.iloc[:1]
    if all_docs and len(df) > 5:
        click.echo("Found %s docs, just opening first 5", len(df))
        df = df.iloc[:5]
    for d in df.path:
        _open_document(d)


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
    "-a", "--all-docs", is_flag=True, help="Open all docs if more than one match."
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
    "-a", "--all-docs", is_flag=True, help="Open all docs if more than one match."
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
@click.option("-l", "--lib-name", type=str, default="", help="Open library.")
@click.option(
    "-a",
    "--auto-open",
    is_flag=True,
    show_default=True,
    help="If true, auto open the default library.",
)
@click.option("-d", "--debug", is_flag=True)
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
    completers["title"] = RustFuzzyCompleter(LibraryContext.get_library_titles)
    completers["tt"] = RustFuzzyCompleter(LibraryContext.get_library_tag_titles)

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
    completers["import_bibtex"] = PathCompleter(only_directories=False, expanduser=True)
    completers["import_doc"] = PathCompleter(only_directories=False, expanduser=True)

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
