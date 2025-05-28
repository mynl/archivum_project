"""
ripgrep | fzf | open script for text file extract from pdfs.

GPT code.

"""
import subprocess
import os
from pathlib import Path

import click

# === CONFIG ===
TEXT_DIR = Path("C:/temp/pdf-full-text")
RIPGREP_EXE = "rg"
FZF_EXE = "fzf"

# === Transform .md → .pdf ===


def transform_path(md_path: Path) -> Path:
    return (
        Path('C:/')
        / md_path.with_name(
            '.'.join(md_path.stem.split('.')[:-1]) + '.pdf'
        )
        .relative_to(TEXT_DIR)
    )

# === CLI ===


@click.command()
@click.argument("pattern")
def search(pattern):
    """Search for PATTERN in extracted text files and open matching PDFs."""

    rg_cmd = [RIPGREP_EXE, "-l", "--stats", pattern, str(TEXT_DIR)]
    try:
        rg_proc = subprocess.run(rg_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        click.echo("ripgrep failed:", err=True)
        click.echo(e.stderr.strip(), err=True)
        return

    files = rg_proc.stdout.strip().splitlines()
    if not files:
        click.echo("No matches found.")
        return

    stats = '\n'.join(files[-8:])
    files = files[:-9]

    click.echo(f"ripgrep found {len(files)} matches")

    try:
        fzf_proc = subprocess.run(
            [FZF_EXE, "--multi"],
            input=rg_proc.stdout,
            text=True,
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        click.echo("fzf failed:", err=True)
        return

    selected = fzf_proc.stdout.strip().splitlines()
    if not selected:
        click.echo("No files selected.")
        return

    for sel in selected:
        selected_path = Path(sel)
        pdf_path = transform_path(selected_path)

        click.echo("\n***")
        click.echo(f"Selected {selected_path}")
        click.echo(f"Opening  {pdf_path}")
        click.echo("***\n")

        if pdf_path.exists():
            try:
                os.startfile(pdf_path)
            except Exception as e:
                click.echo(f"ERROR: could not open {pdf_path}: {e}", err=True)
        else:
            click.echo(f"PDF not found: {pdf_path}", err=True)

    # Show rg stats
    click.echo("\n=== ripgrep stats ===")
    click.echo(stats)


if __name__ == "__main__":
    search()
