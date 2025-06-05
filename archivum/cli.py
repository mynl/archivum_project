"""Implement command line interface for archivum."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import yaml

import click
from lark import ParseError
import pandas as pd
from pendulum import local_timezone
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import (
    FuzzyCompleter, WordCompleter,
    NestedCompleter, DynamicCompleter
)
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.document import Document
from rich.console import Console
from rich.text import Text

from . reference import Reference
from . library import Library
from . import DEFAULT_CONFIG_FILE, BASE_DIR, APP_NAME, EMPTY_LIBRARY
from . utilities import fGT
from . document import find_pdfs, Document
from . library import Library
from . logger_shim import LoggerShim, LogLevel


# local constants
DEFAULT_NEW_DIR = str(Path.home() / 'Downloads')
EMPTY_DF = pd.DataFrame([])
# logger
logger = LoggerShim(level=LogLevel.INFO, use_click=True, name=__name__)


console = Console()

# ========================================================================================
# ========================================================================================
# Library context manager
class LibraryContext:
    """Singleton context manager for the global open Library instance."""

    current = None
    no_library = EMPTY_LIBRARY

    @classmethod
    def set(cls, lib):   # noqa
        cls.current = lib
        logger.debug("Library set to: %s", lib)

    @classmethod
    def get(cls):   # noqa
        if cls.current is None:
            return cls.no_library
        return cls.current

    @classmethod
    def clear(cls):   # noqa
        logger.debug("Library %s closed.", cls.current)
        cls.current = None


# ========================================================================================
# ========================================================================================
def get_prompt(cmd):
    """Make a prompt for REPL."""
    lib = LibraryContext.get()
    lib_name = lib.name
    return HTML(
        f'<ansigreen>[{lib_name}]-></ansigreen> '
        f'<ansiyellow>{cmd} > </ansiyellow>'
    )

# ========================================================================================
# ========================================================================================
# Completers


def make_query_completer_static(df):
    """Make nested query completer for df (eg ref_df or database)."""
    lib = LibraryContext.get()
    if lib.is_empty:
        libs = None
    else:
        libs = {l: None for l in lib.list()}
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
    return NestedCompleter.from_nested_dict({
        "top": {},
        "recent": None,
        "verbose": None,
        "select": {
            "*": None,
            "-": cols,
            **cols
        },
        "where": cols_with_values,
        "order": cols,
        "sort": cols,
        "~": cols,
        "!": cols,
        "and": None,
        "open": libs,
        "o": None,
    })


# ========================================================================================
# ========================================================================================
@click.group()
def entry():
    """CLI for managing bibliographic entries."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')

# ========================================================================================


@entry.command()
@click.argument('lib_name', type=str)
def open_library(lib_name):
    """Open a library by name and set it as current."""
    try:
        lib = Library(lib_name)
        LibraryContext.set(lib)
        logger.debug(f"Opened {lib.name}, loaded {len(lib.ref_df):,d} references.")
    except Exception as e:
        logger.error('Open library error: %s', e)

# ========================================================================================


