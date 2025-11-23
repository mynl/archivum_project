# archivum/import_bibtex.py

"""
BibTeX import helpers for archivum.

This module reuses the Mendeley porting logic (Bib2df) to
incrementally import new references from a BibTeX file into an
existing Library.

Each import run is recorded under a timestamped directory so that
the original .bib and a copy of the PDFs are preserved and the
ETL is, in principle, replayable.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional

import datetime as dt
import logging
import shutil

import pandas as pd

from . import BASE_DIR, APP_NAME
from .mendeley_port import Bib2df

logger = logging.getLogger(__name__)


def _normalize_title(s: str) -> str:
    """Simple normalization for title comparison."""
    if not isinstance(s, str):
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _possible_duplicate(ref_row, existing_ref_df: pd.DataFrame) -> str | None:
    """
    Heuristic duplicate check: by DOI (if present) and by normalized title.
    Returns a short message if something looks like a duplicate.
    """
    doi = str(ref_row.get("doi", "") or "").strip().lower()
    title_norm = _normalize_title(ref_row.get("title", ""))

    # DOI check
    if doi and "doi" in existing_ref_df.columns:
        mask = existing_ref_df["doi"].astype(str).str.lower().str.strip() == doi
        if mask.any():
            tags = existing_ref_df.loc[mask, "tag"].tolist()
            return f"Possible duplicate by DOI; existing tag(s): {tags}"

    # Title check
    if title_norm:
        if "_title_norm" not in existing_ref_df.columns:
            existing_ref_df["_title_norm"] = existing_ref_df["title"].map(_normalize_title)
        mask = existing_ref_df["_title_norm"] == title_norm
        if mask.any():
            tags = existing_ref_df.loc[mask, "tag"].tolist()
            return f"Possible duplicate by title; existing tag(s): {tags}"

    return None


def _summarize_entry(label: str, row: pd.Series, drop_fields: set[str]) -> str:
    """
    Build a short, readable multi-line summary of a reference row,
    dropping very long or uninteresting fields.
    """
    keep = []
    for key, val in row.items():
        if key in drop_fields:
            continue
        if pd.isna(val):
            continue
        v = str(val).strip()
        if not v:
            continue
        keep.append(f"{key}: {v}")
    body = "\n  ".join(keep)
    return f"{label}:\n  {body}"


def _write_skipped_bibtex(
    skipped_rows: list[pd.Series],
    path: Path,
    drop_fields: set[str],
) -> None:
    """
    Write a minimal BibTeX file with the skipped entries.

    Uses the ported/raw-style rows (including 'type' and 'tag') and drops
    long or unwanted fields.
    """
    lines: list[str] = []
    for row in skipped_rows:
        entry_type = str(row.get("type", "article") or "article")
        tag = str(row.get("tag", "untagged") or "untagged")
        lines.append(f"@{entry_type}{{{tag},")
        for key, val in row.items():
            if key in drop_fields:
                continue
            if key in {"type", "tag"}:
                continue
            if pd.isna(val):
                continue
            v = str(val).strip()
            if not v:
                continue
            lines.append(f"  {key} = {{{v}}},")
        lines.append("}\n")

    path.write_text("\n".join(lines), encoding="utf-8")



@dataclass
class ImportResult:
    """
    Summary of a single BibTeX import run.
    """

    run_dir: Path
    added_refs: int
    added_docs: int


def _resolve_imports_root(imports_dir: Optional[Path]) -> Path:
    """
    Resolve the root directory under which import runs are recorded.

    If an explicit path is provided it is used as-is, otherwise a
    default under BASE_DIR is created.
    """
    if imports_dir is not None:
        root = imports_dir
    else:
        root = BASE_DIR / "imports"

    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root

def run_import(
    bibtex_path: Path,
    library,
    imports_dir: Optional[Path] = None,
    pdf_dir: Optional[Path] = None,
    audit_mode: bool = False,
) -> ImportResult:
    """
    Interactive import of references/documents from a BibTeX file into an existing Library.

    For each candidate reference:
      * display a "raw" ported view (no abstracts, etc.),
      * display the rationalized view (ref_df row),
      * flag possible duplicates,
      * prompt user to [a]ccept / [s]kip / [q]uit.

    Accepted items are appended to the library. Skipped items are written
    to a BibTeX file in the run directory for later manual editing.
    """
    bibtex_path = Path(bibtex_path).expanduser().resolve()
    print(f'{bibtex_path = }')
    imports_root = _resolve_imports_root(imports_dir)

    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = imports_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    # Keep the original .bib for replay/audit.
    bib_copy = run_dir / bibtex_path.name
    shutil.copy2(bibtex_path, bib_copy)

    # Where to look for PDFs referenced in this BibTeX file.
    if pdf_dir is None:
        pdf_dir = Path(library.config.pdf_dir_name)
    else:
        pdf_dir = Path(pdf_dir).expanduser().resolve()

    logger.info("Importing BibTeX from %s", bibtex_path)
    logger.info("PDF source directory: %s", pdf_dir)
    logger.info("Import run directory: %s", run_dir)

    bib = Bib2df(p=bibtex_path, pdf_dir=pdf_dir, fillna=True, audit_mode=audit_mode)

    # "Raw/ported" view (includes 'file'); cleaned reference view (no file).
    raw_df = bib.ported_df.copy()
    ref_df = bib.ref_df.copy()
    doc_df = bib.doc_df.copy()
    ref_doc_df = bib.ref_doc_df.copy()

    existing_ref_df = library.ref_df.copy()
    existing_doc_df = library.doc_df.copy()
    existing_ref_doc_df = library.ref_doc_df.copy()

    # We treat "new candidate" as "tag not in existing ref_df".
    existing_tags = set(existing_ref_df["tag"])
    candidate_mask = ~ref_df["tag"].isin(existing_tags)
    candidate_ref_df = ref_df[candidate_mask].reset_index(drop=True)

    # Align raw_df by tag so we can show "raw" vs "rationalized".
    raw_by_tag = raw_df.set_index("tag")

    # Which fields to suppress in summaries and in the skipped BibTeX.
    drop_fields = set(Bib2df.omitted_menedely_fields) | {
        "abstract",
        "keywords",
        "file",
        "arc-source",
        "_title_norm",
    }

    accepted_tags: list[str] = []
    skipped_raw_rows: list[pd.Series] = []

    for _, ref_row in candidate_ref_df.iterrows():
        tag = ref_row["tag"]
        print("\n" + "=" * 80)
        print(f"Candidate tag: {tag}")

        # Raw/ported view.
        try:
            raw_row = raw_by_tag.loc[tag]
        except KeyError:
            raw_row = ref_row  # fallback; should be rare

        print(_summarize_entry("Raw (ported) entry", raw_row, drop_fields))

        # Rationalized/ref_df view.
        print()
        print(_summarize_entry("Rationalized entry", ref_row, drop_fields))

        dup_msg = _possible_duplicate(ref_row, existing_ref_df)
        if dup_msg:
            print()
            print(f"WARNING: {dup_msg}")

        # Simple prompt loop.
        while True:
            ans = input("[a]ccept / [s]kip / [q]uit? [a/s/q]: ").strip().lower()
            if ans in {"", "a", "s", "q"}:
                break
            print("Please enter 'a', 's', or 'q'.")

        if ans in {"", "a"}:
            accepted_tags.append(tag)
        elif ans == "s":
            skipped_raw_rows.append(raw_row)
        elif ans == "q":
            skipped_raw_rows.append(raw_row)
            # break out of the outer loop
            break

    # If nothing accepted, just write skipped bibtex (if any) and return.
    if not accepted_tags:
        if skipped_raw_rows:
            pip_path = run_dir / "bibtex-pip.bib"
            _write_skipped_bibtex(skipped_raw_rows, pip_path, drop_fields)
            logger.info("No references accepted; wrote skipped entries to %s", pip_path)
        return ImportResult(run_dir=run_dir, added_refs=0, added_docs=0)

    accepted_tags_set = set(accepted_tags)

    # Build ref/doc/ref_doc additions based on accepted tags.
    ref_add = ref_df[ref_df["tag"].isin(accepted_tags_set)].copy()

    # Restrict ref_doc and doc_df to the accepted refs.
    rd_candidates = ref_doc_df[ref_doc_df["tag"].isin(accepted_tags_set)].copy()
    new_paths = set(rd_candidates["path"])
    doc_add_candidates = doc_df[doc_df["path"].isin(new_paths)].copy()

    # Drop any docs we already know about.
    existing_paths = set(existing_doc_df["path"])
    doc_add = doc_add_candidates[~doc_add_candidates["path"].isin(existing_paths)].copy()

    # And restrict ref_doc to only the retained docs.
    kept_paths = set(doc_add["path"])
    rd_add = rd_candidates[rd_candidates["path"].isin(kept_paths)].copy()

    # Append to existing dataframes.
    ref_out = pd.concat([existing_ref_df, ref_add], ignore_index=True)
    doc_out = pd.concat([existing_doc_df, doc_add], ignore_index=True)
    ref_doc_out = pd.concat([existing_ref_doc_df, rd_add], ignore_index=True)

    ref_path = library.config_path.with_suffix(f".{APP_NAME}-ref-feather")
    doc_path = library.config_path.with_suffix(f".{APP_NAME}-doc-feather")
    ref_doc_path = library.config_path.with_suffix(f".{APP_NAME}-ref-doc-feather")

    # persist TODO
    # ref_out.to_feather(ref_path)
    # doc_out.to_feather(doc_path)
    # ref_doc_out.to_feather(ref_doc_path)

    # Update in-memory library.
    library._ref_df = ref_out
    library._doc_df = doc_out
    library._ref_doc_df = ref_doc_out

    # Write skipped entries (if any) to a "pip" BibTeX file.
    if skipped_raw_rows:
        pip_path = run_dir / "bibtex-pip.bib"
        _write_skipped_bibtex(skipped_raw_rows, pip_path, drop_fields)
        logger.info("Wrote %d skipped entries to %s", len(skipped_raw_rows), pip_path)

    return ImportResult(
        run_dir=run_dir,
        added_refs=len(ref_add),
        added_docs=len(doc_add),
    )
