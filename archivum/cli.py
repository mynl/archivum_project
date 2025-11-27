"""Implement command line interface for archivum."""

from importlib.resources import files
import json
import logging
import logging.config
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
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text

# for uber loop
from great2.shell import UberShell

from . reference import Reference
from . library import Library
from . document import Document
from . import DEFAULT_LIBRARY, EMPTY_LIBRARY, LIBRARIES_DIR
from . utilities import make_qd
from . config import Configurator
from . querex import querex_help

# local constants
DEFAULT_NEW_DIR = str(Path.home() / 'Downloads')
EMPTY_DF = pd.DataFrame([])

# for local display function
qd = make_qd(max_table_inch_width=18,
            max_string_length=-1, # no string truncation
            max_rows=50,
            display_func=click.echo)

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
    try:
        lib_name = lib.name
        return HTML(
            '<ansired>archivum </ansired>'
            f'<ansigreen>[{lib_name}] > </ansigreen>'
            f'<ansiyellow>{cmd} > </ansiyellow>'
        )
    except AttributeError as e:
        print('get prompt error', e, sep='\n')
        return HTML(f'ERR: <ansiyellow>{cmd} > </ansiyellow>')
# ========================================================================================
# ===================f=====================================================================
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
        logger.info(f"Opened {lib.config.name}, loaded {len(lib.ref_df):,d} references.")
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
    lib.save()
    click.echo(f"{lib.name} saved")


