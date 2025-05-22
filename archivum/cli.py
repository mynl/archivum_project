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
from . import DEFAULT_CONFIG_FILE, BASE_DIR
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

if __name__ == '__main__':
    entry()