@entry.command()
def save_library():
    """Save the current library to disk."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...nothing to save. Returning")
        return
    logger.todo("Saving library %s", lib)
    lib.save()


# ========================================================================================
@entry.command()
def close_library():
    """Close the currently open library."""
    # TODO THINK ABOUT SAVING??
    lib = LibraryContext.get()
    if lib.is_empty:
        click.secho('No library open; ignoring.')
        return
    logger.info('Closing library %s', lib)
    logger.todo('SHOULD WE SAVE on CLOSE??')
    lib = LibraryContext.get()
    lib.close()
    LibraryContext.clear()


# ========================================================================================
@entry.command()
@click.argument('other_lib_name', type=str)
def merge_library(other_lib_name):
    """Merge another library into the current library."""
    lib = LibraryContext.get()
    if lib.is_empty:
        return

    logger.info("Merging %s into %s", other_lib_name, lib)
    logger.todo('Implement merge_library!')
    # TODO: Implement merge logic
    # try:
    #     other = Library(other_lib_name)
    # except Exception as e:
    #     logger.error(e)
    # else:
    #     logger.todo('PERFORM MERGE!!')


# ========================================================================================
@entry.command()
@click.argument('lib_name', type=str)
def create_library(lib_name):
    """Interactively create a YAML config file for a new library called lib_name."""
    lib_file_name = lib_name.replace(' ', '-')

    # sort the file out
    lib_path = BASE_DIR / f'{lib_file_name}.{APP_NAME}-config'
    click.secho("=== Library Config Creator ===", fg='cyan')
    click.secho(f'Creating Library {lib_name} at {lib_path}')
    if lib_path.exists():
        click.secho('Library file already exists: %s', lib_path)
        return

    def pr(x):
        """Make the prompt string."""
        return f'[{lib_name}] {x} > '

    tablefmt_completer = FuzzyCompleter(WordCompleter(
        ['mixed_grid', 'simple_grid', 'outline', 'simple_outline', 'mixed_outline', 'rst'],
        ignore_case=True))

    config = {
        "name": lib_name,
        "description": click.prompt(pr('Description')),
        "columns": ['type', 'tag', 'author', 'doi', 'file', 'journal', 'pages', 'title',
                    'volume', 'year', 'publisher', 'url', 'institution', 'number',
                    'mendeley-tags', 'booktitle', 'edition', 'month', 'address', 'editor',
                    'arc-citations', 'arc-source'],
        # TODO
        "bibtex_file": click.prompt(pr('BibTeX File'), default=f'\\S\\Telos\\biblio\\{lib_file_name}-test.bib'),
        "pdf_dir_name": click.prompt(pr('PDF Directory'), default='\\S\\Telos\\Library'),
        "full_text": "true",
        "text_dir_name": click.prompt(pr('PDF Directory'), default='\\temp\\pdf-full-text'),
        "file_formats": ["*.pdf"],
        "hash_files": click.confirm(pr("Hash files?"), default=True),
        "hash_workers": click.prompt(pr("Number of hash workers"), default=6, type=int),
        "last_indexed": 0,
        "timezone": click.prompt(pr("Timezone"), default=local_timezone()),
        "tablefmt": click.prompt(pr("Table format"), completer=tablefmt_completer),
    }

    with lib_path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False)

    lib = Library(lib_file_name)
    LibraryContext.set(lib)

    click.secho(f"\nConfig written to {lib_path}", fg="green")


# ========================================================================================
@entry.command()
@click.option(
    '-d', '--details',
    is_flag=True,
    help='Show detailed information about each library.'
)
def list_libraries(details):
    """List all available libraries."""
    logger.debug("Listing libraries...")
    # TODO: Implement listing logic
    if details:
        logger.debug("Detailed information.")
        df = Library.list_deets()
        click.echo(fGT(df))
    else:
        logger.debug("Basic information.")
        l = Library.list()
        l.insert(0, 'Library')
        click.echo(fGT(l))


# ========================================================================================
@entry.command()
def get_library_stats():
    """Display library stats library."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open. Returning")
        return
    logger.debug("Library stats %s", lib)
    click.echo(fGT(lib.stats().reset_index(drop=False)))