# ========================================================================================
@entry.command()
def close_library():
    """
    Close the currently open library.

    This is a command line concept; the Library class has no close
    method. You just delete it. It does NOT track if it is dirty and
    needs to change.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.secho('No library open; ignoring.')
        return
    logger.info('Closing library %s', lib)
    lib = LibraryContext.get()
    del lib
    LibraryContext.clear()


# ========================================================================================
# @entry.command()
# @click.argument('other_lib_name', type=str)
# def merge_library(other_lib_name):
#     """Merge another library into the current library."""
#     lib = LibraryContext.get()
#     if lib.is_empty:
#         return

#     logger.info("Merging %s into %s", other_lib_name, lib)
#     logger.todo('Implement merge_library!')
#     # TODO: Implement merge logic
#     # try:
#     #     other = Library(other_lib_name)
#     # except Exception as e:
#     #     logger.error(e)
#     # else:
#     #     logger.todo('PERFORM MERGE!!')


# ========================================================================================
@entry.command()
@click.argument('lib_name', type=str)
def create_library(lib_name):
    """
    Create and open a new library. SEE ALSO THE CONFIG VERSION

    Interactively create a YAML config file for a new library
    called lib_name. Save config. Then open and return the library.

    Library must not already exist.
    """
    lib_dir_name = lib_name.replace(' ', '-')

    # sort the file out
    lib_path = LIBRARIES_DIR / lib_dir_name
    if lib_path.exists():
        click.secho('Error: Library file already exists: %s', lib_path)
        click.secho('Pick another name. Returning, no library created.')
        return
    click.secho("=== Library Config Creator ===", fg='cyan')
    click.secho(f'Creating Library {lib_name} at {lib_path}')

    def pr(x):
        """Make the prompt string."""
        return f'[{lib_name}] {x} > '

    tablefmt_completer = FuzzyCompleter(WordCompleter(
        ['mixed_grid', 'simple_grid', 'outline', 'simple_outline', 'mixed_outline', 'rst'],
        ignore_case=True))
    while True:
        config = {
            "name": lib_name,
            "description": click.prompt(pr('Description')),
            "columns": ['type', 'tag', 'author', 'doi', 'file', 'journal', 'pages', 'title',
                        'volume', 'year', 'publisher', 'url', 'institution', 'number',
                        'mendeley-tags', 'booktitle', 'edition', 'month', 'address', 'editor',
                        'arc-citations', 'arc-source'],
            # TODO
            "bibtex_file": click.prompt(pr('BibTeX File'), default=f'\\S\\Telos\\biblio\\{lib_dir_name}-test.bib'),
            "pdf_dir_name": click.prompt(pr('PDF Directory'), default='\\S\\Telos\\Library'),
            "full_text": "true",
            "text_dir_name": click.prompt(pr('PDF Directory'), default='\\temp\\pdf-full-text'),
            "file_formats": ["*.pdf"],
            "hash_files": click.confirm(pr("Hash files?"), default=False),
            "hash_workers": click.prompt(pr("Number of hash workers"), default=6, type=int),
            "last_indexed": 0,
            "timezone": click.prompt(pr("Timezone"), default=local_timezone()),
            "tablefmt": click.prompt(pr("Table format"), completer=tablefmt_completer),
        }
        try:
            con = Configurator(**config)
            break
        except ValidationError as e:
            logger.error('configuration error %s', e)
            click.secho('Error in config, no file written. Adjust!')
            # todo  - a quit option!

    # con must be valid
    con.save(lib_path)
    #o open the library
    lib = Library(lib_dir_name)
    LibraryContext.set(lib)
    click.secho(f"\nConfig written to {lib_path}", fg="green")


# ========================================================================================
@entry.command()
@click.option(
    '-d', '--details',
    is_flag=True,
    help='Show detailed information about each library.'
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
        l = Library.list()
        l.insert(0, 'Library')
        qd(l)


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
        qd(df)
    elif field in lib.database:
        df = lib.distinct_value_counts(field).reset_index(drop=False)
        qd(df)
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
            if expr.lower() in {"exit", "x", ".."}:
                break
            elif expr == "?":
                click.echo(querex_help())
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
                    logger.info(f'{expr=}')
                    tags = result.loc[result.tag.str.contains(expr, regex=True), 'tag']
                    tags = sorted(set(tags.values))
                    docs = lib.ref_doc_df.query('tag in @tags').path.values
                    logger.info(f'{docs=}')
                    print(f'Trying to open {docs=}')
                    logger.info(f'Trying to open {docs=}')
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
                qd(result)
                click.echo(
                    f'{len(result)} of {result.qx_unrestricted_len:,d} results shown.')
                if pipe:
                    click.echo(
                        f'Found pipe clause {pipe=} TODO: deal with this!')
        except Exception as e:
            click.echo(f"[Error] {e}")


# ========================================================================================
@entry.command()
# file_okay=False ensures autocomplete prefers directories
@click.argument('directory', type=click.Path(exists=True, file_okay=False), default='.')
@click.option(
    '-r', '--recursive',
    is_flag=True,
    show_default=True,
    help='Recursive search of DIRECTORY and its sub-directories.'
)
@click.option(
    "-s", "--save-path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,  # Set to None to handle dynamic default logic below
    help="Output path for bibtex file. Defaults to {directory}/bibliography.bib"
)
def new_docs(directory, recursive, save_path):
    """
    Scan a directory for new [PDF] document files and optionally display metadata.

    Note ``new`` requires an open library for timezone and name completion.
    Optionally: look for duplicates!
    """
    directory = Path(directory).absolute()
    click.echo(f'Scanning {directory}')
    if not directory.exists():
        click.echo("Input directory must exist.")
        return

    docs = []
    bibs = []
    file_generator = directory.rglob("*.pdf") if recursive else directory.glob("*.pdf")
    for f in file_generator:
        if not f.is_file():
            continue
        click.echo(f'Scanning {f.name}')
        try:
            doc = Document(f)
            doc.process()
            docs.append(doc)
            blob = doc.bibtex()
            bibs.append(blob)
        except Exception as e:
            click.echo(f'Error: {e}')

    click.echo(f'Found {len(docs)} docs and created {len(bibs)} bib entries:\n')
    s = '\n\n'.join(bibs)
    click.echo(s)
    click.echo()

    # Dynamic default: if not provided, save to bibliography.bib in the target dir
    if save_path is None:
        save_path = directory / "bibliography.bib"
    else:
        save_path = Path(save_path)
    click.echo(f'Saving bib file to {save_path.absolute()}')
    # actually save
    save_path.write_text(s, encoding='utf-8')


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

@entry.command(name="import-bibtex")
@click.argument(
    "bibtex_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-I",
    "--imports-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Root directory for import runs; defaults to config.imports_dir_name or BASE_DIR / 'imports'.",
)
@click.option(
    "-P",
    "--pdf-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing PDFs referenced in the BibTeX file; "
         "defaults to the library's pdf_dir_name.",
)
@click.option(
    "--audit-mode",
    is_flag=True,
    help="Enable BibTeX import audit logging (delegated to Bib2df).",
)
def import_bibtex_cmd(bibtex_path, imports_dir, pdf_dir, audit_mode):
    """
    Import new references from a BibTeX file into the current library.
    """
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open -- cannot import.")
        return

    cfg = lib.config
    if imports_dir is None:
        # Prefer an explicit config field if present, otherwise fall back.
        root = getattr(cfg, "imports_dir_name", None)
        if root:
            imports_dir = Path(root)
        else:
            imports_dir = lib.BASE_DIR / "imports"

    print(f'{imports_dir = }')

    result = lib.import_bibtex(
        bibtex_path=bibtex_path,
        imports_dir=imports_dir,
        pdf_dir=pdf_dir,
        audit_mode=audit_mode,
    )

    click.echo(
        f"Imported {result.added_refs} references and {result.added_docs} documents."
    )
    click.echo(f"Import run directory: {result.run_dir}")





# ========================================================================================


@entry.command(context_settings={"ignore_unknown_options": True})
# @click.argument("pattern", type=str, required=True)
@click.option(
    '-n',
    default=10,
    type=int,
    show_default=True,
    help='Number of results to return, default=10, n=-1 returns all.'
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def rg(args, n):
    """Run ripgrep (rg) with given pattern and args against text extracts from pdfs."""
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open...don't know where to look for text files. Returning")
        return
    # library runs the query, cli prints it out
    if not args:
        click.echo("Missing pattern!", err=True)
    pattern = args[0]
    args = args[1:]
    return_value, proc = lib.run_ripgrep(pattern, args)
    if return_value == 'FileNotFoundError':
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
        suffix = f'.{lib.extractor}.md'
    except AttributeError:
        print('ATTRIBUTE ERROR...here is dir lib')
        print(dir(lib))
        print('RETURNING')
        return
    last_file = ''
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
                    console.print('')  # between files
                    # new file
                    fc = 0
                    last_file = file
                styled = Text()
                if new_file:
                    file = file.replace(prefix, '').replace(suffix, '')
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
                styled.append('\n')
                console.print(styled, end='')
        except json.JSONDecodeError:
            console.print('ERROR ' + line.strip(), style="dim")

    return

# helpers =--------------------------
def get_available_libraries():
    """Return list of library directory names."""
    if not LIBRARIES_DIR.exists():
        return []
    return


# Uber using Gemini new technology Nov 2025
@entry.command()
@click.option("--debug", is_flag=True)
def uber(debug):
    """QT Standalone Shell."""
    shell = UberShell("archivum", debug)

    # figure completers
    completers = {}
    completers['open-library'] = DynamicCompleter(lambda: WordCompleter(Library.list()))

    # Register QT commands, exclude 'uber' to prevent recursion
    shell.register_click_group(entry, exclude=["uber"], completers=completers)

    def prompt_function():
        lib = LibraryContext.get()
        return HTML(f"<ansired>archivum <ansigreen>[{lib.name}]</ansigreen> > </ansired>")

    shell.start(prompt_function=prompt_function)


if __name__ == '__main__':
    # to facilitate performance logging
    # run python -m cProfile -o perf.prof -m archivum.cli
    # recent top 10 !/Boonen|Tsanakas|Wang, R/
    entry()
