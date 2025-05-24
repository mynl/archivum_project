"""Implement command line interface for archivum."""

from functools import partial
import os
from pathlib import Path
import socket
import yaml

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import FuzzyCompleter, WordCompleter
from prompt_toolkit.formatted_text import HTML

from greater_tables import GT

from . reference import Reference
from . library import Library
from . import DEFAULT_CONFIG_FILE, BASE_DIR, APP_SUFFIX, APP_NAME
from . utilities import df_to_str


@click.group()
def entry():
    """CLI for managing bibliographic entries."""
    pass


@entry.command()
@click.option('-d', '--directory', default='~/Downloads', type=click.Path(exists=True), help='Directory to scan for PDFs')
def new(directory):
    """List PDFs and show basic metadata."""
    pdfs = find_pdfs(directory)
    for i, pdf in enumerate(pdfs):
        meta = extract_metadata(pdf)
        click.echo(f"{i}: {pdf.name}")
        if meta:
            click.echo(f"    Title: {meta.get('title', 'Unknown')}")
            click.echo(f"    Author: {meta.get('author', 'Unknown')}")


@entry.command()
@click.option('-p', '--partial', required=True, help='Comma-separated list of PDF numbers to upload')
def upload(partial):
    """Interactively create reference object."""
    indices = [int(i.strip()) for i in partial.split(',')]
    pdfs = find_pdfs()  # reuse previous list or cache it
    for i in indices:
        pdf_path = pdfs[i]
        ref = Reference.from_pdf(pdf_path)
        prompt_for_fields(ref)  # interactively fill in fields
        click.echo(ref.to_dict())  # or save it, display BibTeX, etc.


@entry.command()
@click.argument('libname', type=str)
def create_library(libname):
    """Interactively create a YAML config file for a new library called libname."""

    # sort the file out
    lib_path = BASE_DIR / f'{libname}{APP_SUFFIX}'
    click.secho("=== Library Config Creator ===", fg='cyan')
    click.secho(f'Creating Library {libname} at {lib_path}')

    config = {
        "library": libname,
        "description": click.prompt('Description'),
        "database": str(lib_path.with_suffix(f'.{APP_NAME}-feather')),
        "columns": ['type', 'tag', 'author', 'doi', 'file', 'journal', 'pages', 'title',
                    'volume', 'year', 'publisher', 'url', 'institution', 'number',
                    'mendeley-tags', 'booktitle', 'edition', 'month', 'address', 'editor',
                    'arc-citations'],
        "bibtex_file": click.prompt('BibTeX File', default=f'{libname}-test.bib'),
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


# todo add a default config
@ entry.command()
@ click.option('-c', '--config', type=str, help='Library config path')
@ click.option('-t', '--tablefmt', default='', show_default=True, help='Markdown table format (see tabulate docs); default uses config file value.')
def query_repl(config: Path, tablefmt: str):
    """Interactive REPL to run multiple queries on the file index with fuzzy completion."""
    lib = Library(config)
    print(lib)
    if tablefmt == '':
        tablefmt = lib.tablefmt
    click.echo(f"Loaded {len(lib.database):,} rows from {lib.name}")
    click.echo(
        "Enter pandas query expressions (type 'exit', 'x', 'quit' or 'q' to stop and ? for help).\n")

    keywords = ['cls', 'and', 'or'] + list(lib.database.columns)
    word_completer = FuzzyCompleter(WordCompleter(keywords, sentence=True))
    session = PromptSession(completer=word_completer)
    result = None

    while True:
        try:
            expr = session.prompt(HTML('<ansiyellow>>> </ansiyellow>')).strip()
            pipe = False
            if expr.lower() in {"exit", "x", "quit", "q"}:
                break
            elif expr == "?":
                click.echo(lib.query_help())
                click.echo(repl_help())
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
                    print('No existing query! Run query first')
                    continue
                # open file mode, start with o n
                try:
                    expr = int(expr[1:].strip())
                except ValueError:
                    print('Wrong syntax for open, expect o index number')
                try:
                    fname = result.loc[expr, 'file']
                    print('TODO...open ', file)
                    # os.startfile(fname)
                except KeyError:
                    print(f'Key {expr} not found.')
                except FileNotFoundError:
                    print("File does not exist.")
                except OSError as e:
                    print(f"No association or error launching: {e}")
                continue

            # if here, run query work
            result = lib.querex(expr)
            click.echo(df_to_str(result, tablefmt=tablefmt))
            click.echo(
                f'{lib._last_unrestricted:,d} unrestricted results, {len(result)} shown.')
            if pipe:
                click.echo(
                    f'Found pipe clause {pipe = } TODO: deal with this!')
        except Exception as e:
            click.echo(f"[Error] {e}")


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