# ========================================================================================
@entry.command()
@click.option(
    '-f', '--field',
    type=str,
    default='',
    help='Show distinct values of field in each library field.'
)
def get_distinct_values(field):
    """Display number of distinct values in each library field."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't know where to look for files. Returning")
        return
    field = field.strip()
    logger.debug("Distinct values for field %s", field)
    if field == '':
        df = lib.distinct_values_by_field().reset_index(drop=False)
        df.index.name = 'field'
        df = df.sort_values(['distinct'], ascending=[False])
        click.echo(fGT(df))
    elif field in lib.database:
        df = lib.distinct_value_counts(field).reset_index(drop=False)
        click.echo(fGT(df))
    else:
        click.echo(f'Field {field} not found in library database.')


# ========================================================================================
@entry.command()
@click.argument(
    'start',
    type=str,
    default='',
    required=False,
)
@click.option(
    '-r', '--ref',
    is_flag=True,
    help='Search ref_df rather than database (default)'
)
def query_library(start: str, ref):
    """Interactive REPL to run multiple queries on the file index with fuzzy completion."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't what to query. Returning")
        return
    if ref:
        df = lib.ref_df
    else:
        df = lib.database

    click.echo(df.columns)

    click.echo(
        "Enter querex expression [verbose] [recent] [top n] [select field[, fields]\n"
        "column ~ /regex/ where sql_expression sort field1, -field2\n"
        " or type 'exit', 'x', 'quit' or 'q' to stop and ? for help).\n")

    # keywords = ['cls', 'and', 'or'] + list(lib.database.columns)
    # word_completer = FuzzyCompleter(WordCompleter(keywords, sentence=True))
    # session = PromptSession(completer=word_completer)
    # result = None
    result = EMPTY_DF
    base_completer = make_query_completer_static(df)

    def tag_branch():
        tag_values = sorted({str(tag) for tag in result["tag"].dropna().unique()})
        return FuzzyCompleter(WordCompleter(tag_values, sentence=True))

    # Inject dynamic fuzzy completer into 'open' and 'o'
    base_completer.options["open"] = DynamicCompleter(tag_branch)
    base_completer.options["o"] = DynamicCompleter(tag_branch)
    session = PromptSession(completer=base_completer)

    while True:
        try:
            expr = start or session.prompt(get_prompt('query-library'))
            start = ''
            pipe = False
            if expr.lower() in {"exit", "x", "quit", "q", ".."}:
                break
            elif expr == "?":
                click.echo(lib.querex_help())
                continue
            elif expr == 'cls':
                # clear screen
                os.system('cls')
                continue
            elif expr.find(">") >= 0:
                # contains a pipe
                expr, pipe = expr.split('>')
                pipe = pipe.strip()
            elif expr.startswith('o ') or expr.startswith('open '):
                # open files
                if result.empty:
                    click.echo('No existing query! Run query first')
                    continue
                # open file mode, start with o n
                try:
                    # o or open
                    if expr.startswith('o '):
                        expr = expr[1:].strip()
                    elif expr.startswith('open '):
                        expr = expr[5:].strip()
                    logger.info(f'{expr = }')
                    tags = result.loc[result.tag.str.contains(expr, regex=True), 'tag']
                    tags = sorted(set(tags.values))
                    docs = lib.ref_doc_df.query('tag in @tags').path.values
                    logger.info(f'{docs = }')
                    print(f'Trying to open {docs = }')
                    logger.info(f'Trying to open {docs = }')
                    for d in docs:
                        p = Path(d)
                        if not p.exists():
                            logger.info('file %s not found', p.name)
                            continue
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
                except Exception:
                    raise
                continue

            # if here, run query work
            try:
                # set as ref_df or database above...
                result = df.querex(expr)
            except ParseError as e:
                logger.error('Parsing error')
                logger.error(e)
            else:
                click.echo(fGT(result))
                click.echo(
                    f'{len(result)} of {result.qx_unrestricted_len:,d} results shown.')
                if pipe:
                    click.echo(
                        f'Found pipe clause {pipe = } TODO: deal with this!')
        except Exception as e:
            click.echo(f"[Error] {e}")


# ========================================================================================
@entry.command()
@click.option(
    '-d', '--directory',
    type=click.Path(exists=True),
    default=DEFAULT_NEW_DIR,
    show_default=True,
    help='Directory to scan for new PDFs.'
)
@click.option(
    '-m', '--meta',
    is_flag=True,
    help='Display metadata information for each PDF.'
)
@click.option(
    '-r', '--recursive',
    is_flag=True,
    help='Recursive search of DIRECTORY and its sub-directories, default is single.'
)
def new(directory, meta, recursive):
    """
    Scan a directory for new PDF files and optionally display metadata.

    Note ``new`` requires an open library for timezone and name completion.
    Optionally: look for duplicates!
    """
    logger.info("Scanning directory %s", directory)
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo('No open library...exiting')
        LibraryContext.last_new = EMPTY_DF
        return
    try:
        dfs = lib.new_documents(directory, meta, recursive)
    except FileNotFoundError:
        click.echo('%s directory not found', directory)
        LibraryContext.last_new = EMPTY_DF
        return
    else:
        # store it away in the context
        LibraryContext.last_new = dfs

    if meta:
        click.echo(fGT(dfs[['n', 'create', 'file_name', 'meta_author',
                            'meta_subject', 'meta_title', 'meta_crossref']]
                       .sort_values('create', ascending=False)
                       ))
    else:
        click.echo(fGT(dfs[['n', 'file_name']]))


