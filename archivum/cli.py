"""Implement command line interface for archivum."""

import os
from pathlib import Path
import shlex
import subprocess
import yaml

import click

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import (
    FuzzyCompleter, WordCompleter, FuzzyWordCompleter, NestedCompleter
    )
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.document import Document

from . reference import Reference
from . library import Library
from . import DEFAULT_CONFIG_FILE, BASE_DIR, APP_NAME
from . utilities import fGT
from . document import find_pdfs
from . library import Library
from . logger_shim import LoggerShim, LogLevel


# local constants
DEFAULT_NEW_DIR = str(Path().home() / 'Downloads')

# logger
logger = LoggerShim(level=LogLevel.INFO, use_click=True, name=__name__)


# ========================================================================================
# ========================================================================================
# Library context manager
class LibraryContext:
    """Singleton context manager for the global open Library instance."""

    current = None
    no_library = type('EmptyLibrary', (), {'name': 'No library open', 'is_empty': True})

    @classmethod
    def set(cls, lib):   # noqa
        cls.current = lib
        logger.info("Library set to: %s", lib)

    @classmethod
    def get(cls):   # noqa
        if cls.current is None:
            return cls.no_library
        return cls.current

    @classmethod
    def clear(cls):   # noqa
        logger.info("Library %s closed.", cls.current)
        cls.current = None


# ========================================================================================
# ========================================================================================
@click.group()
def entry():
    """CLI for managing bibliographic entries."""
    pass

# ========================================================================================


@entry.command()
@click.argument('lib_name', type=str)
def open_library(lib_name):
    """Open a library by name and set it as current."""
    lib = Library(lib_name)
    click.echo(f"Opened {lib.name}, loaded {len(lib.ref_df):,d} references.")
    LibraryContext.set(lib)


# ========================================================================================
@entry.command()
def save_library():
    """Save the current library to disk."""
    lib = LibraryContext.get()
    logger.info("Saving library %s", lib)
    lib.save()


# ========================================================================================
@entry.command()
def close_library():
    """Close the currently open library."""
    # TODO THINK ABOUT SAVING??
    lib = LibraryContext.get()
    logger.info('Closing library %s', lib)
    logger.warning('SHOULD WE SAVE on CLOSE??')
    lib = LibraryContext.get()
    lib.close()
    LibraryContext.clear()


# ========================================================================================
@entry.command()
@click.argument('other_lib_name', type=str)
def merge_library(other_lib_name):
    """Merge another library into the current library."""
    lib = LibraryContext.get()
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
    logger.info(f"Creating new library: {lib_name}")

    # sort the file out
    lib_path = BASE_DIR / f'{lib_name}.{APP_NAME}-config'
    click.secho("=== Library Config Creator ===", fg='cyan')
    click.secho(f'Creating Library {lib_name} at {lib_path}')

    config = {
        "library": lib_name,
        "description": click.prompt('Description'),
        "columns": ['type', 'tag', 'author', 'doi', 'file', 'journal', 'pages', 'title',
                    'volume', 'year', 'publisher', 'url', 'institution', 'number',
                    'mendeley-tags', 'booktitle', 'edition', 'month', 'address', 'editor',
                    'arc-citations'],
        "bibtex_file": click.prompt('BibTeX File', default=f'{lib_name}-test.bib'),
        "pdf_dir": click.prompt('PDF Directory', default='NOT USED YET'),
        "file_formats": ["*.pdf"],
        "hash_files": click.confirm("Hash files?", default=True),
        "hash_workers": click.prompt("Number of hash workers", default=6, type=int),
        "last_indexed": 0,
        "timezone": click.prompt("Timezone", default="Europe/London"),
        "tablefmt": click.prompt("Table format", default="mixed_grid"),
    }

    with lib_path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False)

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
    logger.info("Listing libraries...")
    # TODO: Implement listing logic
    if details:
        logger.info("Detailed information.")
        click.echo(Library.list_deets())
    else:
        logger.info("Basic information.")
        click.echo(Library.list())


# ========================================================================================
@entry.command()
def get_library_stats():
    """Display library stats library."""
    lib = LibraryContext.get()
    logger.info("Library stats %s", lib)
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
    logger.info("Distinct values for field %s", field)
    if field == '':
        click.echo(fGT(lib.distinct_values_by_field()))
    elif field in lib.database:
        dv = lib.distinct_value_counts(field)
        click.echo(fGT())
    else:
        click.echo(f'Field {field} not found in library database.')


