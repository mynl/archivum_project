"""
ripgrep | fzf | open script for text file extract from pdfs.

GPT code.
"""

import subprocess
import os
from pathlib import Path


# === CONFIG ===
TEXT_DIR = Path("C:\\temp\\pdf-full-text")
RIPGREP_EXE = "rg"
FZF_EXE = "fzf"
SUBLIME_EXE = "c:\\program files\\sublime text\\subl.exe"


# === Transform .md → .pdf or similar ===
def transform_path(md_path: Path) -> Path:
    """Transform .md file path to original PDF path."""
    return Path('c:\\') / md_path.with_name('.'.join(md_path.stem.split('.')[:-1]) + '.pdf').relative_to(TEXT_DIR)


# === Main logic ===
def main():
    pattern = input("Enter regex: ").strip()
    if not pattern:
        print("No pattern entered.")
        return

    # Run ripgrep to get matching filenames
    rg_cmd = [RIPGREP_EXE, "-l", pattern, str(TEXT_DIR)]
    try:
        rg_proc = subprocess.run(rg_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("ripgrep failed:", e.stderr.strip())
        return

    files = rg_proc.stdout.strip().splitlines()
    print(f"ripgrep found {len(files)} matches")

    if not files:
        print("No matches.")
        return

    # Send list of files to fzf
    fzf_proc = subprocess.run(
        [FZF_EXE],
        input=rg_proc.stdout,
        text=True,
        capture_output=True
    )

    selected = fzf_proc.stdout.strip()
    if not selected:
        print("No file selected.")
        return

    # Convert to corresponding PDF path
    selected_path = Path(selected)
    pdf_path = transform_path(selected_path)

    print('***\n'*2)
    print(f'Selected {selected_path}')
    print(f'Opening  {pdf_path}')
    print('***\n'*2)

    # Open in default
    if pdf_path.exists():
        try:
            os.startfile(pdf_path)

        except Exception as e:
            print(f'ERROR: {e}')
    else:
        print(f"PDF not found: {pdf_path}")


if __name__ == "__main__":
    main()