# ========================================================================================
@entry.command(name='import')
@click.option(
    '-x', '--execute',
    is_flag=True,
    help='Actually perform the import; otherwise, do a dry run.'
)
@click.option(
    '-p', '--partial',
    default='',
    show_default=True,
    help='Comma-separated list of PDF file numbers to upload, default all files.'
)
@click.option(
    '-r', '--regex',
    is_flag=True,
    help='Interpret partial option as a regex, default is comma separated list'
)
def import_(execute, partial, regex):
    """Import bibliographic entries, optionally filtered and executed."""
    logger.info("Importing documents, partial match = '%s', regex mode %", partial, regex)
    df = LibraryContext.last_new
    if df.empty:
        click.echo('No new documents found! Run new.')
        return
    # figure the docs
    if regex:
        r = re.compile(partial)
        indices = [i for i in range(1, 1 + len(df))
                   if r.search(str(i))]
    else:
        indices = [int(i.strip()) for i in partial.split(',')]
    logger.info('Indices = %', indices)
    # Import logic
    for i in indices:
        pdf_path = df.loc[i, 'path']
        ref = Reference.from_pdf(pdf_path)
        # prompt_for_fields(ref)  # interactively fill in fields
        click.echo(ref.to_dict())  # or save it, display BibTeX, etc.

    if execute:
        logger.info("Execution enabled: changes will be applied.")
    else:
        logger.info("Dry run mode: no changes applied.")


# ========================================================================================
def run_ripgrep(pattern, args):
    cmd = ["rg", "--json", "--stats", "--C 1", pattern, *args]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        console.print("[red]ripgrep (rg) not found on PATH[/red]")
        return

    if proc.stdout is None:
        console.print("[red]Failed to read rg output[/red]")
        return

    for line in proc.stdout:
        try:
            result = json.loads(line)
            if result.get("type") == "match":
                file = result["data"]["path"]["text"]
                line_num = result["data"]["line_number"]
                text = result["data"]["lines"]["text"].rstrip()

                styled = Text()
                styled.append(f"{file}:{line_num}: ", style="bold cyan")
                styled.append(text, style="white")
                console.print(styled)
        except json.JSONDecodeError:
            console.print(line.strip(), style="dim")


@entry.command(context_settings={"ignore_unknown_options": True})
@click.argument("pattern")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def rg(pattern, args):
    """Run ripgrep (rg) with given pattern and args against text extracts from pdfs."""
    run_ripgrep(pattern, args)
    return
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't know where to look for files. Returning")
        return
    text_dir_name = lib.text_dir_name
    print(type(args))
    args = list(args) + [f'-g "{text_dir_name}"']
    cmd = ["rg", pattern, *args]
    print(cmd)
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        click.echo("Error: ripgrep (rg) not found on PATH", err=True)


