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
from . import DEFAULT_CONFIG_FILE, BASE_DIR, APP_SUFFIX, BIBTEX_DIR, APP_NAME
# from . manager import ProjectManager


# custom greater-tables formatter
fGT = partial(GT,
              show_index=False,
              large_ok=True,
              formatters={'year': str, 'index': str},
              aligners={'suffix': 'center'})


# @click.group()
# def main():
#     """File database CLI."""
#     pass


# @main.command()
# @click.option('-c', '--config', type=click.Path(exists=False, dir_okay=False, path_type=Path), default=DEFAULT_CONFIG_FILE, help='YAML config path')
# def index(config: Path):
#     """Run the indexer and write Feather file."""
#     pm = ProjectManager(config)
#     pm.index(config)
#     click.echo(f"Index update completed.")


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

    config={
        "library": libname,
        "description": click.prompt('Description'),
        "database": lib_path.with_suffix(f'.{APP_NAME}-feather'),
        "bibtex_file": click.prompt('BibTeX File', default=f'{BIBTEX_DIR}{libname}.bib'),
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

if __name__ == '__main__':
    entry()
