"""
Changeset files: the record of one commit of field changes to ``ref.feather``.

A changeset is one JSON file in ``<library>/doi-audit/changesets/``. It is
written with the outcome ``pending`` before the library changes, and rewritten
with the result afterwards, so every commit leaves a file whatever happens.
This module holds the rules and the file handling; the write to the library is
``Library.apply_field_changes``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from . import AUDIT_DIR_NAME

# The only fields the commit path may write. tag, author and year are absent
# by design: nothing in the DOI audit changes them.
WRITABLE_FIELDS = ("doi", "type", "journal", "volume", "number", "pages")

PENDING, APPLIED, SKIPPED, FAILED = "pending", "applied", "skipped", "failed"
CHANGED_SINCE_REVIEW = "changed since review"


def cell_value(value: Any) -> str | None:
    """
    The form in which a ``ref.feather`` cell is compared and recorded.

    Parameters
    ----------
    value : Any
        A cell value, or a value proposed for a cell.

    Returns
    -------
    str or None
        ``None`` for a missing value (``None``, ``NaN``, ``pd.NA``), otherwise
        the text. An empty string stays an empty string: the library holds both
        empty and missing cells, and a revert restores whichever was there.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def normalize_changes(changes: Iterable) -> list[dict]:
    """
    Put proposed changes into one form.

    Parameters
    ----------
    changes : iterable
        Each item is ``(tag, field, old, new)`` or a mapping with those keys
        and an optional ``evidence``.

    Returns
    -------
    list of dict
        One row per change with ``tag``, ``field``, ``old``, ``new``,
        ``evidence``, and ``outcome`` set to ``pending``.
    """
    rows = []
    for change in changes:
        if isinstance(change, dict):
            tag, field, old, new = (change[k] for k in ("tag", "field", "old", "new"))
            evidence = change.get("evidence")
        else:
            tag, field, old, new = change
            evidence = None
        rows.append({
            "tag": str(tag), "field": str(field), "old": cell_value(old), "new": cell_value(new),
            "evidence": evidence, "outcome": PENDING, "note": "",
        })
    return rows


def validate_changes(rows: list[dict], ref_df: pd.DataFrame, allowed_types: Iterable[str],
                     allow_book_change: bool = False) -> None:
    """
    Refuse a batch that the commit path must never apply.

    Parameters
    ----------
    rows : list of dict
        Rows from :func:`normalize_changes`.
    ref_df : pandas.DataFrame
        The library's references.
    allowed_types : iterable of str
        The entry types BibTeX output accepts.
    allow_book_change : bool, optional
        True only for a revert, which must be able to restore a type.

    Raises
    ------
    ValueError
        Naming every refused row. One bad row refuses the whole batch, before
        anything is written.

    Notes
    -----
    Refused: a field outside :data:`WRITABLE_FIELDS` (so ``tag``, ``author``
    and ``year``), a field that is not a column of ``ref.feather``, an unknown
    tag, a tag held by more than one row, the same cell listed twice, a row
    that changes nothing, an entry type BibTeX does not accept, and any change
    to the type of a reference that is a ``book``.
    """
    allowed_types = set(allowed_types)
    tag_counts = ref_df["tag"].value_counts() if "tag" in ref_df.columns else {}
    types = dict(zip(ref_df["tag"], ref_df["type"])) if "type" in ref_df.columns else {}
    problems, seen = [], set()
    for row in rows:
        tag, field = row["tag"], row["field"]
        where = f"{tag}.{field}"
        if field not in WRITABLE_FIELDS:
            problems.append(f"{where}: the field is not one the commit path writes")
            continue
        if field not in ref_df.columns:
            problems.append(f"{where}: the library has no such column")
            continue
        count = int(tag_counts.get(tag, 0))
        if count != 1:
            problems.append(f"{where}: the tag is on {count} references")
            continue
        if (tag, field) in seen:
            problems.append(f"{where}: listed more than once")
        seen.add((tag, field))
        if row["old"] == row["new"]:
            problems.append(f"{where}: old and new values are the same")
        if field == "type":
            if str(row["new"] or "").lower() not in allowed_types:
                problems.append(f"{where}: {row['new']!r} is not an entry type")
            is_book = str(types.get(tag, "")).lower() == "book" or str(row["old"] or "").lower() == "book"
            if is_book and not allow_book_change:
                problems.append(f"{where}: the type of a book is never changed")
    if problems:
        raise ValueError("Changes refused: " + "; ".join(problems))


def changeset_dir(library_path: Path) -> Path:
    """Return ``<library>/doi-audit/changesets``, without creating it."""
    return Path(library_path) / AUDIT_DIR_NAME / "changesets"


def new_changeset(library_path: Path, name: str, rows: list[dict], reverts: str = "") -> tuple[Path, dict]:
    """
    Build a pending changeset and write its file.

    Parameters
    ----------
    library_path : Path
        The library folder.
    name : str
        A short label, used in the file name.
    rows : list of dict
        Rows from :func:`normalize_changes`.
    reverts : str, optional
        The file name of the changeset this one reverts.

    Returns
    -------
    tuple of (Path, dict)
        The file and its content.
    """
    folder = changeset_dir(library_path)
    folder.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "changes"
    stem = f"{now.astimezone().strftime('%Y%m%d_%H%M%S')}_{slug}"
    path, n = folder / f"{stem}.json", 1
    while path.exists():
        n += 1
        path = folder / f"{stem}-{n}.json"
    changeset = {
        "name": name, "created": now.isoformat(timespec="seconds"), "outcome": PENDING,
        "error": "", "reverts": reverts, "rows": rows,
    }
    write_changeset(path, changeset)
    return path, changeset


def write_changeset(path: Path, changeset: dict) -> None:
    """Write a changeset whole: to a temporary file, then moved into place."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(changeset, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_changeset(path: Path) -> dict:
    """Return the content of a changeset file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_changesets(library_path: Path) -> list[Path]:
    """Return the library's changeset files, newest first."""
    folder = changeset_dir(library_path)
    return sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []


def find_changeset(library_path: Path, name: str) -> Path:
    """
    Find a changeset from its file name, with or without ``.json``.

    Raises
    ------
    FileNotFoundError
        If the library has no such changeset.
    """
    stem = Path(name).name.removesuffix(".json")
    path = changeset_dir(library_path) / f"{stem}.json"
    if not path.exists():
        raise FileNotFoundError(f"No changeset {stem!r} in {changeset_dir(library_path)}")
    return path


def inverse_changes(changeset: dict) -> list[dict]:
    """
    The changes that undo a changeset.

    Parameters
    ----------
    changeset : dict
        Content of a changeset file.

    Returns
    -------
    list of dict
        One change per applied row, with old and new exchanged. Skipped rows
        changed nothing and have no inverse.
    """
    return [
        {"tag": row["tag"], "field": row["field"], "old": row["new"], "new": row["old"],
         "evidence": {"reverts": row.get("evidence")} if row.get("evidence") else None}
        for row in changeset["rows"] if row["outcome"] == APPLIED
    ]