# ========================================================================================
@entry.command()
@click.option(
    "-l", "lib_name",
    default='',
    required=False,
    type=str,
    help="Starting library name, default 'Uber-Library'"
)
@click.option(
    '-d', '--debug',
    is_flag=True,
    help='Enable debug mode with verbose output.'
)
@click.option(
    '-s', '--start',
    type=str,
    required=False,
    default='',
    help='Starting command, e.g., uber query-library.'
)
@click.argument(
    "subcommand_args",
    nargs=-1,
    type=click.UNPROCESSED,
)
def uber(lib_name, start, debug, subcommand_args):
    """
    Start an interactive REPL loop for issuing archivum commands.

    If given, open lib_name, otherwise open the default library.

    \b
    Examples:
        - archivum uber
        - archivum uber "query-library"
        - archivum uber "open-library mylib"
        - archivum uber -d -s query-library -- "recent top 10 !/Wang, R/"

    \b
    Arguments
        - argument: argument to pass to the subcommand."
    """
    if debug:
        logger.setLevel(LogLevel.DEBUG)
        logger.debug("Debug mode enabled.")

    commands = [
        'open-library',
        'save-library',
        'close-library',
        'create-library',
        'merge-library',
        'query-library',
        'list-libraries',
        'get-library-stats',
        'get-distinct-values',
        'new',
        'import',
        'rg',
        'cls',
        'exit',
    ]
    safe_on_empty_libraries = [
        'open-library',
        'create-library',
        'list-libraries',
        'new',
        'cls',
        'exit',
    ]

    # Hybrid resolver: prefix match first, fallback to fuzzy
    fuzzy_completer = FuzzyCompleter(WordCompleter(commands))

    def resolve_command_hybrid(cmd: str) -> str:
        prefix_matches = [c for c in commands if c.startswith(cmd)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        doc = Document(text=cmd)
        completions = list(fuzzy_completer.get_completions(doc, complete_event=None))
        return completions[0].text if len(completions) == 1 else cmd

    # Interactive prompt with fuzzy + nested completer for UI
    # session = PromptSession(
    #     completer=FuzzyCompleter(NestedCompleter({c: None for c in commands}))
    # )
    session = PromptSession(
        completer=FuzzyCompleter(WordCompleter(commands, sentence=True))
    )

    lib_name = lib_name or DEFAULT_CONFIG_FILE
    logger.info('Opening "%s" and starting interactive loop.', lib_name)
    entry(args=["open-library", lib_name], standalone_mode=False)

    if len(subcommand_args) == 1:
        subcommand_args = list(subcommand_args)
    elif len(subcommand_args):
        subcommand_args = [' '.join(subcommand_args)]
    else:
        subcommand_args = []

    while True:
        try:
            lib = LibraryContext.get()
            q = start or session.prompt(get_prompt('uber'))
            start = ''
            # dispatch call
            if q in {'exit', ';', 'x', '..'}:
                break
            elif q in {'?', 'h'}:
                click.echo("Available commands:\n * " + "\n * ".join(commands) + "\n")
                entry(args=['uber', '--help'], standalone_mode=False)
            elif q == 'cls':
                os.system('cls' if os.name == 'nt' else 'clear')
            elif q:
                try:
                    args = q.split()
                    if args:
                        args[0] = resolve_command_hybrid(args[0])
                    logger.info('uber dispatch, resolved args = %s, sub = %s', args, subcommand_args)
                    # only query-library function takes debug arg
                    if len(args) > 1 and 'debug' in args and 'query-library' not in args:
                        args.remove('debug')
                    cmd = args[0]
                    if lib.is_empty and cmd not in safe_on_empty_libraries:
                        click.echo(f'No library open, cannot execute {cmd}.')
                    else:
                        print(args + subcommand_args)
                        entry(args=args + subcommand_args, standalone_mode=False)
                        subcommand_args = []
                    logger.info('REPL loop completed.')
                except Exception as e:
                    logger.error(f"Error: {e}")
        except (KeyboardInterrupt, EOFError):
            break


# ========================================================================================
# ========================================================================================
def repl_help():
    """Help string for repl loop."""
    return """
Repl Help
=========

[select top regex order etc] > output file

* > pipe output NYI.

cls     clear screen
?       show help
x       exit

"""


# ========================================================================================
def uber_help():
    h = '''
Meta
====
.. ; x               quit
? h                  help
--help               Built in help (always available)

Help for Archivum Scripts
==========================
query-repl          enter query REPL loop
new                 display new PDFs in watched folders
upload              upload new pdf(s)
list                list all archivum libraries
deets               details on all archivum libraries
uber                Uber search, access to all archivum functions
cls                 clear screen

'''
    click.echo(h)


if __name__ == '__main__':
    # to facilitate performance logging
    # run python -m cProfile -o perf.prof -m archivum.cli
    # recent top 10 !/Boonen|Tsanakas|Wang, R/
    entry()
