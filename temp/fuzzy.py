"""Test out different fuzzy matching options."""
import subprocess
import shutil
import sys

import click
from prompt_toolkit import prompt
from prompt_toolkit.completion import FuzzyWordCompleter

import pandas as pd


try:
    from InquirerLib.prompts.fuzzy import FuzzyPrompt
except ImportError:
    FuzzyPrompt = None

df = pd.read_csv('dm.csv')

AUTHORS = sorted(list(df.revised.value_counts().keys()))

def fzf_select(options):
    if shutil.which('fzf') is None:
        click.echo("fzf not found, skipping...")
        return None

    try:
        result = subprocess.run(
            ['fzf', '--prompt=Author: '],
            input='\n'.join(options).encode(),
            stdout=subprocess.PIPE
        )
        selected = result.stdout.decode().strip()
        if selected and selected not in options:
            if click.confirm(f"Add '{selected}' as new author?", default=True):
                return selected
            else:
                return fzf_select(options)
        return selected
    except Exception as e:
        click.echo(f"fzf failed: {e}")
        return None

def inquirerlib_select(options):
    if FuzzyPrompt is None:
        click.echo("InquirerLib not installed, skipping...")
        return None

    prompt_instance = FuzzyPrompt(
        message="Author (InquirerLib):",
        choices=options,
        instruction="(type to filter or enter new)",
        validate=lambda val: True
    )
    result = prompt_instance.execute()

    if result not in options:
        if click.confirm(f"Add '{result}' as new author?", default=True):
            return result
        else:
            return inquirerlib_select(options)
    return result

def prompt_toolkit_select(options):
    completer = FuzzyWordCompleter(options, WORD=True)
    result = prompt("Author (prompt_toolkit): ", completer=completer)

    if result not in options:
        if click.confirm(f"Add '{result}' as new author?", default=True):
            return result
        else:
            return prompt_toolkit_select(options)
    return result

@click.command()
def main():
    click.echo("=== FZF ===")
    author1 = fzf_select(AUTHORS)
    click.echo(f"Selected: {author1}\n")

    click.echo("=== InquirerLib ===")
    author2 = inquirerlib_select(AUTHORS)
    click.echo(f"Selected: {author2}\n")

    click.echo("=== prompt_toolkit ===")
    author3 = prompt_toolkit_select(AUTHORS)
    click.echo(f"Selected: {author3}")

if __name__ == '__main__':
    main()
