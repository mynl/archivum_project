"""
The append-only JSONL files in ``<library>/doi-audit/``.

One line is one record. An append is a single write, so a crash loses at most
the line being written, and a file is readable in any editor.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path

from . import AUDIT_DIR_NAME

logger = logging.getLogger(__name__)

DECISIONS = "decisions.jsonl"
ACCEPTED, REJECTED = "accepted", "rejected"

# waitress serves requests on several threads
_LOCK = threading.Lock()


def audit_dir(library_path: Path) -> Path:
    """Return ``<library>/doi-audit``, without creating it."""
    return Path(library_path) / AUDIT_DIR_NAME


def now_utc() -> str:
    """The current time as an ISO string in UTC, to the second."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_lines(library_path: Path, file_name: str, records: list[dict]) -> None:
    """
    Append records to a journal file, creating the folder on first use.

    Parameters
    ----------
    library_path : Path
        The library folder.
    file_name : str
        The journal file, such as :data:`DECISIONS`.
    records : list of dict
        One record per line. A record with no ``at`` is stamped with the time.
    """
    if not records:
        return
    folder = audit_dir(library_path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = now_utc()
    text = "".join(json.dumps({"at": stamp, **record}, ensure_ascii=False) + "\n" for record in records)
    with _LOCK, open(folder / file_name, "a", encoding="utf-8") as f:
        f.write(text)


def read_lines(library_path: Path, file_name: str) -> list[dict]:
    """
    Return the records of a journal file, oldest first.

    A missing file is an empty journal. A line that does not parse, such as
    one cut short by a crash, is skipped and logged.
    """
    path = audit_dir(library_path) / file_name
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipped line %s of %s: not JSON", number, path)
    return records


def rejected_proposals(library_path: Path) -> set[tuple]:
    """
    The proposals the user has rejected.

    Returns
    -------
    set of tuple
        ``(tag, field, old, proposed)`` for each rejection. A proposal is
        hidden only while the cell still holds ``old`` and the proposal is
        still ``proposed``.
    """
    return {
        (r.get("tag"), r.get("field"), r.get("old"), r.get("proposed"))
        for r in read_lines(library_path, DECISIONS) if r.get("decision") == REJECTED
    }