# ========================================================================================
@entry.command()
@click.option(
    '-d', '--debug',
    is_flag=True,
    help='Run querying REPL loop.'
)
def query_library(debug: str):
    """Interactive REPL to run multiple queries on the file index with fuzzy completion."""
    lib = LibraryContext.get()
    click.echo(
        "Enter querex expression [verbose] [recent] [top n] [select field[, fields]\n"
        "column ~ /regex/ where sql_expression sort field1, -field2\n"
        " or type 'exit', 'x', 'quit' or 'q' to stop and ? for help).\n")

    keywords = ['cls', 'and', 'or'] + list(lib.database.columns)
    word_completer = FuzzyCompleter(WordCompleter(keywords, sentence=True))
    session = PromptSession(completer=word_completer)
    result = None

    while True:
        try:
            expr = session.prompt(HTML('<ansiyellow>archivum</ansiyellow>'
                                       f'<ansigreen>>>[{lib.name}]</ansigreen> > ')).strip()
            pipe = False
            if expr.lower() in {"exit", "x", "quit", "q"}:
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
            elif expr.startswith('o'):
                # open files
                if result is None:
                    click.echo('No existing query! Run query first')
                    continue
                # open file mode, start with o n
                try:
                    expr = int(expr[1:].strip())
                except ValueError:
                    click.echo('Wrong syntax for open, expect o index number')
                try:
                    fname = result.loc[expr, 'file']
                    logger.todo('TODO...open ', fname)
                    # os.startfile(fname)
                except KeyError:
                    logger.error(f'Key {expr} not found.')
                except FileNotFoundError:
                    logger.error("File does not exist.")
                except OSError as e:
                    logger.error(f"No association or error launching: {e}")
                continue

            # if here, run query work
            result = lib.database.querex(expr)
            click.echo(fGT(result))
            click.echo(
                f'{lib._last_unrestricted:,d} unrestricted results, {len(result)} shown.')
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
    help='Recursively scan subdirectories.'
)
def new(directory, meta, recursive):
    """Scan a directory for new PDF files and optionally display metadata."""
    logger.info("Scanning directory %s", directory)
    if recursive:
        logger.info("Recursively scanning subdirectories.")
    if meta:
        logger.info("Displaying metadata for found PDFs.")
    # TODO: Implement actual scanning logic
    if recursive:
        pdfs = directory.rgrep('*.pdf')
    else:
        pdfs = directory.grep('*.pdf')
    for i, pdf in enumerate(pdfs):
        click.echo(f"{i: 2d}: {pdf.name}")
        if meta:
            meta = extract_metadata(pdf)
            click.echo(f"    Title: {meta.get('title', 'Unknown')}")
            click.echo(f"    Author: {meta.get('author', 'Unknown')}")


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
def import_(execute, partial):
    """Import bibliographic entries, optionally filtered and executed."""
    logger.info("Importing entries, partial match = '%s'", partial)
    if execute:
        logger.info("Execution enabled: changes will be applied.")
    else:
        logger.info("Dry run mode: no changes applied.")
    # TODO: Implement import logic
    indices = [int(i.strip()) for i in partial.split(',')]
    pdfs = find_pdfs()  # reuse previous list or cache it
    for i in indices:
        pdf_path = pdfs[i]
        ref = Reference.from_pdf(pdf_path)
        # prompt_for_fields(ref)  # interactively fill in fields
        click.echo(ref.to_dict())  # or save it, display BibTeX, etc.


# ========================================================================================
@entry.command()
@click.argument(
    "lib_name",
    default='',
    required=False,
    type=str,
)
@click.option(
    '-d', '--debug',
    is_flag=True,
    help='Enable debug mode with verbose output.'
)
def uber(lib_name, debug):
    """
    Start an interactive REPL loop for issuing archivum commands.

    If given, open lib_name, otherwise open the default library.

    Examples:
        archivum uber
        archivum uber "query-library"
        archivum uber "open-library mylib"
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
    fuzzy_completer = FuzzyWordCompleter(commands)

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

    while True:
        try:
            lib = LibraryContext.get()
            q = session.prompt(HTML(f'<ansigreen>archivum [{lib.name}]> </ansigreen>')).strip()
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
                    logger.info('uber dispatch, resolved args = %s', args)
                    # only query-library function takes debug arg
                    if len(args) > 1 and 'debug' in args and 'query-library' not in args:
                        args.remove('debug')
                    cmd = args[0]
                    if lib.is_empty and cmd not in safe_on_empty_libraries:
                        click.echo(f'No library open, cannot execute {cmd}.')
                    else:
                        entry(args=args, standalone_mode=False)
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
